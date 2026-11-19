"""Local NekoConne asset browser. Run with: python viewer_gui.py"""

import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import viewer_core as core
from viewer_runtime import APP_HOME, FROZEN, command

ROOT = Path(__file__).resolve().parent
try:
    from PIL import Image, ImageTk
except ImportError:
    sys.path.insert(0, str(ROOT / ".tools" / "unity"))
    try:
        from PIL import Image, ImageTk
    except ImportError:
        Image = ImageTk = None

JOBS = {"builtin": "精选图片 / Spine", "cubism": "Live2D", "audio": "语音", "video": "视频", "images": "全部内置图片"}
OPENABLE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".wav", ".ogg", ".flac", ".mp3", ".m4a",
            ".mp4", ".webm", ".mkv", ".mov", ".html", ".htm"}


def open_local(path):
    path = Path(path).resolve(strict=True)
    if path.is_file() and path.suffix.lower() not in OPENABLE:
        path = path.parent
    if sys.platform == "win32":
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


class Viewer:
    page_size = 250

    def __init__(self, root):
        self.root = root
        root.title("NekoConne · 本地素材查看器")
        root.geometry("1080x780")
        root.minsize(880, 650)
        self.game = tk.StringVar(value=str(core.detect_game() or ""))
        self.cache = tk.StringVar(value=str(Path(os.environ.get("NEKOCONNE_CACHE", APP_HOME / "exports")).absolute()))
        self.query, self.category = tk.StringVar(), tk.StringVar(value="全部")
        self.status, self.details, self.page_label = (tk.StringVar() for _ in range(3))
        self.flags = {key: tk.BooleanVar(value=key in ("builtin", "cubism")) for key in JOBS}
        self.assets, self.matches, self.photo = [], [], None
        self.motion_asset, self.motion_map = None, {}
        self.spine_lookup = None
        self.page = 0
        self.busy = self.closing = False
        self.events = Queue()
        self.worker = None
        self.process = None
        self.cancel = threading.Event()
        self.filter_timer = self.resize_timer = None

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="NekoConne   /   本地素材库", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        paths = ttk.Frame(frame, padding=(0, 8))
        paths.pack(fill="x")
        paths.columnconfigure(1, weight=1)
        self.path_controls = []
        for row, (name, var) in enumerate((("游戏安装目录", self.game), ("本地导出缓存", self.cache))):
            ttk.Label(paths, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12))
            entry = ttk.Entry(paths, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            button = ttk.Button(paths, text="选择…", command=lambda v=var: self.browse(v))
            button.grid(row=row, column=2, padx=(6, 0))
            self.path_controls.extend([entry, button])
        self.import_button = ttk.Button(paths, text="导入外部素材目录…", command=self.import_external)
        self.import_button.grid(row=1, column=3, padx=(6, 0))
        self.path_controls.append(self.import_button)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(0, 8))
        for key, name in JOBS.items():
            ttk.Checkbutton(bar, text=name, variable=self.flags[key]).pack(side="left", padx=(0, 8))
        self.run_button = ttk.Button(bar, text="运行所选任务", command=self.run_jobs)
        self.run_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.scan_button = ttk.Button(bar, text="刷新素材库", command=self.refresh)
        self.scan_button.pack(side="right")

        self.tabs = ttk.Notebook(frame)
        self.tabs.pack(fill="both", expand=True)
        library = ttk.Frame(self.tabs, padding=9)
        logs = ttk.Frame(self.tabs, padding=6)
        self.tabs.add(library, text=" 素材库 ")
        self.tabs.add(logs, text=" 任务日志 ")
        filters = ttk.Frame(library)
        filters.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="查找").pack(side="left")
        ttk.Entry(filters, textvariable=self.query).pack(side="left", fill="x", expand=True, padx=8)
        self.categories = ttk.Combobox(filters, textvariable=self.category, state="readonly", width=19, values=["全部"])
        self.categories.pack(side="right")
        self.query.trace_add("write", self.schedule_filter)
        self.category.trace_add("write", self.schedule_filter)

        panes = ttk.Panedwindow(library, orient="horizontal")
        panes.pack(fill="both", expand=True)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=3)
        panes.add(right, weight=2)
        footer = ttk.Frame(left)
        footer.pack(side="bottom", fill="x", pady=(5, 0))
        ttk.Button(footer, text="上一页", command=lambda: self.change_page(-1)).pack(side="left")
        ttk.Button(footer, text="下一页", command=lambda: self.change_page(1)).pack(side="right")
        ttk.Label(footer, textvariable=self.page_label).pack()
        self.tree = ttk.Treeview(left, columns=("kind", "title"), show="headings", selectmode="browse")
        self.tree.heading("kind", text="类型")
        self.tree.heading("title", text="素材名称")
        self.tree.column("kind", width=90, stretch=False)
        self.tree.column("title", width=260)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_preview())
        self.tree.bind("<Double-Button-1>", lambda _: self.action("open"))
        actions = ttk.Frame(right)
        actions.pack(side="bottom", fill="x", pady=(6, 0))
        for label, action in (("打开", "open"), ("导出素材…", "copy"), ("生成壁纸…", "wallpaper")):
            ttk.Button(actions, text=label, command=lambda a=action: self.action(a)).pack(side="left", padx=(0, 4))
        motion_bar = ttk.Frame(right)
        motion_bar.pack(side="bottom", fill="x", pady=4)
        self.motion_choice = ttk.Combobox(motion_bar, state="disabled", width=16)
        self.motion_choice.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.motion_button = ttk.Button(motion_bar, text="渲染动作", command=self.render_motion, state="disabled")
        self.motion_button.pack(side="right")
        self.info = ttk.Label(right, textvariable=self.details, wraplength=290, justify="left")
        self.info.pack(side="bottom", fill="x", pady=6)
        self.canvas = tk.Canvas(right, bg="#e9e4dd", highlightthickness=0, width=290, height=260)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        self.log = tk.Text(logs, wrap="word", state="disabled", bg="#f8f7f3", fg="#27251f", font=("Consolas", 10))
        sb = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=(7, 0))
        self.cache.trace_add("write", self.cache_changed)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Destroy>", self.on_destroy, add="+")
        self.poll_timer = root.after(70, self.poll)
        self.refresh()

    def browse(self, var):
        current = Path(var.get()) if var.get() else ROOT
        value = filedialog.askdirectory(parent=self.root, initialdir=current if current.is_dir() else ROOT)
        if value:
            var.set(value)
            if var is self.cache:
                self.refresh()

    def cache_changed(self, *_):
        self.set_library([])
        self.status.set("缓存路径已改变。请刷新素材库。")

    def set_busy(self, busy, can_stop=False):
        self.busy = busy
        for w in [self.run_button, self.scan_button, *self.path_controls]:
            w.state(["disabled" if busy else "!disabled"])
        self.stop_button.state(["!disabled" if can_stop else "disabled"])

    def launch(self, fn, can_stop=False):
        if self.busy:
            return
        self.cancel.clear()
        self.set_busy(True, can_stop)

        def work():
            try:
                fn()
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("done", None))
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def refresh(self):
        if self.busy or not self.cache.get().strip():
            return
        cache = Path(self.cache.get()).expanduser()
        self.status.set("正在读取素材索引…")

        def task():
            assets = list(core.library(cache)) if cache.is_dir() else []
            self.events.put(("library", assets))
            self.events.put(("status", f"已索引 {len(assets):,} 项。没有素材时，请先选择游戏目录并运行任务。"))
        self.launch(task)

    def run_jobs(self):
        if self.busy:
            return
        game, cache = Path(self.game.get()).expanduser(), Path(self.cache.get()).expanduser()
        keys = [k for k in JOBS if self.flags[k].get()]
        if not self.game.get().strip() or not game.is_dir() or not self.cache.get().strip() or not keys:
            messagebox.showinfo("运行任务", "请选择游戏目录、缓存目录和至少一个解码任务。", parent=self.root)
            return
        self.tabs.select(1)
        self.status.set("正在运行，详情见任务日志。")

        def task():
            cmds = core.jobs(game.resolve(), cache.resolve())
            self.commands(cache, [(JOBS[k], cmds[k]) for k in keys])
        self.launch(task, True)

    def import_external(self):
        if self.busy or not self.cache.get().strip():
            return
        directory = filedialog.askdirectory(parent=self.root, title="选择已有的 UnityFS / DDS 素材目录")
        if not directory:
            return
        cache, source = Path(self.cache.get()).expanduser().resolve(), Path(directory).resolve()
        if source.is_relative_to(cache) or cache.is_relative_to(source):
            messagebox.showerror("目录重叠", "请把导入来源和素材缓存放在互不包含的目录。", parent=self.root)
            return
        cmd = command("import_bundles.py", source, "--out", cache / "external")
        if self.game.get().strip():
            game = Path(self.game.get()).expanduser().resolve()
            if cache.is_relative_to(game):
                messagebox.showerror("缓存位置", "不能写入游戏安装目录。", parent=self.root)
                return
            cmd += ["--skip-bundle", str(game / "NekoConne_Data/data.unity3d")]
        self.tabs.select(1)
        self.launch(lambda: self.commands(cache, [("外部素材导入", cmd)]), True)

    def commands(self, cache, commands):
        results = []
        for name, cmd in commands:
            if self.cancel.is_set():
                break
            self.events.put(("log", f"\n[{name}] 开始\n"))
            p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                 env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
            self.process = p
            if self.cancel.is_set():
                self.terminate()
            with p.stdout:
                for line in p.stdout:
                    self.events.put(("log", line))
            code = p.wait()
            self.process = None
            results.append(f"{name}: {code}")
            self.events.put(("log", f"[{name}] 退出码 {code}\n"))
        self.events.put(("library", list(core.library(cache)) if cache.is_dir() else []))
        self.events.put(("status", ("已停止。 " if self.cancel.is_set() else "任务退出码：") + "；".join(results)))

    def render_motion(self):
        asset, name = self.selected(), self.motion_choice.get()
        if self.busy or asset is None or asset.kind not in core.MODEL_KINDS or name not in self.motion_map:
            return
        cache = Path(self.cache.get()).expanduser().resolve()
        group, index = self.motion_map[name]
        self.tabs.select(1)
        self.status.set(f"正在渲染 {name}…")
        game = self.game.get().strip()
        def render():
            cmd = core.render_command(asset, cache, group, index)
            if FROZEN and asset.kind == "cubism" and game:
                cmd.extend(["--game", game])
            self.commands(cache, [(name, cmd)])
        self.launch(render, True)

    def terminate(self):
        p = self.process
        if p is not None and p.poll() is None:
            if sys.platform == "win32" and shutil.which("taskkill"):
                subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                p.terminate()

    def stop(self):
        self.cancel.set()
        self.stop_button.state(["disabled"])
        self.status.set("正在停止当前任务…")
        threading.Thread(target=self.terminate, daemon=True).start()

    def poll(self):
        for _ in range(150):
            try:
                what, data = self.events.get_nowait()
            except Empty:
                break
            if what == "done":
                self.set_busy(False)
                if self.closing:
                    self.root.destroy()
                    return
            elif what == "library":
                self.set_library(data)
            elif what == "spine":
                asset, choices, error = data
                self.spine_lookup = None
                if self.motion_asset == asset:
                    self.set_motions(choices)
                    self.status.set(f"Spine：{error}" if error else f"Spine 动作索引已就绪：{len(choices)} 个选择。")
                    if error:
                        self.events.put(("log", f"Spine：{error}\n"))
                elif self.motion_asset and self.motion_asset.kind == "spine":
                    self.load_spine(self.motion_asset)
            elif what == "log":
                self.log.configure(state="normal")
                self.log.insert("end", data)
                if int(self.log.index("end-1c").split(".")[0]) > 1500:
                    self.log.delete("1.0", "251.0")
                self.log.see("end")
                self.log.configure(state="disabled")
            elif what == "error":
                self.status.set("操作失败，请查看任务日志。")
                self.events.put(("log", f"错误：{data}\n"))
                if not self.closing:
                    messagebox.showerror("操作失败", data, parent=self.root)
            else:
                self.status.set(data)
        self.poll_timer = self.root.after(70, self.poll)

    def on_destroy(self, event):
        if event.widget is self.root:
            for timer in (self.poll_timer, self.filter_timer, self.resize_timer):
                if timer:
                    self.root.after_cancel(timer)

    def set_library(self, assets):
        self.assets = assets
        kinds = ["全部", *sorted({a.kind for a in assets})]
        self.categories.configure(values=kinds)
        if self.category.get() not in kinds:
            self.category.set("全部")
        self.apply_filter()

    def schedule_filter(self, *_):
        if self.filter_timer:
            self.root.after_cancel(self.filter_timer)
        self.filter_timer = self.root.after(120, self.apply_filter)

    def apply_filter(self):
        if self.filter_timer:
            self.root.after_cancel(self.filter_timer)
        self.filter_timer = None
        query, kind = self.query.get().strip().casefold(), self.category.get()
        self.matches = [a for a in self.assets if (kind == "全部" or kind == a.kind)
                        and (not query or query in a.title.casefold() or query in a.path.name.casefold())]
        self.page = 0
        self.draw_page()

    def change_page(self, delta):
        self.page = max(0, min(self.page + delta, (len(self.matches) - 1) // self.page_size))
        self.draw_page()

    def draw_page(self):
        self.tree.delete(*self.tree.get_children())
        start = self.page * self.page_size
        for i, asset in enumerate(self.matches[start:start + self.page_size], start):
            self.tree.insert("", "end", iid=str(i), values=(asset.kind, asset.title))
        pages = max(1, (len(self.matches) + self.page_size - 1) // self.page_size)
        self.page_label.set(f"{len(self.matches):,} 项 · {self.page + 1}/{pages}")
        self.show_preview()

    def selected(self):
        ids = self.tree.selection()
        return self.matches[int(ids[0])] if ids else None

    def on_resize(self, _):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(130, self.show_preview)

    def set_motions(self, choices):
        self.motion_map = choices
        self.motion_choice.configure(values=list(choices), state="readonly" if choices else "disabled")
        self.motion_choice.set(next(iter(choices), ""))
        self.motion_button.state(["!disabled" if choices else "disabled"])

    def load_spine(self, asset):
        self.status.set("正在后台读取 Spine 动作；首次启用会从 GitHub 校验并缓存约 72 MiB 组件。")
        if self.spine_lookup is not None:
            return  # Finish this request first; the poller picks up the latest selection.
        self.spine_lookup = asset
        def read():
            try:
                choices = core.motion_choices(asset)
            except Exception as exc:
                self.events.put(("spine", (asset, {}, str(exc))))
            else:
                self.events.put(("spine", (asset, choices, "")))
        threading.Thread(target=read, daemon=True).start()

    def show_preview(self):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = None
        asset = self.selected()
        if asset != self.motion_asset:
            self.motion_asset = asset
            self.set_motions({})
            if asset and asset.kind == "spine":
                self.load_spine(asset)
            elif asset and asset.kind == "cubism":
                try:
                    self.set_motions(core.motion_choices(asset))
                except (KeyError, ValueError, OSError) as exc:
                    self.status.set(f"动作索引不可读：{exc}")
        self.canvas.delete("all")
        self.photo = None
        w, h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.details.set(f"{asset.title}\n{asset.path}" if asset else "")
        message = "选择素材查看预览" if asset is None else "无静态缩略图，可用「打开」查看"
        image_path = asset.preview if asset else None
        if asset and image_path is None and asset.path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            image_path = asset.path
        if image_path and Path(image_path).is_file():
            try:
                if Image is not None:
                    with Image.open(image_path) as img:
                        img.thumbnail((w, h), Image.Resampling.LANCZOS)
                        self.photo = ImageTk.PhotoImage(img)
                else:
                    img = tk.PhotoImage(file=str(image_path))
                    self.photo = img.subsample(max(1, (img.width()+w-1)//w, (img.height()+h-1)//h))
                self.canvas.create_image(w // 2, h // 2, image=self.photo)
                return
            except (ValueError, OSError, tk.TclError) as exc:
                message = f"缩略图无法打开：{exc}"
        self.canvas.create_text(w // 2, h // 2, text=message, width=max(40, w-24), fill="#675c51", justify="center")

    def action(self, action):
        asset = self.selected()
        if not asset:
            messagebox.showinfo("素材库", "请先在左侧选择一项素材。", parent=self.root)
            return
        if action == "open":
            try:
                open_local(asset.path)
            except (ValueError, OSError) as exc:
                messagebox.showerror("打开失败", str(exc), parent=self.root)
            return
        if self.busy:
            self.status.set("请等待当前任务结束。")
            return
        folder = filedialog.askdirectory(title="选择输出目录", parent=self.root)
        if folder:
            if self.game.get().strip() and Path(folder).resolve().is_relative_to(Path(self.game.get()).expanduser().resolve()):
                messagebox.showerror("输出位置", "请选择游戏安装目录以外的位置。", parent=self.root)
                return
            self.status.set("正在写入选中素材…")
            fn = core.copy_asset if action == "copy" else core.wallpaper
            self.launch(lambda: self.events.put(("status", f"已生成：{fn(asset, Path(folder))}")))

    def close(self):
        if self.busy:
            if not messagebox.askyesno("退出", "有任务正在运行。停止解码或等待当前文件操作结束后退出？", parent=self.root):
                return
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


if __name__ == "__main__":
    app = tk.Tk()
    Viewer(app)
    app.mainloop()
