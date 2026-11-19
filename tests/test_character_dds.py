import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import resource_names, resource_sources, scan_assets
from tools.import_bundles import Image


class CharacterDdsTests(unittest.TestCase):
    def test_preview_and_export_replace_old_inverted_pngs_but_keep_other_dds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game, cache = root / "game", root / "cache"
            resources = game / "resources"
            resources.mkdir(parents=True)
            bitmap = Image.new("RGBA", (3, 4), "blue")
            bitmap.paste((255, 0, 0, 255), (0, 0, 3, 2))
            dds = io.BytesIO()
            bitmap.save(dds, format="DDS")
            chara = "PC/Images/Chara/chara_icon_log_10101_01_S01_000.dds"
            other = "PC/Images/Other/chara_icon_log_10201_01_S01_000.dds"
            names = root / "names.txt"
            names.write_text(chara + "\n" + other)
            for hidden, logical in (("character", chara), ("elsewhere", other)):
                (resources / hidden).write_bytes(resource_names.decode(dds.getvalue(), logical))
            (resources / "plain.dds").write_bytes(dds.getvalue())

            def refresh():
                scan_assets.catalog(game, cache, listing=names)
                scan_assets.run(game, cache, {"images"}, listing=names)

            def top(file):
                with Image.open(cache / file) as im:
                    return im.convert("RGB").getpixel((1, 0))

            with patch.object(resource_sources, "character_dds", return_value=False):
                refresh()
            index = cache / "index"
            preview = scan_assets.read(index / "catalog.json", {})["characters"]["10101"]["avatars"][0]["file"]
            exported = {r["source"]: r["file"] for r in scan_assets.read(index / "assets.json", [])}
            self.assertEqual(top(preview), (255, 0, 0))
            refresh()
            self.assertEqual(top(preview), (0, 0, 255))
            self.assertEqual(top(exported["resources/character"]), (0, 0, 255))
            for rel in ("resources/plain.dds", "resources/elsewhere"):
                self.assertEqual(top(exported[rel]), (255, 0, 0))
            self.assertEqual(scan_assets.read(index / "report.json", {})["rebuilt"], 1)
            refresh()
            self.assertEqual(scan_assets.read(index / "report.json", {})["reused"], 3)
