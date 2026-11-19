import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import scan_assets
from tools.import_bundles import Image


class ParallelScanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.game = self.root / "game"
        self.resources = self.game / "resources"
        self.resources.mkdir(parents=True)

    def test_workers_overlap_but_manifests_and_asset_contents_are_identical(self):
        for i, color in enumerate(("red", "green", "blue", "white")):
            folder = self.resources / str(i)
            folder.mkdir()
            Image.new("RGB", (3, 2), color).save(folder / "same.png")
        serial, parallel = self.root / "serial", self.root / "parallel"
        scan_assets.run(self.game, serial, {"images"}, jobs=1)
        real_unpack = scan_assets.unpack
        gate = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        workspaces = set()
        active = peak = 0
        def tracked(source, out, selected, workspace, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                workspaces.add(workspace)
            try:
                gate.wait()
                return real_unpack(source, out, selected, workspace, *args)
            finally:
                with lock:
                    active -= 1
        with patch.object(scan_assets, "unpack", side_effect=tracked):
            scan_assets.run(self.game, parallel, {"images"}, jobs=2)
        self.assertEqual((peak, len(workspaces)), (2, 4))
        for file in ("assets.json", "characters.json", "report.json", "scan.json"):
            self.assertEqual((serial / "index" / file).read_bytes(), (parallel / "index" / file).read_bytes())
        def images(cache):
            return {p.name: p.read_bytes() for p in (cache / "images").iterdir()}
        self.assertEqual(images(serial), images(parallel))
        self.assertEqual(len(images(parallel)), 4)

    def fake_bundle(self, source, out, *args):
        data = source.path.read_bytes()
        folder = out / "images"
        folder.mkdir(parents=True)
        assets = []
        for name in ("a.png", "b.png"):
            (folder / name).write_bytes(data)
            assets.append(dict(kind="image", title=name, source=source.relative, file=f"images/{name}"))
        return {"images": {"assets": assets, "issues": []}}

    def test_publication_failure_rolls_back_whole_source_and_can_retry(self):
        p = self.resources / "source"
        old, new = b"UnityFS\0old", b"UnityFS\0changed"
        p.write_bytes(old)
        cache = self.root / "out"
        rename = Path.rename
        def fail_last(path, dst):
            if path.name == "b.png" and path.parent.parent.name == "out" and dst == cache / "images/b.png":
                raise PermissionError("simulated rename failure")
            return rename(path, dst)
        with patch.object(scan_assets, "unpack", side_effect=self.fake_bundle):
            scan_assets.run(self.game, cache, {"images"})
            p.write_bytes(new)
            with patch.object(Path, "rename", fail_last):
                scan_assets.run(self.game, cache, {"images"})
            status = json.loads((cache / "index/report.json").read_text())
            self.assertIn("simulated rename failure", status["issues"][0]["reason"])
            self.assertEqual([f.read_bytes() for f in (cache / "images").iterdir()], [old, old])
            scan_assets.run(self.game, cache, {"images"})
        self.assertEqual([f.read_bytes() for f in (cache / "images").iterdir()], [new, new])

    def test_two_sources_cannot_publish_the_same_output(self):
        (self.resources / "aa").write_bytes(b"UnityFS\0first")
        (self.resources / "bb").write_bytes(b"UnityFS\0second")
        cache = self.root / "out"
        with patch.object(scan_assets, "unpack", side_effect=self.fake_bundle):
            scan_assets.run(self.game, cache, {"images"}, jobs=2)
        self.assertEqual((cache / "images/a.png").read_bytes(), b"UnityFS\0first")
        rows = json.loads((cache / "index/assets.json").read_text())
        self.assertEqual({row["source"] for row in rows}, {"resources/aa"})
        issue, = json.loads((cache / "index/report.json").read_text())["issues"]
        self.assertEqual(issue["source"], "resources/bb")
        self.assertIn("Conflicting", issue["reason"])

    def test_missing_staged_dependency_does_not_overwrite_existing_model(self):
        cache, stage = self.root / "out", self.root / "stage"
        model = "live2d/hero/hero.model3.json"
        texture = "live2d/hero/page.png"
        for root, data in ((cache, b"old model"), (stage, b"new model")):
            (root / model).parent.mkdir(parents=True)
            (root / model).write_bytes(data)
        (cache / texture).write_bytes(b"old texture")
        rows = {"live2d": {"assets": [{"file": model, "outputs": [model, texture]}]}}
        with self.assertRaisesRegex(ValueError, "Staged files do not match"):
            scan_assets.publish_source(cache, stage, rows, "source", {}, rows)
        self.assertEqual((cache / model).read_bytes(), b"old model")
        self.assertEqual((cache / texture).read_bytes(), b"old texture")

    def test_temporarily_filtered_file_keeps_ownership_for_later_rebuild(self):
        source = self.resources / "bundle"
        cache = self.root / "out"
        def subset(src, out, *args):
            result = self.fake_bundle(src, out, *args)
            if src.path.read_bytes().endswith(b"skip"):
                result["images"]["assets"].pop()
            return result
        with patch.object(scan_assets, "unpack", side_effect=subset):
            for payload in (b"1", b"skip", b"updated"):
                source.write_bytes(b"UnityFS\0" + payload)
                scan_assets.run(self.game, cache, {"images"})
        self.assertEqual((cache / "images/b.png").read_bytes(), b"UnityFS\0updated")
        self.assertFalse(json.loads((cache / "index/report.json").read_text())["issues"])

    def test_invalid_workers_do_not_create_a_cache(self):
        cache = self.root / "out"
        for jobs in (0, 5):
            with self.subTest(jobs=jobs), self.assertRaisesRegex(ValueError, "jobs"):
                scan_assets.run(self.game, cache, {"images"}, jobs=jobs)
        self.assertFalse(cache.exists())
