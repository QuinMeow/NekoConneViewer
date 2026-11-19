import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import viewer_core as core


class LibraryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cache = self.root / "cache"
        self.model = self.cache / "live2d/C/C.model3.json"
        self.model.parent.mkdir(parents=True)
        self.model.write_text(json.dumps({"FileReferences": {"Motions": {"Idle": [{"File":"idle.motion3.json"}]}}}))
        (self.model.parent / "texture_00.png").write_bytes(b"page")
        self.index = self.cache / "index" / "assets.json"
        self.index.parent.mkdir()
        self.index.write_text(json.dumps([dict(kind="cubism", title="C", category="live2d",
                                               file=self.model.relative_to(self.cache).as_posix())]))

    def test_model_export_keeps_siblings_and_never_overwrites(self):
        asset, = core.library(self.cache)
        self.assertEqual(asset.kind, "cubism")
        copied = core.copy_asset(asset, self.root / "copies")
        self.assertEqual((copied / "texture_00.png").read_bytes(), b"page")
        with self.assertRaises(FileExistsError):
            core.copy_asset(asset, self.root / "copies")
        with self.assertRaises(FileExistsError):
            core.copy_asset(asset, self.model.parent / "nested")

    def test_motion_selection_matches_exported_metadata(self):
        asset, = core.library(self.cache)
        self.assertEqual(core.motion_choices(asset), {"Idle / idle.motion3.json": ("Idle", 0)})
        command = core.render_command(asset, self.cache, "Idle", 0)
        self.assertTrue(any(Path(value).name == self.model.name and Path(value).samefile(self.model) for value in command))
        with self.assertRaises(ValueError):
            core.render_command(asset, self.cache, "Idle", 12)

    def test_spine_choices_are_derived_from_skin_and_animation_ids(self):
        anchor = self.cache / "spine/S/S.skel"
        anchor.parent.mkdir(parents=True)
        anchor.touch()
        (self.index.parent / "assets-import.json").write_text(json.dumps([dict(
            kind="spine", title="S", category="spine", file=anchor.relative_to(self.cache).as_posix())]))
        meta = self.cache / "renders/spine/S/spine.json"
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps(dict(skins=["coat", "default"], animations=[{"name": "wave"}])))
        asset = next(a for a in core.library(self.cache) if a.kind == "spine")
        self.assertEqual(len(core.motion_choices(asset)), 2)
        cmd = core.render_command(asset, self.cache, "coat", 0)
        self.assertEqual(cmd[cmd.index("--animation")+1], "wave")
        self.assertEqual(cmd[cmd.index("--skin")+1], "coat")

    def test_export_cache_cannot_target_game_tree(self):
        game = self.root / "game"
        with self.assertRaises(ValueError):
            core.scan_command(game, game / "resources", ["images"])

    def test_duplicate_and_unsafe_audio_rows_not_exposed(self):
        directory = self.cache / "index/audio"
        directory.mkdir(parents=True)
        (directory / "decoded-test.csv").write_text("cue,wav\nhello,wav/a.wav\nrepeat,wav/a.wav\nbad,../escape.wav\n")
        matches = [a for a in core.library(self.cache) if a.kind == "audio"]
        self.assertEqual([a.title for a in matches], ["hello"])

    def test_video_library_uses_the_current_manifest(self):
        video = self.cache / "video"
        video.mkdir()
        for name in ("current", "old"):
            (video / (name + ".webm")).touch()
        index = self.cache / "index/video"
        index.mkdir()
        (index / "index.csv").write_text("file,preview\ncurrent.webm,\n", encoding="utf8")
        self.assertEqual([a.title for a in core.library(self.cache) if a.kind == "video"], ["current"])


if __name__ == "__main__":
    unittest.main()
