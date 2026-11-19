import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools import resource_names, scan_assets
from tools.import_bundles import Image
import viewer_core


class ScanTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.game = self.root / "game"
        self.sources = self.game / "resources"
        self.sources.mkdir(parents=True)
        self.cache = self.root / "cache"

    def make_icon(self, name, color):
        target = self.sources / name
        Image.new("RGBA", (12, 12), color).save(target, format="PNG")
        return target

    def read(self, name):
        return json.loads((self.cache / "index" / name).read_text(encoding="utf8"))

    def test_flat_export_character_provenance_and_only_changed_source_rebuilt(self):
        source = self.make_icon("chara_icon_log_10101_01_S01_000", "red")
        self.make_icon("chara_icon_log_10101_01_S01_001", "blue")
        self.make_icon("chara_icon_plf_1010101", "yellow")
        self.make_icon("tile_10101", "green")
        (self.sources / "not-identified").write_bytes(b"synthetic unknown content")
        scan_assets.run(self.game, self.cache, {"images"})
        self.assertFalse((self.cache / "builtin").exists())
        avatars = self.read("characters.json")
        self.assertEqual(set(avatars), {"10101"})
        self.assertEqual(len(avatars["10101"]["avatars"]), 3)
        self.assertEqual(avatars["10101"]["names"], {})
        self.assertTrue(all(row["source"].startswith("resources/") for row in avatars["10101"]["avatars"]))
        library = viewer_core.library(self.cache)
        self.assertEqual(len(library), 4)
        self.assertEqual(sum(asset.character_id == "10101" for asset in library), 3)
        original = {a.path: (a.path.stat().st_mtime_ns, a.path.read_bytes()) for a in library}
        scan_assets.run(self.game, self.cache, {"images"})
        self.assertEqual(self.read("report.json")["reused"], 4)
        self.assertEqual(original, {a.path: (a.path.stat().st_mtime_ns, a.path.read_bytes()) for a in library})
        self.make_icon(source.name, "purple")
        s = source.stat()
        os.utime(source, ns=(s.st_atime_ns, s.st_mtime_ns + 1000000000))
        scan_assets.run(self.game, self.cache, {"images"})
        report = self.read("report.json")
        self.assertEqual((report["reused"], report["rebuilt"]), (3, 1))
        self.assertEqual([i["status"] for i in report["issues"]], ["unrecognized"])
        self.assertEqual(sum(path.read_bytes() != data for path, (_, data) in original.items()), 1)

    def test_old_cache_untouched(self):
        legacy = self.cache / "builtin"
        legacy.mkdir(parents=True)
        (legacy / "keep").write_bytes(b"old")
        with self.assertRaises(ValueError):
            scan_assets.run(self.game, self.cache, {"images"})
        self.assertEqual((legacy / "keep").read_bytes(), b"old")
        self.assertFalse((self.cache / "index").exists())

    def test_catalog_has_named_avatar_before_any_category_export_and_selection_is_enforced(self):
        first_name = "portraits/chara_icon_log_10101_01_S01_000.png"
        second_name = "portraits/chara_icon_log_10102_01_S01_000.png"
        names = self.root / "names.txt"
        names.write_text(first_name + "\n" + second_name, encoding="utf8")
        for hidden, logical, color in (("hashed-first", first_name, "red"), ("hashed-second", second_name, "blue")):
            p = self.make_icon(hidden, color)
            p.write_bytes(resource_names.decode(p.read_bytes(), logical))
        scan_assets.catalog(self.game, self.cache, listing=names)
        entries = self.read("catalog.json")["characters"]
        self.assertEqual(set(entries), {"10101", "10102"})
        self.assertTrue((self.cache / entries["10101"]["avatars"][0]["file"]).is_file())
        self.assertFalse((self.cache / "images").exists())
        scan_assets.run(self.game, self.cache, {"images"}, ("10102",), listing=names)
        asset, = viewer_core.library(self.cache)
        self.assertEqual(asset.character_id, "10102")
        self.assertEqual(len(list((self.cache / "images").glob("*.png"))), 1)

    def test_default_ignores_other_roles_but_keeps_unassigned_and_scope_accepts_variants(self):
        self.make_icon("chara_icon_log_10101_01_S01_000", "red")
        self.make_icon("chara_thumb_l_1010102_01", "purple")
        self.make_icon("chara_icon_log_20101_01_S01_000", "blue")
        self.make_icon("background", "green")
        scan_assets.run(self.game, self.cache, {"images"})
        self.assertEqual(sorted(a.character_id for a in viewer_core.library(self.cache)), ["", "10101", "10101"])
        separate = self.root / "selected"
        scan_assets.run(self.game, separate, {"images"}, ("1010102",))
        self.assertEqual([a.character_id for a in viewer_core.library(separate)], ["10101", "10101"])
        scan_assets.run(self.game, self.cache, {"images"}, include_other_roles=True)
        self.assertEqual(sorted(a.character_id for a in viewer_core.library(self.cache)), ["", "10101", "10101", "20101"])

    def test_loose_cubism_rebuilds_when_a_referenced_texture_changes(self):
        model = self.sources / "10101.model3.json"
        (self.sources / "model.moc3").write_bytes(b"MOC3fixture")
        page = self.make_icon("texture", "red")
        model.write_text(json.dumps({"FileReferences": {"Moc": "model.moc3", "Textures": ["texture"]}}))
        scan_assets.run(self.game, self.cache, {"live2d"})
        asset, = (a for a in viewer_core.library(self.cache) if a.kind == "cubism")
        target = asset.path.with_name("texture")
        self.assertEqual(target.read_bytes(), page.read_bytes())
        scan_assets.run(self.game, self.cache, {"live2d"})
        self.assertEqual(self.read("report.json")["reused"], 1)
        self.make_icon("texture", "blue")
        s = page.stat()
        os.utime(page, ns=(s.st_atime_ns, s.st_mtime_ns + 1000000000))
        scan_assets.run(self.game, self.cache, {"live2d"})
        self.assertEqual(self.read("report.json")["rebuilt"], 1)
        self.assertEqual(target.read_bytes(), page.read_bytes())
        page.unlink()
        scan_assets.run(self.game, self.cache, {"live2d"})
        self.assertEqual(viewer_core.library(self.cache), [])
