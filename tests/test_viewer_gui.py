"""Desktop smoke tests using generated fixtures; no installation or game media needed."""

import gc
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None
else:
    import viewer_gui as ui


@unittest.skipIf(tk is None, "Tkinter is not installed")
class ViewerSmokeTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk desktop unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(gc.collect)
        self.addCleanup(self.root.destroy)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.cache = self.work / "cache"
        self.image_path = self.cache / "images/BluePixel.png"
        self.image_path.parent.mkdir(parents=True)
        pixel = tk.PhotoImage(master=self.root, width=1, height=1)
        pixel.put("#2152bb")
        pixel.write(str(self.image_path), format="png")
        model = self.cache / "live2d/Synthetic/Synthetic.model3.json"
        model.parent.mkdir(parents=True)
        model.write_text(json.dumps({"FileReferences": {"Motions": {"Idle": [{"File": "idle.motion3.json"}]}}}))
        index = self.cache / "index/assets.json"
        index.parent.mkdir()
        index.write_text(json.dumps([
            {"kind": "image", "category": "images", "file": self.image_path.relative_to(self.cache).as_posix(),
             "title": "BluePixel"},
            {"kind": "cubism", "category": "live2d", "file": model.relative_to(self.cache).as_posix(),
             "title": "Synthetic"},
        ]), encoding="utf8")
        self.stub(patch.dict(os.environ, {"NEKOCONNE_CACHE": str(self.cache)}))
        self.stub(patch.object(ui.core, "detect_game", return_value=self.work / "example-game"))
        self.stub(patch.object(ui.messagebox, "showerror", side_effect=AssertionError("Unexpected error dialog")))
        self.stub(patch.object(ui.messagebox, "showinfo", side_effect=AssertionError("Unexpected info dialog")))
        self.viewer = ui.Viewer(self.root)
        self.wait_idle()

    def stub(self, context):
        mock = context.start()
        self.addCleanup(context.stop)
        return mock

    def wait_idle(self):
        deadline = time.monotonic() + 10
        while self.viewer.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.viewer.busy)

    def select(self, category, query=""):
        v = self.viewer
        v.category.set(category)
        v.query.set(query)
        v.apply_filter()
        self.assertEqual(len(v.matches), 1)
        v.tree.selection_set("0")
        v.show_preview()

    def test_search_preview_copy_and_wallpaper(self):
        self.select(ui.JOBS["images"], "bluepixel")
        self.assertIsNotNone(self.viewer.photo)
        out = self.work / "output"
        out.mkdir()
        self.stub(patch.object(ui.filedialog, "askdirectory", return_value=str(out)))
        self.viewer.action("copy")
        self.wait_idle()
        self.assertEqual((out / self.image_path.name).read_bytes(), self.image_path.read_bytes())
        # The backend generates a JPEG project thumbnail using the optional Pillow dependency.
        if ui.Image is not None:
            self.viewer.action("wallpaper")
            self.wait_idle()
            project = next((self.cache / "wallpapers").glob("*/project.json"))
            meta = json.loads(project.read_text(encoding="utf8"))
            self.assertTrue((project.parent / meta["file"]).is_file())
            self.assertEqual((project.parent / "media.png").read_bytes(), self.image_path.read_bytes())

    def test_generate_wallpaper_button_exports_spine_card_as_native_html(self):
        from tools import build_spine_wallpaper

        path = self.cache / "spine/Card/Card.skel"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\0" * 8 + b"\x074.1.11")
        path.with_suffix(".atlas").write_text("Card.png\nsize:32,32\n", encoding="ascii")
        path.with_suffix(".png").write_bytes(b"fixture atlas image")
        asset = ui.core.Asset("spine", "Card", path, category="spine")
        meta = {"skins": ["default"], "animations": [{"name": "animation"}]}
        self.stub(patch.object(ui.core, "spine_meta", return_value=meta))
        self.stub(patch.object(build_spine_wallpaper, "web_runtime", return_value=(b"runtime", b"license")))
        self.viewer.set_library([asset])
        self.select(ui.JOBS["spine"], "Card")

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        button, = (widget for widget in descendants(self.root)
                   if isinstance(widget, ui.ttk.Button) and widget.cget("text") == "生成壁纸…")
        button.invoke()
        self.wait_idle()
        project = self.cache / "wallpapers/NekoConne-Card-Spine"
        self.assertIn("spine-webgl.js", (project / "index.html").read_text(encoding="utf8"))
        self.assertTrue((project / "model-data.js").is_file())
        self.assertFalse(list(project.glob("*.webm")))

    def test_delayed_stop_cannot_terminate_a_subsequent_worker(self):
        gate = threading.Event()
        class Delayed(threading.Thread):
            def run(self):
                if gate.wait(5):
                    super().run()
        def sleeper():
            p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            def finish():
                if p.poll() is None:
                    p.kill()
                p.wait(timeout=5)
            self.addCleanup(finish)
            return p
        first, second = sleeper(), sleeper()
        with patch.object(ui.threading, "Thread", Delayed), patch.object(ui.shutil, "which", return_value=None):
            self.viewer.process = first
            self.viewer.stop()
            self.viewer.process = second
            gate.set()
            first.wait(timeout=3)
            self.assertIsNone(second.poll())

    def test_motion_subprocess_log_and_exit_code(self):
        self.select(ui.JOBS["live2d"])
        self.assertEqual(self.viewer.motion_choice.get(), "Idle / idle.motion3.json")
        # Exercise the subprocess and UI event loop without loading a real Cubism model.
        cmd = [sys.executable, "-u", "-c", "print('synthetic motion job');raise SystemExit(7)"]
        render = self.stub(patch.object(ui.core, "render_command", return_value=cmd))
        self.stub(patch.object(self.viewer, "render_options", return_value={"width": 1920, "height": 1080}))
        self.viewer.render_motion()
        self.assertTrue(self.viewer.busy)
        self.wait_idle()
        self.assertEqual(render.call_args.args[2:], ("Idle", 0))
        self.assertIn("synthetic motion job", self.viewer.log.get("1.0", "end"))
        self.assertIn(": 7", self.viewer.status.get())
        self.assertEqual({a.kind for a in self.viewer.assets}, {"cubism", "image"})

    def test_batch_live2d_enabled_and_runs_each_motion_with_shared_settings(self):
        self.select(ui.JOBS["live2d"])
        v = self.viewer
        self.assertNotIn("disabled", v.batch_button.state())
        choices = {"Idle / idle.motion3.json": ("Idle", 0), "Action / wave.motion3.json": ("Action", 0)}
        v.set_motions(choices)
        select = self.stub(patch.object(v, "choose_batch", side_effect=lambda k, same, everything: everything))
        options = self.stub(patch.object(v, "render_options", return_value={"width": 1920, "height": 1080}))
        cmd = [sys.executable, "-u", "-c", "print('synthetic batch motion')"]
        render = self.stub(patch.object(ui.core, "render_command", return_value=cmd))
        v.batch_button.invoke()
        self.assertIn("disabled", v.batch_button.state())
        self.wait_idle()
        self.assertEqual(len(select.call_args.args[1]), 1)
        self.assertEqual(len(select.call_args.args[2]), 2)
        options.assert_called_once()
        self.assertEqual([call.args[2] for call in render.call_args_list], ["Idle", "Action"])
        self.assertTrue(all(call.kwargs["width"] == 1920 for call in render.call_args_list))
        self.select(ui.JOBS["images"])
        self.assertIn("disabled", v.batch_button.state())

    def test_extended_selection_queues_every_model_and_ignores_images(self):
        v = self.viewer
        assets = v.assets
        original = next(a for a in assets if a.kind == "cubism")
        path = self.cache / "live2d/Second/Second.model3.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(original.path.read_bytes())
        other = ui.core.Asset("cubism", "Second", path, category="live2d")
        v.set_library([*assets, other])
        v.tree.selection_set(*v.tree.get_children())
        v.tree.focus("0")
        v.show_preview()
        self.assertNotIn("disabled", v.batch_button.state())
        self.stub(patch.object(v, "choose_model_batch", return_value=False))
        self.stub(patch.object(v, "render_options", return_value={"width": 1920, "height": 1080}))
        command = [sys.executable, "-u", "-c", "print('queued model')"]
        render = self.stub(patch.object(ui.core, "render_command", return_value=command))
        v.render_batch()
        self.wait_idle()
        self.assertEqual({call.args[0] for call in render.call_args_list}, {original, other})

    def test_inline_video_follows_selection_and_does_not_restart_on_resize(self):
        v = self.viewer
        path = self.cache / "clip.webm"
        path.touch()
        asset = ui.core.Asset("video", "clip", path, category="video")
        v.set_library([asset])
        show = self.stub(patch.object(v.video, "show", side_effect=lambda p: setattr(v.video, "path", p)))
        resize = self.stub(patch.object(v.video, "resize"))
        stop = self.stub(patch.object(v.video, "stop", side_effect=lambda: setattr(v.video, "path", None)))
        v.tree.selection_set("0")
        v.show_preview()
        v.show_preview()
        show.assert_called_once_with(path)
        resize.assert_called()
        v.tree.selection_remove("0")
        v.show_preview()
        stop.assert_called_once()

    def test_focused_model_requests_poster_without_exporting_video(self):
        v = self.viewer
        game = self.work / "game"
        data = game / "NekoConne_Data"
        data.mkdir(parents=True)
        (data / "data.unity3d").touch()
        v.game.set(str(game))
        self.root.after_cancel(v.path_timer)
        v.path_timer = None
        v.set_library(ui.core.library(self.cache))
        launch = self.stub(patch.object(v.poster_job, "start"))
        self.select(ui.JOBS["live2d"])
        v.show_preview()
        launch.assert_called_once()
        command = launch.call_args.args[0]
        self.assertIn("--model", command)
        self.assertIn("--png", command)
        self.assertNotIn("--webm", command)


if __name__ == "__main__":
    unittest.main()
