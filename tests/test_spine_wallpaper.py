import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools import build_spine_wallpaper
from viewer_core import Asset, wallpaper


class SpineWallpaperTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        model = self.root / "cache/spine/Card/Card.skel"
        model.parent.mkdir(parents=True)
        self.skeleton = b"\x00" * 8 + b"\x07" + b"4.1.11" + b"skeleton"
        self.texture = b"fixture texture"
        model.write_bytes(self.skeleton)
        model.with_suffix(".atlas").write_text("Card.png\nsize:32,32\npma:true\n")
        model.with_suffix(".png").write_bytes(self.texture)
        self.asset = Asset("spine", "Card", model)
        self.output = self.root / "wallpapers"

    def test_card_exports_original_skeleton_and_textures_with_no_video(self):
        meta = {"skins": ["default"], "animations": [{"name": "animation"}]}
        with (patch("viewer_core.spine_meta", return_value=meta),
              patch.object(build_spine_wallpaper, "web_runtime", return_value=(b"runtime", b"license"))):
            project = wallpaper(self.asset, self.output)
            with self.assertRaises(FileExistsError):
                wallpaper(self.asset, self.output)
        self.assertEqual(project.name, "NekoConne-Card-Spine")
        self.assertEqual((project / "SPINE-RUNTIMES-LICENSE.txt").read_bytes(), b"license")
        self.assertFalse(list(project.glob("*.webm")))
        config = json.loads((project / "project.json").read_text(encoding="utf8"))
        props = config["general"]["properties"]
        self.assertEqual(set(props), {"wholecard", "hidebg"})
        self.assertTrue(all(not prop["value"] for prop in props.values()))
        page = (project / config["file"]).read_text(encoding="utf8")
        self.assertIn("spine-webgl.js", page)
        self.assertNotIn("<video", page)
        raw = (project / "model-data.js").read_text(encoding="ascii").strip()
        model = json.loads(raw.removeprefix("window.NEKO_SPINE = ").removesuffix(";"))
        self.assertEqual(base64.b64decode(model["skeleton"]), self.skeleton)
        self.assertEqual(base64.b64decode(model["textures"]["Card.png"].split(",")[1]), self.texture)

    def test_multi_action_spine_is_not_rendered_as_card(self):
        meta = {"skins": ["default"], "animations": [{"name": "animation"}, {"name": "idle"}]}
        with (patch("viewer_core.spine_meta", return_value=meta),
              patch.object(build_spine_wallpaper, "web_runtime") as runtime):
            with self.assertRaisesRegex(ValueError, "default / animation"):
                wallpaper(self.asset, self.output)
        runtime.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_incompatible_skeleton_and_missing_atlas_page_leave_no_partial_project(self):
        self.asset.path.write_bytes(self.skeleton.replace(b"4.1.11", b"4.2.11"))
        with self.assertRaisesRegex(ValueError, r"Spine 4\.1"):
            build_spine_wallpaper.build(self.asset.path, self.output)
        self.asset.path.write_bytes(self.skeleton)
        self.asset.path.with_suffix(".png").unlink()
        with self.assertRaises(FileNotFoundError):
            build_spine_wallpaper.build(self.asset.path, self.output)
        self.assertFalse(self.output.exists())

    def test_ordinary_action_preserves_contain_fit(self):
        video = self.root / "action.webm"
        video.write_bytes(b"rendered action")
        project = wallpaper(Asset("action", "Action", video), self.output)
        self.assertNotIn("object-fit:cover", (project / "index.html").read_text(encoding="utf8"))
