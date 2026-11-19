import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import viewer_runtime
from tools import build_scene
from viewer_core import Asset


class PortableTests(unittest.TestCase):
    def test_frozen_decode_uses_worker_not_gui_or_system_python(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp)
            with patch.object(viewer_runtime, "FROZEN", True), patch.object(viewer_runtime, "APP_HOME", app):
                cmd = viewer_runtime.command("import_bundles.py", "input folder", "--out", "cache")
            self.assertEqual(cmd, [str(app / "NekoConneWorker.exe"), "import_bundles", "input folder", "--out", "cache"])

    def test_existing_motion_can_be_packaged_without_rendering(self):
        import sys
        with patch.object(sys, "path", [str(build_scene.ROOT / ".tools/unity"), *sys.path]):
            from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "builtin/cubism/Example/Example.model3.json"
            model.parent.mkdir(parents=True)
            model.write_text(json.dumps({"FileReferences": {"Motions": {"Idle": [{"File": "idle.motion3.json"}]}}}))
            cache = root / "renders/Example"
            cache.mkdir(parents=True)
            Image.new("RGBA", (12, 16), "green").save(cache / "Example-Idle-0.png")
            (cache / "Example-Idle-0.webm").write_bytes(b"pre-rendered fixture")
            background = root / "bg.png"
            Image.new("RGB", (32, 18), "white").save(background)
            asset = Asset("cubism", "Example", model)
            with patch.object(build_scene, "render_command", side_effect=AssertionError("renderer was requested")):
                project = build_scene.build(asset, root, root / "projects", background=background)
            self.assertEqual((project / "idle.webm").read_bytes(), b"pre-rendered fixture")
            self.assertTrue((project / "preview.jpg").is_file())
