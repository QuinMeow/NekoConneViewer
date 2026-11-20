"""Local NekoConne asset browser. Run with: python viewer_gui.py"""

import os
import json
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import viewer_core as core
from viewer_batch import render_queue
from viewer_runtime import APP_HOME, command
from viewer_roles import export_picker, filter_picker, role_entries

ROOT = Path(__file__).resolve().parent
try:
    from PIL import Image, ImageTk
except ImportError:
    sys.path.insert(0, str(ROOT / ".tools" / "unity"))
    try:
        from PIL import Image, ImageTk
    except ImportError:
        Image = ImageTk = None

from viewer_model_preview import PreviewJob
from viewer_video import VideoPreview

JOBS = core.CATEGORIES
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
        default_cache = APP_HOME / "exports"
        if (default_cache / "builtin").exists():
            default_cache = APP_HOME / "exports-v2"
        self.cache = tk.StringVar(value=str(Path(os.environ.get("NEKOCONNE_CACHE", default_cache)).absolute()))
        self.extra_assets, self.resource_names = tk.StringVar(), tk.StringVar()
        self.export_scope = tk.StringVar(value="1 开头角色及未归属素材（不包括其他角色）")
        self.export_ids, self.catalog = (), {}
        self.export_other = False
        self.catalog_opened = self.indexing = False
        self.path_timer = None
        self.query, self.category = tk.StringVar(), tk.StringVar(value="全部")
        self.file_kind, self.character = tk.StringVar(value="全部"), tk.StringVar()
        self.status, self.details, self.page_label = (tk.StringVar() for _ in range(3))
        self.flags = {key: tk.BooleanVar(value=key in ("images", "spine", "live2d")) for key in JOBS}
        self.assets, self.matches, self.photo = [], [], None
        self.motion_asset, self.motion_map = None, {}
        self.spine_lookup = None
        self.page = 0
        self.busy = self.closing = False
        self.events = Queue()
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
        for row, (name, var) in enumerate((("游戏安装目录", self.game), ("本地导出缓存", self.cache),
                                           ("补充普通素材目录（可选）", self.extra_assets),
                                           ("资源名清单（可选）", self.resource_names))):
            ttk.Label(paths, text=name).grid(row=row, column=0, sticky="w", padx=(0, 12))
            entry = ttk.Entry(paths, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            button = ttk.Button(paths, text="选择…", command=lambda v=var: self.browse(v))
            button.grid(row=row, column=2, padx=(6, 0))
            self.path_controls.extend([entry, button])
        self.import_button = ttk.Button(paths, text="导入外部素材目录…", command=self.import_external)
        self.import_button.grid(row=1, column=3, padx=(6, 0))
        self.path_controls.append(self.import_button)
        ttk.Label(paths, text="未识别文件可能需要名称清单").grid(row=3, column=3, padx=(6, 0))
        scope = ttk.Frame(frame)
        scope.pack(fill="x", pady=(0, 5))
        self.scope_button = ttk.Button(scope, text="选择导出角色…", command=self.choose_export_roles)
        self.scope_button.pack(side="left", padx=(0, 6))
        ttk.Label(scope, text="本次导出：").pack(side="left")
        ttk.Label(scope, textvariable=self.export_scope).pack(side="left")
        self.reindex_button = ttk.Button(scope, text="重新索引角色", command=lambda: self.refresh(catalog=True))
        self.reindex_button.pack(side="right")
        self.export_jobs = tk.StringVar(value="2")
        parallel = ttk.Spinbox(scope, from_=1, to=4, width=3, textvariable=self.export_jobs, state="readonly")
        parallel.pack(side="right", padx=(3, 8))
        ttk.Label(scope, text="并行解包").pack(side="right")
        self.path_controls.append(parallel)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(0, 8))
        for key, name in JOBS.items():
            ttk.Checkbutton(bar, text=name, variable=self.flags[key]).pack(side="left", padx=(0, 8))
        self.run_button = ttk.Button(bar, text="导出所选类别", command=self.run_jobs)
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
        self.file_kinds = ttk.Combobox(filters, textvariable=self.file_kind, state="readonly", width=14)
        self.file_kinds.pack(side="right")
        self.categories = ttk.Combobox(filters, textvariable=self.category, state="readonly", width=14, values=["全部"])
        self.categories.pack(side="right", padx=3)
        ttk.Button(filters, text="筛选已导出角色…", command=self.choose_character).pack(side="right", padx=3)
        ttk.Entry(filters, textvariable=self.character, width=9).pack(side="right", padx=(3, 0))
        ttk.Label(filters, text="角色 ID").pack(side="right")
        self.query.trace_add("write", self.schedule_filter)
        self.category.trace_add("write", self.schedule_filter)
        self.file_kind.trace_add("write", self.schedule_filter)
        self.character.trace_add("write", self.schedule_filter)

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
        self.tree = ttk.Treeview(left, columns=("kind", "title"), show="headings", selectmode="extended")
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
        self.batch_button = ttk.Button(motion_bar, text="批量渲染…", command=self.render_batch, state="disabled")
        self.batch_button.pack(side="right", padx=3)
        self.info = ttk.Label(right, textvariable=self.details, wraplength=290, justify="left")
        self.info.pack(side="bottom", fill="x", pady=6)
        self.video_bar = ttk.Frame(right)
        self.video_button = ttk.Button(self.video_bar, text="播放", command=self.toggle_video, state="disabled")
        self.video_button.pack(side="left")
        self.video_message = tk.StringVar()
        ttk.Label(self.video_bar, textvariable=self.video_message, wraplength=220).pack(side="left", padx=6)
        self.canvas = tk.Canvas(right, bg="#e9e4dd", highlightthickness=0, width=290, height=260)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        self.video = VideoPreview(self.canvas, self.video_state)
        self.playing = False
        self.tabs.bind("<<NotebookTabChanged>>",
                       lambda _: self.video.pause() if self.tabs.select() != str(library) else None)
        self.poster_job = PreviewJob(root, self.terminate)
        self.poster_path = None
        self.poster_error = ""
        self.log = tk.Text(logs, wrap="word", state="disabled", bg="#f8f7f3", fg="#27251f", font=("Consolas", 10))
        sb = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.status).pack(anchor="w", pady=(7, 0))
        for var in (self.cache, self.game, self.extra_assets, self.resource_names):
            var.trace_add("write", self.paths_changed)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Destroy>", self.on_destroy, add="+")
        self.poll_timer = root.after(70, self.poll)
        self.refresh(catalog=True)

    def browse(self, var):
        current = Path(var.get()) if var.get() else ROOT
        if var is self.resource_names:
            value = filedialog.askopenfilename(parent=self.root, title="选择本地资源名清单",
                                               initialdir=current.parent if current.is_file() else ROOT,
                                               filetypes=[("文本 / JSON / Markdown", "*.txt *.json *.md *.markdown"),
                                                          ("全部文件", "*.*")])
        else:
            value = filedialog.askdirectory(parent=self.root, initialdir=current if current.is_dir() else ROOT)
        if value:
            var.set(value)

    def paths_changed(self, *_):
        if self.path_timer is not None:
            self.root.after_cancel(self.path_timer)
        for name in ("export_roles", "filter_roles"):
            if dialog := self.root.children.get(name):
                dialog.destroy()
        self.catalog = {}
        self.character.set("")
        self.set_export_scope(())
        self.set_library([])
        self.status.set("目录已改变，等待读取索引。")
        self.path_timer = self.root.after(500, self.refresh_paths)

    def refresh_paths(self):
        self.path_timer = None
        if self.closing:
            return
        if self.busy:
            self.path_timer = self.root.after(250, self.refresh_paths)
        else:
            self.refresh(catalog=True)

    def set_busy(self, busy, can_stop=False):
        self.busy = busy
        for w in [self.run_button, self.scan_button, self.scope_button, self.reindex_button, *self.path_controls]:
            w.state(["disabled" if busy else "!disabled"])
        self.stop_button.state(["!disabled" if can_stop else "disabled"])
        self.update_motion_buttons()

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
        threading.Thread(target=work, daemon=True).start()

    def refresh(self, catalog=False, show_picker=False):
        if self.busy or not self.cache.get().strip():
            return
        cache = Path(self.cache.get()).expanduser().resolve()
        game = Path(self.game.get()).expanduser().resolve() if self.game.get().strip() else None
        extra = Path(self.extra_assets.get()).expanduser().resolve() if self.extra_assets.get().strip() else None
        names = Path(self.resource_names.get()).expanduser().resolve() if self.resource_names.get().strip() else None
        self.indexing = bool(catalog and game and game.is_dir() and not cache.is_relative_to(game)
                             and (extra is None or extra.is_dir()) and (names is None or names.is_file()))
        build = self.indexing
        self.status.set("正在建立角色目录（仅索引，不导出素材）…" if build else "正在读取已有素材…")

        def task():
            assets = list(core.library(cache)) if cache.is_dir() else []
            self.events.put(("library", assets))
            if not build:
                hint = " 请确认输入路径有效，且缓存不在游戏目录内。" if catalog else ""
                self.events.put(("status", f"已读取 {len(assets):,} 项已有素材。{hint}"))
                return
            try:
                code = self.run_command("角色目录（仅索引）",
                                        core.catalog_command(game, cache, assets=extra, resource_names=names))
                if self.cancel.is_set():
                    self.events.put(("status", "已停止角色索引，没有执行素材导出。"))
                    return
                if code:
                    raise ValueError(f"退出码 {code}，详见任务日志")
                data = json.loads((cache / "index/catalog.json").read_text(encoding="utf-8-sig"))
                if not isinstance(data.get("characters"), dict):
                    raise ValueError("角色目录格式无效")
                entries = core.merge_role_catalog(data["characters"])
                self.events.put(("catalog", (entries, len(data.get("issues", [])), show_picker)))
            except (OSError, ValueError) as exc:
                self.events.put(("log", f"角色索引失败：{exc}\n"))
                self.events.put(("status", "角色索引失败，请检查目录与任务日志，再点击「重新索引角色」。"))
        self.launch(task, can_stop=build)

    def run_jobs(self):
        if self.busy:
            return
        if self.path_timer is not None or not set(self.export_ids).issubset(self.catalog):
            self.status.set("目录已改变，请等索引完成后重新确认导出范围。")
            return
        game, cache = Path(self.game.get()).expanduser(), Path(self.cache.get()).expanduser()
        keys = [k for k in JOBS if self.flags[k].get()]
        if not self.game.get().strip() or not game.is_dir() or not self.cache.get().strip() or not keys:
            messagebox.showinfo("运行任务", "请选择游戏目录、缓存目录和至少一个解码任务。", parent=self.root)
            return
        extra = Path(self.extra_assets.get()).expanduser().resolve() if self.extra_assets.get().strip() else None
        names = Path(self.resource_names.get()).expanduser().resolve() if self.resource_names.get().strip() else None
        if (cache.resolve().is_relative_to(game.resolve()) or (extra is not None and not extra.is_dir())
                or (names is not None and not names.is_file())):
            messagebox.showerror("导出输入", "请确认补充目录和清单文件有效，并使用游戏目录以外的缓存。", parent=self.root)
            return
        characters, include_others, jobs = tuple(self.export_ids), self.export_other, int(self.export_jobs.get())
        self.tabs.select(1)
        self.status.set("正在运行，详情见任务日志。")

        def task():
            cmd = core.scan_command(game, cache, keys, characters=characters, assets=extra, resource_names=names,
                                    include_other_roles=include_others, jobs=jobs)
            self.commands(cache, [(" / ".join(JOBS[k] for k in keys), cmd)])
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
        cmd = command("scan_assets.py", "--input", source, "--out", cache)
        if self.game.get().strip():
            game = Path(self.game.get()).expanduser().resolve()
            if cache.is_relative_to(game):
                messagebox.showerror("缓存位置", "不能写入游戏安装目录。", parent=self.root)
                return
        self.tabs.select(1)
        self.launch(lambda: self.commands(cache, [("外部素材导入", cmd)]), True)

    def run_command(self, name, cmd):
        self.events.put(("log", f"\n[{name}] 开始\n"))
        p = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                             env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        self.process = p
        try:
            if self.cancel.is_set():
                self.terminate(p)
            with p.stdout:
                for line in p.stdout:
                    self.events.put(("log", line))
            code = p.wait()
        finally:
            self.process = None
        self.events.put(("log", f"[{name}] 退出码 {code}\n"))
        return code

    def commands(self, cache, commands):
        results = []
        for name, cmd in commands:
            if self.cancel.is_set():
                break
            code = self.run_command(name, cmd)
            results.append(f"{name}: {code}")
        self.events.put(("library", list(core.library(cache)) if cache.is_dir() else []))
        self.events.put(("status", ("已停止。 " if self.cancel.is_set() else "任务退出码：") + "；".join(results)))

    def render_motion(self):
        asset, name = self.selected(), self.motion_choice.get()
        if self.busy or asset is None or asset.kind not in core.MODEL_KINDS or name not in self.motion_map:
            return
        cache = Path(self.cache.get()).expanduser().resolve()
        group, index = self.motion_map[name]
        options = {}
        if asset.kind == "cubism":
            options = self.render_options(cache)
            if options is None:
                return
        self.tabs.select(1)
        self.status.set(f"正在渲染 {name}…")
        def render():
            cmd = core.render_command(asset, cache, group, index, **options)
            self.commands(cache, [(name, cmd)])
        self.launch(render, True)

    def render_options(self, cache):
        dialog = tk.Toplevel(self.root)
        dialog.title("Live2D 导出设置")
        dialog.transient(self.root)
        dialog.grab_set()
        width = tk.StringVar(value=str(self.root.winfo_screenwidth()))
        height = tk.StringVar(value=str(self.root.winfo_screenheight()))
        transparent = tk.BooleanVar()
        result = {}
        background = tk.StringVar(value=str(core.scene_image(cache, "bg_quest_upside_01", "title_bg_pc") or ""))
        for i, (label, var) in enumerate((("宽度", width), ("高度", height), ("背景图片", background))):
            ttk.Label(dialog, text=label).grid(row=i, column=0, padx=8)
            ttk.Entry(dialog, textvariable=var, width=38).grid(row=i, column=1, pady=5)
        def browse():
            value = filedialog.askopenfilename(parent=dialog, filetypes=[("图片", "*.png *.jpg *.jpeg *.webp")])
            if value:
                background.set(value)
        ttk.Button(dialog, text="选择…", command=browse).grid(row=2, column=2, padx=4)
        ttk.Checkbutton(dialog, text="透明输出（用于合成壁纸）", variable=transparent).grid(row=3, column=1, sticky="w")
        def accept():
            try:
                w, h = int(width.get()), int(height.get())
                if w < 2 or h < 2:
                    raise ValueError()
            except ValueError:
                messagebox.showerror("分辨率", "请输入大于 1 的整数宽高。", parent=dialog)
                return
            result.update(width=w, height=h, transparent=transparent.get(),
                          background=None if transparent.get() else background.get().strip() or None,
                          game=self.game.get().strip() or None)
            dialog.destroy()
        ttk.Button(dialog, text="开始渲染", command=accept).grid(row=4, column=1, pady=8)
        self.root.wait_window(dialog)
        return result or None

    def choose_batch(self, kind, same, all_motions):
        spine = kind == "spine"
        dialog = tk.Toplevel(self.root)
        dialog.title("批量渲染 Spine" if spine else "批量渲染 Live2D")
        dialog.transient(self.root)
        dialog.grab_set()
        choice = tk.IntVar(value=0 if spine else 1)
        labels = (("当前姿势的全部动作", "全部姿势与动作") if spine else ("当前组全部动作", "当前模型全部动作"))
        answer = []
        for i, label in enumerate(labels):
            label += f"（{len(all_motions if i else same)}）"
            ttk.Radiobutton(dialog, text=label, variable=choice, value=i).pack(anchor="w", padx=12, pady=6)
        ttk.Label(dialog, text="跳过来源未变且已有结果的动作。" if spine else "整批共用一套输出设置，再次渲染会更新对应结果。").pack(padx=12)
        def start():
            answer.extend(all_motions if choice.get() else same)
            dialog.destroy()
        ttk.Button(dialog, text="开始渲染", command=start).pack(pady=8)
        self.root.wait_window(dialog)
        return answer

    def render_batch(self):
        models = self.selected_models()
        if self.busy or not models:
            return
        pending, all_poses = None, False
        choice = self.motion_choice.get()
        if len(models) == 1 and models[0] == self.motion_asset and choice in self.motion_map:
            active_group, _ = self.motion_map[choice]
            all_motions = [(name, *pos) for name, pos in self.motion_map.items()]
            same = [entry for entry in all_motions if entry[1] == active_group]
            pending = self.choose_batch(models[0].kind, same, all_motions)
            if not pending:
                return
        else:
            all_poses = self.choose_model_batch(models)
            if all_poses is None:
                return
        cache = Path(self.cache.get()).expanduser().resolve()
        options = self.render_options(cache) if any(asset.kind == "cubism" for asset in models) else {}
        if options is None:
            return
        self.tabs.select(1)
        self.status.set(f"正在批量渲染 {len(models)} 个模型…")

        def task():
            self.commands(cache, render_queue(models, cache, options, override=pending, all_poses=all_poses,
                                              cancelled=self.cancel.is_set, log=lambda msg: self.events.put(("log", msg))))
        self.launch(task, True)

    def choose_model_batch(self, models):
        dialog = tk.Toplevel(self.root)
        dialog.title("批量渲染所选模型")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"已选 {len(models)} 个模型。每个模型渲染全部动作，非模型项不参与。").pack(anchor="w")
        all_poses = tk.BooleanVar(dialog)
        if any(asset.kind == "spine" for asset in models):
            ttk.Checkbutton(frame, text="Spine 渲染全部姿势（不勾选时仅渲染默认姿势）",
                            variable=all_poses).pack(anchor="w", pady=6)
        result = []
        def accept():
            result.append(all_poses.get())
            dialog.destroy()
        ttk.Button(frame, text="继续", command=accept).pack(anchor="e", pady=(8, 0))
        self.root.wait_window(dialog)
        return result[0] if result else None

    @staticmethod
    def terminate(p):
        if p is not None and p.poll() is None:
            if sys.platform == "win32" and shutil.which("taskkill"):
                try:
                    result = subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True,
                                            creationflags=subprocess.CREATE_NO_WINDOW, timeout=3)
                    if result.returncode == 0:
                        return
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if p.poll() is None:
                try:
                    p.terminate()
                except OSError:
                    if p.poll() is None:
                        raise

    def stop(self):
        self.cancel.set()
        self.stop_button.state(["disabled"])
        self.status.set("正在停止当前任务…")
        # Bind to this process now: a later job may already be running by the
        # time the termination thread receives a timeslice.
        threading.Thread(target=self.terminate, args=(self.process,), daemon=True).start()

    def poll(self):
        for _ in range(150):
            try:
                what, data = self.events.get_nowait()
            except Empty:
                break
            if what == "done":
                self.set_busy(False)
                self.indexing = False
                if self.closing:
                    self.root.destroy()
                    return
            elif what == "library":
                self.set_library(data)
            elif what == "catalog":
                if self.closing:
                    continue
                self.catalog, issues, show_picker = data
                self.update_picker("export_roles")
                self.update_picker("filter_roles")
                if not set(self.export_ids).issubset(self.catalog):
                    # Do not silently turn a previously scoped export into an unscoped one.
                    self.status.set("角色目录已经改变，导出前请重新选择角色。")
                else:
                    self.status.set(f"角色目录：{len(self.catalog):,} 个有证据的 ID，{issues} 项索引提示。尚未开始导出。")
                if self.catalog and (show_picker or not self.catalog_opened):
                    self.show_export_picker()
            elif what == "spine":
                asset, choices, error = data
                self.spine_lookup = None
                if self.motion_asset == asset:
                    self.set_motions(choices)
                    self.status.set(f"Spine：{error}" if error else f"Spine 动作索引已就绪：{len(choices)} 个选择。")
                    if not error:
                        self.show_preview()
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
            for timer in (self.poll_timer, self.filter_timer, self.resize_timer, self.path_timer):
                if timer:
                    self.root.after_cancel(timer)

    def set_library(self, assets):
        self.assets = assets
        self.update_picker("filter_roles")
        categories = ["全部", *JOBS.values()]
        self.categories.configure(values=categories)
        if self.category.get() not in categories:
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
        query = self.query.get().strip().casefold()
        group, kind, character = self.category.get(), self.file_kind.get(), self.character.get().strip()
        character = core.normalize_role(character) or character
        base = [a for a in self.assets if group == "全部" or JOBS.get(a.category) == group]
        kinds = ["全部", *sorted({core.KINDS.get(a.kind, a.kind) for a in base})]
        self.file_kinds.configure(values=kinds)
        if kind not in kinds:
            kind = "全部"
            self.file_kind.set(kind)
        self.matches = [a for a in base if (kind == "全部" or kind == core.KINDS.get(a.kind, a.kind))
                        and (not character or character == core.normalize_role(a.character_id))
                        and (not query or any(query in text.casefold() for text in (a.title, a.path.name, a.character_id, a.note)))]
        self.page = 0
        self.draw_page()

    def change_page(self, delta):
        self.page = max(0, min(self.page + delta, (len(self.matches) - 1) // self.page_size))
        self.draw_page()

    def draw_page(self):
        self.tree.delete(*self.tree.get_children())
        start = self.page * self.page_size
        for i, asset in enumerate(self.matches[start:start + self.page_size], start):
            suffix = " · ".join(v for v in (asset.character_id, asset.note) if v)
            self.tree.insert("", "end", iid=str(i), values=(core.KINDS.get(asset.kind, asset.kind),
                                                           f"{asset.title}  [{suffix}]" if suffix else asset.title))
        pages = max(1, (len(self.matches) + self.page_size - 1) // self.page_size)
        self.page_label.set(f"{len(self.matches):,} 项 · {self.page + 1}/{pages}")
        self.show_preview()

    def selected(self):
        ids = self.tree.selection()
        if not ids:
            return None
        focus = self.tree.focus()
        return self.matches[int(focus if focus in ids else ids[0])]

    def selected_models(self):
        return [asset for i in self.tree.selection()
                if (asset := self.matches[int(i)]).kind in core.MODEL_KINDS]

    def on_resize(self, _):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = self.root.after(130, self.show_preview)

    def set_motions(self, choices):
        self.motion_map = choices
        self.motion_choice.configure(values=list(choices), state="readonly" if choices else "disabled")
        self.motion_choice.set(next(iter(choices), ""))
        self.update_motion_buttons()

    def update_motion_buttons(self):
        has_motion = bool(not self.busy and self.motion_map and self.motion_asset)
        models = self.selected_models()
        batch = not self.busy and bool(models) and (len(models) > 1 or has_motion or self.selected() not in models)
        self.motion_button.state(["!disabled" if has_motion else "disabled"])
        self.batch_button.state(["!disabled" if batch else "disabled"])

    def set_export_scope(self, ids, *, include_other_roles=False):
        self.export_ids = tuple(sorted(set(ids)))
        self.export_other = bool(include_other_roles and not self.export_ids)
        ids = self.export_ids
        if not ids:
            self.export_scope.set("所有角色及未归属素材" if self.export_other else "1 开头角色及未归属素材（不包括其他角色）")
            return
        self.export_scope.set(f"{len(ids)} 个角色：" + "、".join(ids[:6]) + (" 等" if len(ids) > 6 else "")
                              + "（排除未归属素材）")

    def choose_export_roles(self):
        if self.catalog:
            self.show_export_picker()
        elif self.game.get().strip() and self.cache.get().strip():
            self.refresh(catalog=True, show_picker=True)
        else:
            self.status.set("请先选择游戏目录和缓存目录，程序将读取角色索引。")

    def update_picker(self, name):
        if dialog := self.root.children.get(name):
            cache = Path(self.cache.get()).expanduser().resolve()
            assets = self.assets if name == "filter_roles" else None
            dialog.roles.replace_entries(role_entries(cache, self.catalog, assets))

    def show_export_picker(self):
        if dialog := self.root.children.get("export_roles"):
            dialog.lift()
            return
        self.catalog_opened = True
        cache = Path(self.cache.get()).expanduser().resolve()
        export_picker(self.root, cache, role_entries(cache, self.catalog), self.export_ids,
                      self.export_other, self.set_export_scope, self.status.set)

    def choose_character(self):
        if dialog := self.root.children.get("filter_roles"):
            dialog.lift()
            return
        cache = Path(self.cache.get()).expanduser().resolve()
        filter_picker(self.root, cache, role_entries(cache, self.catalog, self.assets), self.character)

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

    def video_state(self, state, detail):
        self.playing = state == "playing"
        self.video_button.configure(text="暂停" if self.playing else "重播" if state == "ended" else "播放")
        self.video_button.state(["disabled" if state in {"loading", "error", "stopped"} else "!disabled"])
        self.video_message.set(detail or {"loading": "正在读取视频…", "paused": "已暂停", "ended": "播放完毕"}.get(state, ""))

    def toggle_video(self):
        if self.playing:
            self.video.pause()
        else:
            self.video.play()

    def poster_for(self, asset):
        if self.busy:
            return None, "等待当前任务结束后渲染首帧"
        game = Path(self.game.get()).expanduser() if self.game.get().strip() else None
        if not game or not (game / "NekoConne_Data/data.unity3d").is_file():
            return None, "选择游戏安装目录后可渲染首帧"
        if asset.kind == "spine" and self.spine_lookup is not None:
            return None, "等待读取 Spine 模型…"
        if self.poster_path is None and not self.poster_error:
            try:
                cache = Path(self.cache.get()).expanduser().resolve()
                self.poster_path = core.model_poster_path(cache, asset)
            except OSError as exc:
                self.poster_error = str(exc)
            else:
                if not self.poster_path.is_file():
                    cmd = command("model_previews.py", "--game", game, "--model", asset.path, "--png", self.poster_path)
                    def ready(error):
                        self.poster_error = error
                        self.show_preview()
                    self.poster_job.start(cmd, ready)
        if self.poster_path is not None and self.poster_path.is_file():
            return self.poster_path, ""
        return None, f"首帧预览失败：{self.poster_error}" if self.poster_error else "正在渲染首帧…"

    def show_preview(self):
        if self.resize_timer:
            self.root.after_cancel(self.resize_timer)
        self.resize_timer = None
        asset = self.selected()
        if asset != self.motion_asset:
            self.poster_job.stop()
            self.poster_path, self.poster_error = None, ""
            self.motion_asset = asset
            self.set_motions({})
            if asset and asset.kind == "spine":
                self.load_spine(asset)
            elif asset and asset.kind == "cubism":
                try:
                    self.set_motions(core.motion_choices(asset))
                except (KeyError, ValueError, OSError) as exc:
                    self.status.set(f"动作索引不可读：{exc}")
        self.update_motion_buttons()
        self.details.set("\n".join(v for v in (asset.title, asset.character_id, asset.note, str(asset.path)) if v) if asset else "")
        if asset and asset.kind in {"video", "action"} and asset.path.is_file():
            self.video_bar.pack(side="bottom", fill="x", pady=(3, 0))
            if self.video.path != asset.path:
                self.canvas.delete("all")
                self.photo = None
                self.video.show(asset.path)
            else:
                self.video.resize()
            return
        if self.video.path is not None:
            self.video.stop()
        self.video_bar.pack_forget()
        self.canvas.delete("all")
        self.photo = None
        w, h = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        message = "选择素材查看预览" if asset is None else "无静态缩略图，可用「打开」查看"
        image_path = asset.preview if asset else None
        if asset and asset.kind in core.MODEL_KINDS and not (image_path and image_path.is_file()):
            image_path, message = self.poster_for(asset)
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
        if action == "wallpaper":
            cache = Path(self.cache.get()).expanduser().resolve()
            folder = str(cache / "wallpapers")
        else:
            folder = filedialog.askdirectory(title="选择输出目录", parent=self.root)
        if folder:
            if self.game.get().strip() and Path(folder).resolve().is_relative_to(Path(self.game.get()).expanduser().resolve()):
                messagebox.showerror("输出位置", "请选择游戏安装目录以外的位置。", parent=self.root)
                return
            self.status.set("正在打包原生 Spine 网页壁纸…" if action == "wallpaper" and asset.kind == "spine"
                            else "正在写入选中素材…")
            fn = core.copy_asset if action == "copy" else core.wallpaper
            self.launch(lambda: self.events.put(("status", f"已生成：{fn(asset, Path(folder))}")))

    def close(self):
        if self.busy:
            if not self.indexing and not messagebox.askyesno("退出", "有任务正在运行。停止解码或等待当前文件操作结束后退出？", parent=self.root):
                return
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


if __name__ == "__main__":
    app = tk.Tk()
    Viewer(app)
    app.mainloop()
