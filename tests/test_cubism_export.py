import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from tools.export_cubism import export
from PIL import Image


def i(number):
    return struct.pack("<i", number)


def string(value):
    raw = value.encode()
    return i(len(raw)) + raw + bytes(-len(raw) % 4)


class Object:
    def __init__(self, unity_file, pid, typ, name, **fields):
        self.assets_file, self.path_id = unity_file, pid
        self.type, self.name = NS(name=typ), name
        self.data = NS(m_Name=name, **fields)
        self.tree = {}
        self.payload = b""
        unity_file.objects[pid] = self

    def read(self, **kwargs):
        return self.data

    def read_typetree(self, **kwargs):
        return self.tree

    def peek_name(self):
        return self.name

    def get_raw_data(self):
        return bytes(28) + string(self.name) + self.payload


def ptr(obj):
    return NS(path_id=obj.path_id, deref=lambda: obj)


def mono(f, pid, kind, name="", **fields):
    script = NS(path_id=1, deref=lambda: NS(peek_name=lambda: kind))
    return Object(f, pid, "MonoBehaviour", name, m_Script=script, **fields)


def model(file_name, name, color, marker, event_id=10):
    f = NS(name=file_name, objects={})
    go = Object(f, 1, "GameObject", name)
    moc = mono(f, 2, "CubismMoc", name)
    payload = b"MOC3" + marker
    moc.payload = i(len(payload)) + payload
    model_component = mono(f, 3, "CubismModel", m_GameObject=ptr(go))
    model_component.tree["_moc"] = ptr(moc)
    texture = Object(f, 4, "Texture2D", "texture_00", image=Image.new("RGB", (1, 1), color))
    renderer = mono(f, 5, "CubismRenderer")
    renderer.tree["_mainTexture"] = ptr(texture)
    # All data names, path IDs and motion event IDs deliberately collide across models.
    expression = mono(f, 6, "CubismExpressionData", "smile.exp3")
    expression.payload = string("Live2D Expression") + struct.pack("<ffi", 1, 1, 0)
    exprs = mono(f, 7, "CubismExpressionList")
    exprs.tree["CubismExpressionObjects"] = [ptr(expression)]
    ctrl = mono(f, 8, "CubismExpressionController")
    ctrl.tree["ExpressionsList"] = ptr(exprs)
    fade = mono(f, 9, "CubismFadeMotionData", "idle.fade")
    fade.payload = (string("mtn/idle.motion3.json") + struct.pack("<4f", 0, 0, 0.1, 0.1)
                    + i(1) + string("ParamX") + i(1) + i(1)
                    + struct.pack("<4fi2f", 0., 0., 0., 0., 0, 0., 0.)
                    + i(0) * 3 + i(1) + struct.pack("<f", -1) + i(1)
                    + struct.pack("<ff", -1, 0.5))
    fades = mono(f, 10, "CubismFadeMotionList")
    fades.tree.update(MotionInstanceIds=[event_id], CubismFadeMotionObjects=[ptr(fade)])
    fade_ctrl = mono(f, 11, "CubismFadeController")
    fade_ctrl.tree["CubismFadeMotionList"] = ptr(fades)
    clip = Object(f, 12, "AnimationClip", "idle", m_SampleRate=30, m_MuscleClip=None,
                  m_Events=[NS(functionName="InstanceId", intParameter=event_id)])
    anim = Object(f, 13, "Animation", "", m_Animations=[ptr(clip)])
    go.data.m_Component = [NS(component=ptr(obj)) for obj in (model_component, renderer, ctrl, fade_ctrl, anim)]
    return f


class CubismExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dest = Path(temporary.name)

    def run_export(self, *files):
        return export(NS(objects=[obj for f in files for obj in f.objects.values()]), self.dest)

    def test_two_models_keep_identically_named_references_separate(self):
        left = model("a.assets", "hero", "red", b"L")
        right = model("b.assets", "hero", "blue", b"R")
        right.objects[12].data.m_SampleRate = 24
        report = self.run_export(left, right)
        self.assertEqual(report["issues"], [])
        left_row, right_row = report["models"]
        self.assertNotEqual(left_row["file"], right_row["file"])
        for row, mark, rgb, fps in [(left_row, b"L", (255, 0, 0), 30), (right_row, b"R", (0, 0, 255), 24)]:
            path = self.dest / row["file"]
            ref = json.loads(path.read_text())["FileReferences"]
            self.assertEqual((path.parent / ref["Moc"]).read_bytes(), b"MOC3" + mark)
            with Image.open(path.parent / ref["Textures"][0]) as im:
                self.assertEqual(im.getpixel((0, 0)), rgb)
            motion = json.loads((path.parent / ref["Motions"]["Idle"][0]["File"]).read_text())
            self.assertEqual(motion["Meta"]["Fps"], fps)
            self.assertEqual(ref["Expressions"], [{"Name": "smile", "File": "smile.exp3.json"}])

    def test_unowned_moc_cannot_borrow_nearby_pages(self):
        a = model("a.assets", "a", "red", b"A")
        del a.objects[3]
        report = self.run_export(a)
        self.assertFalse(report["models"])
        self.assertEqual({r["name"] for r in report["issues"]}, {"a", "smile.exp3", "idle.fade"})
        self.assertTrue(all({"source", "name", "status", "reason"} <= row.keys() for row in report["issues"]))
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_zero_script_guids_cannot_merge_unrelated_component_types(self):
        a = model("stripped", "hero", "red", b"A")
        for obj in a.objects.values():
            obj.serialized_type = NS(script_id=bytes(16))
        report = self.run_export(a)
        self.assertEqual(report["issues"], [])
        self.assertEqual([row["motions"] for row in report["models"]], [1])

    def test_same_root_cannot_assign_one_renderers_pages_to_two_models(self):
        a = model("shared", "hero", "red", b"A")
        other = mono(a, 20, "CubismMoc", "other")
        other.payload = a.objects[2].payload
        component = mono(a, 21, "CubismModel", m_GameObject=ptr(a.objects[1]))
        component.tree["_moc"] = ptr(other)
        a.objects[1].data.m_Component.append(NS(component=ptr(component)))
        report = self.run_export(a)
        self.assertFalse(report["models"])
        self.assertEqual({r["name"] for r in report["issues"] if "uniquely own" in r["reason"]}, {"hero", "other"})

    def test_duplicate_fade_instance_ids_do_not_select_last_binding(self):
        a = model("duplicate", "hero", "red", b"A")
        a.objects[10].tree.update(MotionInstanceIds=[1, 2], CubismFadeMotionObjects=[ptr(a.objects[9])] * 2)
        report = self.run_export(a)
        self.assertEqual(report["models"], [])
        self.assertIn("Conflicting motion instance IDs", report["issues"][0]["reason"])

    def test_rebuild_replaces_removed_motion_without_publishing_user_files(self):
        a = model("a", "hero", "red", b"A")
        old, = self.run_export(a)["models"]
        self.assertIn("hero/idle.motion3.json", old["outputs"])
        a.objects[10].tree.update(MotionInstanceIds=[], CubismFadeMotionObjects=[])
        fresh, = self.run_export(a)["models"]
        self.assertFalse((self.dest / "hero/idle.motion3.json").exists())
        self.assertEqual(set(fresh["outputs"]), {f"hero/{p.name}" for p in (self.dest / "hero").iterdir()})
        personal = self.dest / "hero/notes.txt"
        personal.write_text("mine")
        report = self.run_export(a)
        self.assertFalse(report["models"])
        self.assertIn("Unowned files", report["issues"][0]["reason"])
        self.assertEqual(personal.read_text(), "mine")

    def test_failed_publication_restores_previous_complete_directory(self):
        a = model("a", "hero", "red", b"A")
        self.run_export(a)
        a.objects[2].payload = i(5) + b"MOC3B"
        rename = Path.rename
        def fail_staged(path, dest):
            if path.name == "new":
                raise PermissionError("simulated file lock")
            return rename(path, dest)
        with patch.object(Path, "rename", fail_staged):
            report = self.run_export(a)
        self.assertFalse(report["models"])
        self.assertEqual((self.dest / "hero/hero.moc3").read_bytes(), b"MOC3A")
        self.assertTrue((self.dest / "hero/idle.motion3.json").is_file())
        self.assertEqual([f.name for f in self.dest.iterdir()], ["hero"])

    def test_clipless_variant_exports_curves_and_marks_timing_defaults(self):
        a = model("variant", "cape", "red", b"A")
        a.objects[13].data.m_Animations = []
        report = self.run_export(a)
        self.assertEqual(report["issues"], [])
        row, = report["models"]
        timing, = row["motion_timing_defaults"]
        self.assertEqual((timing["Fps"], timing["Loop"]), (30.0, False))
        data = json.loads((self.dest / "cape" / timing["file"]).read_text())
        self.assertEqual(data["Curves"][0]["Segments"], [0.0, 0.0])
        self.assertEqual(data["Meta"]["Duration"], 0.5)

    def test_fade_slots_use_bound_targets_and_keep_per_curve_fades_aligned(self):
        a = model("cubism.assets", "hero", "red", b"A")
        custom = Object(a, 20, "GameObject", "Ribbon")
        components = [mono(a, 21, "CubismParameter", m_GameObject=ptr(custom)),
                      mono(a, 22, "CubismRenderController")]
        a.objects[1].data.m_Component.extend(NS(component=ptr(c)) for c in components)
        ids = ["Ribbon", "", "Opacity"]
        key = i(1) + struct.pack("<4fi2f", 0, .5, 0, 0, 0, 0, 0) + i(0) * 3
        a.objects[9].payload = (string("idle.motion3.json") + struct.pack("<4f", 0, 0, .1, .1)
                                + i(3) + b"".join(map(string, ids)) + i(3) + key + i(0) * 4 + key
                                + i(3) + struct.pack("<3f", .25, -1, .5)
                                + i(3) + struct.pack("<3f", .75, -1, 1) + struct.pack("<f", .5))
        self.assertEqual(self.run_export(a)["issues"], [])
        data = json.loads((self.dest / "hero/idle.motion3.json").read_text())
        self.assertEqual([(c["Target"], c["Id"], c["FadeInTime"]) for c in data["Curves"]],
                         [("Model", "Opacity", .5), ("Parameter", "Ribbon", .25)])
        self.assertEqual((data["Meta"]["CurveCount"], data["Meta"]["TotalPointCount"]), (2, 2))

    def test_prefab_preload_range_excludes_neighboring_texture_page(self):
        a = model("a.assets", "a", "red", b"A")
        del a.objects[3]
        ids = [2, 4, 7, 10, 12]
        foreign = Object(a, 30, "Texture2D", "texture_00", image=Image.new("RGB", (1, 1), "green"))
        table = [ptr(foreign)] + [ptr(a.objects[n]) for n in ids]
        info = NS(asset=ptr(a.objects[1]), preloadIndex=1, preloadSize=len(ids))
        Object(a, 100, "AssetBundle", "bundle", m_PreloadTable=table, m_Container=[("a.prefab", info)])
        report = self.run_export(a)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["models"][0]["textures"], 1)
        with Image.open(self.dest / "a/texture_00.png") as im:
            self.assertEqual(im.getpixel((0, 0)), (255, 0, 0))
        a.objects[4].name = "background"
        rejected = self.run_export(a)
        self.assertFalse(rejected["models"])
        self.assertIn("no CubismRenderer binding", rejected["issues"][0]["reason"])

    def test_conflicting_page_ordinals_skip_only_affected_model(self):
        a = model("a.assets", "a", "red", b"A")
        b = model("b.assets", "b", "blue", b"B")
        rogue = Object(a, 30, "Texture2D", "texture_00", image=Image.new("RGB", (1, 1), "green"))
        extra = mono(a, 31, "CubismRenderer")
        extra.tree["_mainTexture"] = ptr(rogue)
        a.objects[1].data.m_Component.append(NS(component=ptr(extra)))
        report = self.run_export(a, b)
        self.assertEqual([row["name"] for row in report["models"]], ["b"])
        bad = next(r for r in report["issues"] if r["name"] == "a")
        self.assertIn("Ambiguous texture page", bad["reason"])
        self.assertFalse((self.dest / "a").exists())
