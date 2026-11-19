"""Character portraits and modeless role pickers for the desktop viewer."""
import json
import tkinter as tk
from tkinter import ttk

from tools.character_index import implemented_role, merge_role_catalog, normalize_role


def _watch(widget, var, callback):
    token = var.trace_add("write", callback)
    def remove(event):
        if event.widget is widget:
            var.trace_remove("write", token)
    widget.bind("<Destroy>", remove, add="+")


class RoleGrid(ttk.Frame):
    thumb, card_width, card_height = 96, 138, 148

    def __init__(self, parent, cache, entries, selected=(), *, multiple=False, include_others=False, on_pick=None):
        super().__init__(parent)
        self.cache, self.entries = cache, entries
        self.multiple, self.on_pick = multiple, on_pick
        self.picked, self.photos = set(selected).intersection(entries), {}
        self.query = tk.StringVar(self)
        self.other = tk.BooleanVar(self, value=include_others)
        self.summary = tk.StringVar(self)
        self.timer, self.cursor, self.columns = None, None, 1
        self.visible = []
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 7))
        ttk.Label(top, text="查找 ID / 名称").pack(side="left")
        ttk.Entry(top, textvariable=self.query, width=17).pack(side="left", padx=5)
        ttk.Checkbutton(top, text="包括其他角色（非 1 开头）", variable=self.other).pack(side="left", padx=6)
        ttk.Label(top, textvariable=self.summary).pack(side="right")
        area = ttk.Frame(self)
        area.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(area, bg="#edeae3", highlightthickness=0, takefocus=True)
        scroll = ttk.Scrollbar(area, command=self.canvas.yview)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        def yscroll(first, last):
            scroll.set(first, last)
            self.schedule_draw()
        self.canvas.configure(yscrollcommand=yscroll)
        self.canvas.bind("<Configure>", lambda _: self.layout())
        self.canvas.bind("<Button-1>", self.click)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            (-1 if e.delta > 0 else 1) * max(1, abs(e.delta)//120), "units"))
        self.canvas.configure(yscrollincrement=32)
        for key in ("Left", "Right", "Up", "Down", "space", "Return", "Home", "End"):
            self.canvas.bind(f"<{key}>", self.key)
        _watch(self, self.query, self.refilter)
        _watch(self, self.other, self.refilter)
        self.bind("<Destroy>", self.cleanup, add="+")
        self.refilter()

    @staticmethod
    def names(row):
        return [str(name) for name in row.get("names", {}).values() if name]

    def refilter(self, *_):
        query = self.query.get().strip().casefold()
        query = normalize_role(query) or query
        if not self.other.get():
            before = self.picked
            self.picked = {cid for cid in before if implemented_role(cid)}
            if self.on_pick and before != self.picked:
                self.on_pick("")
        self.visible = [cid for cid, row in sorted(self.entries.items())
                        if (self.other.get() or implemented_role(cid))
                        and any(query in s.casefold() for s in [cid, *self.names(row)])]
        self.cursor = next((cid for cid in self.visible if cid in self.picked), next(iter(self.visible), None))
        self.canvas.yview_moveto(0)
        self.layout()

    def replace_entries(self, entries):
        if self.on_pick and self.picked.difference(entries):
            self.on_pick("")
        self.picked.intersection_update(entries)
        self.entries = entries
        self.photos.clear()
        self.refilter()

    def layout(self):
        w = max(1, self.canvas.winfo_width())
        self.columns = max(1, w//self.card_width)
        rows = (len(self.visible)+self.columns-1)//self.columns
        self.canvas.configure(scrollregion=(0, 0, w, rows*self.card_height))
        self.schedule_draw()

    def schedule_draw(self):
        if self.timer is None:
            self.timer = self.after_idle(self.draw)

    def cleanup(self, event):
        if event.widget is self and self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def portrait(self, cid):
        if cid not in self.photos:
            self.photos[cid] = None
            try:
                from PIL import Image, ImageTk
            except ImportError:
                return None
            for item in self.entries[cid].get("avatars", []):
                if not item.get("file"):
                    continue
                path = (self.cache / item["file"]).resolve()
                if not path.is_relative_to(self.cache) or not path.is_file():
                    continue
                try:
                    with Image.open(path) as original:
                        avatar = original.convert("RGBA")
                        if not (bbox := avatar.getchannel("A").getbbox()):
                            continue
                        avatar = avatar.crop(bbox)
                        scale = self.thumb / max(avatar.size)
                        size = tuple(max(1, round(n*scale)) for n in avatar.size)
                        photo = ImageTk.PhotoImage(avatar.resize(size, Image.Resampling.LANCZOS), master=self)
                    self.photos[cid] = photo
                    break
                except (OSError, ValueError, tk.TclError):
                    continue
        return self.photos[cid]

    def draw(self):
        self.timer = None
        cv, height = self.canvas, self.card_height
        cv.delete("all")
        self.summary.set(f"显示 {len(self.visible)} · 已选 {len(self.picked)}")
        if not self.visible:
            cv.create_text(20, 25, anchor="w", text="没有匹配的角色", fill="#55544e")
            return
        width = max(1, cv.winfo_width()) / self.columns
        top = max(0, int(cv.canvasy(0)//height))
        end = min(len(self.visible), (int(cv.canvasy(cv.winfo_height())//height)+2)*self.columns)
        for i in range(top*self.columns, end):
            cid = self.visible[i]
            row, col = divmod(i, self.columns)
            x, y, mid = col*width, row*height, (col+.5)*width
            selected = cid in self.picked
            cv.create_rectangle(x+4, y+3, x+width-4, y+height-3, fill="#e5f3ed" if selected else "#faf9f6",
                                outline="#246857" if selected else "#c6c6be", width=3 if selected else 1)
            if photo := self.portrait(cid):
                cv.create_image(mid, y+57, image=photo)
            else:
                cv.create_text(mid, y+55, text="暂无头像", fill="#706f68")
            if self.cursor == cid and cv.focus_get() is cv:
                cv.create_rectangle(x+7, y+6, x+width-7, y+height-6, outline="#246857", dash=(3, 3))
            cv.create_text(mid, y+117, text=("已选 " if selected else "")+cid, fill="#184d40" if selected else "#252923",
                           font=("Microsoft YaHei UI", 11, "bold"))
            names = self.names(self.entries[cid])
            caption = names[0] if names else f"{self.entries[cid].get('assets', 0)} 项素材"
            cv.create_text(mid, y+136, text=caption[:18], fill="#595c55")

    def toggle(self, cid):
        if cid not in self.visible:
            return
        self.cursor = cid
        if self.multiple:
            self.picked.symmetric_difference_update([cid])
        else:
            self.picked = {cid}
        self.schedule_draw()
        if self.on_pick:
            self.on_pick(cid)

    def click(self, event):
        self.canvas.focus_set()
        width = max(1, self.canvas.winfo_width())/self.columns
        i = int(self.canvas.canvasy(event.y)//self.card_height)*self.columns+int(event.x//width)
        if 0 <= i < len(self.visible):
            self.toggle(self.visible[i])

    def key(self, event):
        if not self.visible:
            return "break"
        i = self.visible.index(self.cursor) if self.cursor in self.visible else 0
        if event.keysym in ("Return", "space"):
            self.toggle(self.visible[i])
        else:
            delta = {"Left": -1, "Right": 1, "Up": -self.columns, "Down": self.columns,
                     "Home": -len(self.visible), "End": len(self.visible)}
            i = max(0, min(len(self.visible)-1, i+delta[event.keysym]))
            self.cursor = self.visible[i]
            y = (i//self.columns)*self.card_height
            height = ((len(self.visible)+self.columns-1)//self.columns)*self.card_height
            if y < self.canvas.canvasy(0) or y+self.card_height > self.canvas.canvasy(self.canvas.winfo_height()):
                self.canvas.yview_moveto(y/height)
            self.schedule_draw()
        return "break"


def role_entries(cache, catalog, assets=None):
    """Use catalog portraits, but limit the post-export picker to actual library roles."""
    def load(path, nested=False):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            return merge_role_catalog(data["characters"] if nested else data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}
    primary = catalog or load(cache / "index/catalog.json", nested=True)
    fallback = load(cache / "index/characters.json")
    if assets is None:
        counts = {cid: row.get("assets", 0) for cid, row in primary.items()}
    else:
        counts = {}
        for asset in assets:
            if cid := normalize_role(asset.character_id):
                counts[cid] = counts.get(cid, 0) + 1
    result = {}
    for cid, count in counts.items():
        row, backup = primary.get(cid, {}), fallback.get(cid, {})
        result[cid] = {"names": row.get("names") or backup.get("names", {}),
                       "avatars": [*row.get("avatars", []), *backup.get("avatars", [])], "assets": count}
    return result


class PickerDialog(tk.Toplevel):
    def __init__(self, root, name, title, hint, cache, entries, selected, **options):
        super().__init__(root, name=name)
        self.title(title)
        self.geometry("770x575")
        self.minsize(530, 350)
        self.transient(root)
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=hint).pack(anchor="w", pady=(0, 7))
        self.roles = RoleGrid(frame, cache, entries, selected, **options)
        self.roles.pack(fill="both", expand=True)
        self.buttons = ttk.Frame(frame)
        self.buttons.pack(fill="x", pady=(7, 0))


def export_picker(root, cache, entries, selected, include_others, accept, status):
    dialog = PickerDialog(root, "export_roles", "导出前选择角色", "点击头像以选中或取消，可多选。确认范围后在主窗口开始导出。",
                          cache, entries, selected, multiple=True,
                          include_others=include_others or any(not implemented_role(cid) for cid in selected))
    grid, buttons = dialog.roles, dialog.buttons
    def apply(ids=None):
        if ids is not None and not ids:
            status("当前没有可应用的角色。")
            return
        accept(ids or (), include_other_roles=ids is None and grid.other.get())
        dialog.destroy()
    all_button = ttk.Button(buttons, command=apply)
    all_button.pack(side="left")
    def all_label(*_):
        all_button.configure(text="全部素材（含其他角色、未归属）" if grid.other.get() else "全部素材（1 开头角色、未归属）")
    all_label()
    _watch(dialog, grid.other, all_label)
    ttk.Button(buttons, text="应用当前可见角色", command=lambda: apply(grid.visible)).pack(side="left", padx=4)
    ttk.Button(buttons, text="应用所选角色", command=lambda: apply(grid.picked)).pack(side="right")
    ttk.Button(buttons, text="稍后", command=dialog.destroy).pack(side="right", padx=5)


def filter_picker(root, cache, entries, selection):
    current = normalize_role(selection.get()) or selection.get()
    dialog = PickerDialog(root, "filter_roles", "筛选已导出角色", "仅列出已有素材的角色。点击一张头像即可筛选素材库。",
                          cache, entries, [current], include_others=current in entries and not implemented_role(current),
                          on_pick=selection.set)
    grid, buttons = dialog.roles, dialog.buttons
    def clear():
        selection.set("")
        grid.picked.clear()
        grid.schedule_draw()
    ttk.Button(buttons, text="全部角色（清除筛选）", command=clear).pack(side="left")
    ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side="right")
