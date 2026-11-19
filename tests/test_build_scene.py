import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import build_scene
from viewer_core import Asset


class BuildSceneTests(unittest.TestCase):
    def test_existing_motion_can_be_packaged_without_rendering(self):
        import sys
        with patch.object(sys, "path", [str(build_scene.ROOT / ".tools/unity"), *sys.path]):
            from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / "live2d/Example/Example.model3.json"
            model.parent.mkdir(parents=True)
            model.write_text(json.dumps({"FileReferences": {"Motions": {"Idle": [{"File": "idle.motion3.json"}]}}}))
            cache = root / "renders/live2d/Example"
            cache.mkdir(parents=True)
            Image.new("RGBA", (12, 16), "green").save(cache / "Example-Idle-0-alpha.png")
            (cache / "Example-Idle-0-alpha.webm").write_bytes(b"pre-rendered fixture")
            background = root / "bg.png"
            Image.new("RGB", (32, 18), "white").save(background)
            asset = Asset("cubism", "Example", model)
            with patch.object(build_scene, "render_command", side_effect=AssertionError("renderer was requested")):
                project = build_scene.build(asset, root, root / "projects", background=background)
            self.assertEqual((project / "idle.webm").read_bytes(), b"pre-rendered fixture")
            self.assertTrue((project / "preview.jpg").is_file())
