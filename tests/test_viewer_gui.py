"""Desktop smoke tests using generated fixtures; no installation or game media needed."""

import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib

try:
    import tkinter as tk
except ImportError:
    tk = None
else:
    import viewer_gui as ui


def tiny_png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"\0\x21\x52\xbb\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@unittest.skipIf(tk is None, "Tkinter is not installed")
class ViewerSmokeTest(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk desktop unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.cache = self.work / "cache"
        self.image_path = self.cache / "builtin/images/BluePixel.png"
        self.image_path.parent.mkdir(parents=True)
        self.image_path.write_bytes(tiny_png())
        model = self.cache / "builtin/cubism/Synthetic/Synthetic.model3.json"
        model.parent.mkdir(parents=True)
        model.write_text(json.dumps({"FileReferences": {"Motions": {"Idle": [{"File": "idle.motion3.json"}]}}}))
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
        self.select("image", "bluepixel")
        self.assertIsNotNone(self.viewer.photo)
        out = self.work / "output"
        out.mkdir()
        self.stub(patch.object(ui.filedialog, "askdirectory", return_value=str(out)))
        self.viewer.action("copy")
        self.wait_idle()
        self.assertEqual((out / self.image_path.name).read_bytes(), tiny_png())
        # The backend generates a JPEG project thumbnail using the optional Pillow dependency.
        if ui.Image is not None:
            self.viewer.action("wallpaper")
            self.wait_idle()
            project = next(out.glob("*/project.json"))
            meta = json.loads(project.read_text(encoding="utf8"))
            self.assertTrue((project.parent / meta["file"]).is_file())
            self.assertEqual((project.parent / "media.png").read_bytes(), tiny_png())

    def test_motion_subprocess_log_and_exit_code(self):
        self.select("cubism")
        self.assertEqual(self.viewer.motion_choice.get(), "Idle / idle.motion3.json")
        # Exercise the subprocess and UI event loop without loading a real Cubism model.
        cmd = [sys.executable, "-u", "-c", "print('synthetic motion job');raise SystemExit(7)"]
        render = self.stub(patch.object(ui.core, "render_command", return_value=cmd))
        self.viewer.render_motion()
        self.assertTrue(self.viewer.busy)
        self.wait_idle()
        self.assertEqual(render.call_args.args[2:], ("Idle", 0))
        self.assertIn("synthetic motion job", self.viewer.log.get("1.0", "end"))
        self.assertIn(": 7", self.viewer.status.get())
        self.assertEqual({a.kind for a in self.viewer.assets}, {"cubism", "image"})


if __name__ == "__main__":
    unittest.main()
