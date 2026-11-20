import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import build_duo_wallpaper as duo


class DuoBuildTests(unittest.TestCase):
    def test_offline_project_owns_its_media_and_both_property_sets(self):
        with patch.object(sys, "path", [str(duo.ROOT / ".tools/unity"), *sys.path]):
            from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp).resolve()
            index = cache / "index"
            index.mkdir()
            rows = []
            for key, _, title in duo.BACKGROUNDS:
                Image.new("RGB", (24, 12), "green").save(cache / (key + ".png"))
                rows.append(dict(title=title, file=key + ".png", category="images"))
            (index / "assets.json").write_text(json.dumps(rows))
            models = cache / "live2d"
            for number, _ in duo.ACTORS:
                title = number + "_01_S01"
                folder = models / (title + "-source")
                folder.mkdir(parents=True)
                (folder / (title + ".model3.json")).touch()
                folder = cache / "audio/wav/voice_home" / number
                folder.mkdir(parents=True)
                for label in duo.VOICE_TYPES:
                    (folder / f"voice_home_{number}_charaTap_{label}.wav").write_bytes(number.encode())
            core, target = cache / "core.dll", cache / "project"
            core.touch()

            def fake_bake(model, dll, directory, prefix):
                self.assertEqual(core, dll)
                self.assertEqual(model.name[:5], directory.name)
                directory.mkdir(parents=True)
                Image.new("RGBA", (5, 8), "red").save(directory / "neutral.png")
                return {"portrait": prefix + "/neutral.png"}

            with patch.object(duo, "bake", side_effect=fake_bake):
                duo.build(cache, models, core, target)
            scene = json.loads((target / "scene-data.js").read_text(encoding="utf8")
                               .removeprefix("window.NEKO_SCENE=").strip().removesuffix(";"))
            project = json.loads((target / "project.json").read_text(encoding="utf8"))
            properties = project["general"]["properties"]
            self.assertTrue((target / project["file"]).is_file())
            self.assertTrue((target / project["preview"]).is_file())
            self.assertEqual({p["value"] for p in properties["background"]["options"]},
                             {b["id"] for b in scene["backgrounds"]})
            for actor in scene["actors"]:
                for key in ("x", "y", "scale"):
                    self.assertEqual(properties[f"p{actor['id']}{key}"]["value"], actor[key])
                self.assertEqual(len(actor["voices"]), 4)
                for file in actor["voices"]:
                    self.assertEqual((target / file).read_bytes(), actor["id"].encode())
            with self.assertRaises(FileExistsError):
                duo.build(cache, models, core, target)

    def test_legacy_image_csv_skips_outside_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp).resolve()
            images = cache / "images"
            images.mkdir()
            (cache / "external.png").touch()
            (images / "garden.png").touch()
            with (images / "index.csv").open("w", newline="", encoding="utf8") as f:
                writer = csv.writer(f)
                writer.writerows([("name", "type", "file"), ("garden", "Sprite", "../external.png"),
                                  ("garden", "Texture2D", "garden.png")])
            self.assertEqual(duo.background_image(cache, "garden"), images / "garden.png")
