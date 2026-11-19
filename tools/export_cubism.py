"""Rebuild the embedded Cubism model from its Unity runtime objects."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
import struct
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools/unity"))
import UnityPy
from UnityPy.classes.PPtr import PPtr


class Fields:
    """Little-endian Unity player serialization (this build has no custom typetrees)."""
    def __init__(self, obj):
        self.buf = obj.get_raw_data()
        self.pos = 28  # PPtr<GameObject>, enabled + padding, PPtr<MonoScript>
        self.name = self.string()
        if self.name != obj.read(check_read=False).m_Name:
            raise ValueError("Unsupported MonoBehaviour layout")

    def values(self, fmt):
        result = struct.unpack_from("<" + fmt, self.buf, self.pos)
        self.pos += struct.calcsize("<" + fmt)
        return result

    def count(self):
        n, = self.values("i")
        if not 0 <= n <= len(self.buf) - self.pos:
            raise ValueError(f"Invalid count {n}")
        return n

    def string(self):
        n = self.count()
        start = self.pos
        self.pos += (n + 3) & ~3
        return self.buf[start:start+n].decode("utf8")

    def end(self):
        if self.pos != len(self.buf):
            raise ValueError(f"{self.name}: unexpected trailing data")


def segments(keys):
    if not keys:
        raise ValueError("Empty curve")
    flat, points = list(keys[0][:2]), 1
    for a, b in zip(keys, keys[1:]):
        t, v, _, out_slope, flags_a, _, out_weight = a
        t1, v1, in_slope, _, flags_b, in_weight, _ = b
        dt = t1 - t
        if dt <= 0:
            raise ValueError("Unordered keyframes")
        if not math.isfinite(out_slope) or not math.isfinite(in_slope):
            flat.extend([2, t1, v1])
            points += 1
        else:
            d0 = dt * (out_weight if flags_a & 2 else 1/3)
            d1 = dt * (in_weight if flags_b & 1 else 1/3)
            flat.extend([1, t+d0, v+out_slope*d0, t1-d1, v1-in_slope*d1, t1, v1])
            points += 3
    return flat, len(keys)-1, points


def parse_motion(r, clip, targets):
    original_path = r.string()
    _, _, fade_in, fade_out = r.values("4f")
    ids = [r.string() for _ in range(r.count())]
    slots, nsegments, npoints = [], 0, 0
    if r.count() != len(ids):
        raise ValueError("Mismatched number of animation curves")
    for param in ids:
        keys = [r.values("4fi2f") for _ in range(r.count())]
        r.values("3i")  # infinity modes and rotation order
        if not param and not keys:
            slots.append(None)  # Unity serializes empty slots in some fade lists.
            continue
        target = targets.get(param, "Parameter" if param.startswith("Param") else None)
        if not target:
            raise ValueError(f"Unsupported curve target: {param}")
        flat, ns, np = segments(keys)
        slots.append(dict(Target=target, Id=param, Segments=flat))
        nsegments += ns
        npoints += np
    for name in ("FadeInTime", "FadeOutTime"):
        if r.count() != len(ids):
            raise ValueError("Mismatched per-curve fade array")
        for curve in slots:
            v, = r.values("f")
            if v >= 0 and curve is not None:
                curve[name] = v
    duration, = r.values("f")
    r.end()
    # Cubism Native walks the Model, Parameter, and PartOpacity groups in this order.
    order = {"Model": 0, "Parameter": 1, "PartOpacity": 2}
    curves = sorted((c for c in slots if c is not None), key=lambda c: order[c["Target"]])
    meta = dict(Duration=duration, Fps=clip.m_SampleRate if clip is not None else 30.0,
                Loop=bool(clip is not None and getattr(clip.m_MuscleClip, "m_LoopTime", False)),
                AreBeziersRestricted=False, FadeInTime=fade_in, FadeOutTime=fade_out,
                CurveCount=len(curves), TotalSegmentCount=nsegments, TotalPointCount=npoints)
    return Path(original_path.replace("\\", "/")).name, dict(Version=3, Meta=meta, Curves=curves)


def write_json(path, obj):
    # Older Cubism Native parsers need whitespace between a final number and ']'.
    path.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf8")


def expression(obj):
    r = Fields(obj)
    kind = r.string()
    fi, fo = r.values("2f")
    params = []
    for _ in range(r.count()):
        param = r.string()
        value, blend = r.values("fi")
        params.append(dict(Id=param, Value=value, Blend={0: "Overwrite", 1: "Add", 2: "Multiply"}[blend]))
    r.end()
    if not re.fullmatch(r"[\w-]+\.exp3", r.name) or kind != "Live2D Expression":
        raise ValueError("Unsupported expression")
    return r.name + ".json", dict(Type=kind, FadeInTime=fi, FadeOutTime=fo, Parameters=params)


# Layout hashes from the shipped player, for bundles stripped of their custom typetrees.
# Each offset below is relative to the end of the common MonoBehaviour header.
POINTER_FIELDS = {
    "CubismModel": ("_moc", 0, "56d7eb142504c23a05c54afba4195ca3"),
    "CubismRenderer": ("_mainTexture", 60, "7f5eaace693291078cd734aa564456f9"),
    "CubismFadeController": ("CubismFadeMotionList", 0, "8ac8eea35792f36f69ab06286720e33e"),
    "CubismExpressionController": ("ExpressionsList", 0, "054323784ea9cbbc6a9ad120d783d225"),
}
MODEL_BINDINGS = {"CubismRenderController": "Opacity", "CubismEyeBlinkController": "EyeBlink",
                  "CubismMouthController": "LipSync"}
COMPONENT_ASSETS = {"CubismRenderer": "Texture2D", "CubismFadeController": "CubismFadeMotionList",
                    "CubismExpressionController": "CubismExpressionList"}
READ_ERRORS = (AttributeError, IndexError, KeyError, OSError, TypeError, ValueError, RuntimeError, struct.error)


def identity(obj):
    return id(obj.assets_file), obj.path_id


def source(obj):
    return Path(obj.assets_file.name).name


def object_name(obj):
    try:
        return obj.peek_name() or ""
    except READ_ERRORS:
        return ""


def deref(owner, ptr):
    if isinstance(ptr, dict):
        ptr = PPtr(m_FileID=ptr["m_FileID"], m_PathID=ptr["m_PathID"], assetsfile=owner.assets_file)
    return ptr.deref() if ptr and ptr.path_id else None


def raw_layout(obj, expected):
    if getattr(obj.serialized_type, "old_type_hash", b"").hex() != expected:
        raise ValueError("Unknown stripped layout; a serialized typetree is needed")
    return Fields(obj)


def raw_pointer(obj, reader):
    file_id, path_id = reader.values("iq")
    return deref(obj, {"m_FileID": file_id, "m_PathID": path_id})


def component_ref(obj, kind):
    name, offset, layout = POINTER_FIELDS[kind]
    tree = obj.read_typetree(check_read=False)
    if name in tree:
        return deref(obj, tree[name])
    reader = raw_layout(obj, layout)
    reader.pos += offset
    return raw_pointer(obj, reader)


def list_refs(obj, fade):
    field = "CubismFadeMotionObjects" if fade else "CubismExpressionObjects"
    tree = obj.read_typetree(check_read=False)
    if field in tree:
        refs = [deref(obj, p) for p in tree[field]]
        ids = tree["MotionInstanceIds"] if fade else [None] * len(refs)
    else:
        r = raw_layout(obj, "b80167ff4b11aa88d16f210bb3dd69ac" if fade else "7fbfd0cc934ffe6de3629d29d8c568d0")
        ids = [r.values("i")[0] for _ in range(r.count())] if fade else None
        refs = [raw_pointer(obj, r) for _ in range(r.count())]
        r.end()
        if ids is None:
            ids = [None] * len(refs)
    if len(ids) != len(refs) or any(ref is None for ref in refs):
        raise ValueError(f"Invalid {field} references")
    return zip(ids, refs)


class ScriptKinds:
    def __init__(self):
        self.types = {}

    def __call__(self, obj):
        if obj.type.name != "MonoBehaviour":
            return obj.type.name
        # In stripped players thousands of components share one serialized script type.
        tag = getattr(getattr(obj, "serialized_type", None), "script_id", None)
        key = (id(obj.assets_file), tag) if tag and any(tag) else identity(obj)
        if key not in self.types:
            ptr = obj.read(check_read=False).m_Script
            self.types[key] = ptr.deref().peek_name() if ptr.path_id else None
        return self.types[key]

    def objects(self, env):
        for obj in env.objects:
            try:
                typ = self(obj)
            except READ_ERRORS:
                continue  # Unresolved MonoScripts cannot be classified as Cubism.
            yield typ, obj


class Dependencies:
    def __init__(self):
        self.textures, self.expressions, self.fades = {}, {}, {}
        self.clips = defaultdict(dict)
        self.fade_ids = {}
        self.targets = {}

    def bind(self, name, target):
        self.targets[name] = target if self.targets.get(name, target) == target else None

    def collect(self, obj, kind):
        k = kind(obj)
        if k == "Texture2D":
            self.textures[identity(obj)] = obj
        elif k in MODEL_BINDINGS:
            self.bind(MODEL_BINDINGS[k], "Model")
        elif k in {"CubismParameter", "CubismPart"}:
            name = obj.read(check_read=False).m_GameObject.deref().peek_name()
            target = "Parameter" if k == "CubismParameter" else "PartOpacity"
            self.bind(name, target)
        elif k == "AnimationClip":
            for e in obj.read().m_Events:
                if e.functionName == "InstanceId":
                    self.clips[e.intParameter][identity(obj)] = obj
        elif k in {"CubismFadeMotionList", "CubismExpressionList"}:
            fade = k == "CubismFadeMotionList"
            target = self.fades if fade else self.expressions
            for token, ref in list_refs(obj, fade):
                if kind(ref) != k.removesuffix("List") + "Data":
                    raise ValueError(f"Unexpected {k} entry type")
                key = identity(ref)
                if fade and (self.fade_ids.setdefault(token, key) != key
                             or (key in target and target[key][1] != token)):
                    raise ValueError(f"Conflicting motion instance IDs in {k}")
                target[key] = (ref, token)


def dependency_graph(root, kind):
    deps = Dependencies()
    seen = set()
    nodes = [root]
    while nodes:
        go = nodes.pop()
        if identity(go) in seen:
            continue
        seen.add(identity(go))
        comps = [ref.component.deref() for ref in go.read().m_Component if ref.component.path_id]
        kinds = [(kind(comp), comp) for comp in comps]
        model_count = sum(k == "CubismModel" for k, _ in kinds)
        if go is root and model_count != 1:
            raise ValueError("Model components do not uniquely own the root GameObject")
        if go is not root and model_count:
            continue  # A nested model owns its subtree.
        for k, comp in kinds:
            if k in MODEL_BINDINGS or k in {"CubismParameter", "CubismPart"}:
                deps.collect(comp, kind)
            elif k in {"Transform", "RectTransform"}:
                for child in comp.read().m_Children:
                    if child.path_id:
                        nodes.append(child.deref().read().m_GameObject.deref())
            elif k in COMPONENT_ASSETS:
                linked = component_ref(comp, k)
                if linked is not None:
                    if kind(linked) != COMPONENT_ASSETS[k]:
                        raise ValueError(f"{k} does not reference {COMPONENT_ASSETS[k]}")
                    deps.collect(linked, kind)
            elif k in {"Animator", "Animation"}:
                data = comp.read()
                if k == "Animator":
                    if not data.m_Controller.path_id:
                        continue
                    controller = data.m_Controller.deref()
                    if kind(controller) != "AnimatorController":
                        raise ValueError("Unsupported animation controller")
                    refs = controller.read().m_AnimationClips
                else:
                    refs = data.m_Animations
                for ptr in refs:
                    if ptr.path_id:
                        deps.collect(ptr.deref(), kind)
    return deps


def preload_graphs(bundle, kind, wanted, scopes):
    table = bundle.read()
    for path, entry in table.m_Container:
        if not path.lower().endswith((".prefab", ".moc3", ".moc3.asset")):
            continue
        primary = deref(bundle, entry.asset)
        expected = "GameObject" if path.lower().endswith(".prefab") else "CubismMoc"
        if primary is None or kind(primary) != expected:
            continue
        start, size = entry.preloadIndex, entry.preloadSize
        if start < 0 or size < 0 or start + size > len(table.m_PreloadTable):
            raise ValueError(f"Invalid preload range: {path}")
        members = {identity(primary): primary}
        for ptr in table.m_PreloadTable[start:start + size]:
            if ptr.path_id:
                obj = ptr.deref()
                members[identity(obj)] = obj
        mocs = [key for key, obj in members.items() if kind(obj) == "CubismMoc"]
        if len(mocs) == 1 and mocs[0] in wanted:
            # The table records dependencies of this entry, not of the entire bundle.
            # Ignore a bare MOC entry that provides no information about its pages.
            if any(obj.type.name == "Texture2D" for obj in members.values()):
                scopes[mocs[0]][frozenset(members)] = members


def model_files(moc, graph, dest):
    textures = graph.textures
    name = moc.peek_name()
    r = Fields(moc)
    size = r.count()
    payload = r.buf[r.pos:r.pos + size]
    r.pos += size
    r.end()
    if not payload.startswith(b"MOC3"):
        raise ValueError("Missing MOC3 signature")
    moc_file = name + ".moc3"
    (dest / moc_file).write_bytes(payload)
    pages = {}
    for tex in textures.values():
        match = re.fullmatch(r"texture_(\d+)", tex.peek_name() or "", re.I)
        if not match and len(textures) != 1:
            raise ValueError("Ambiguous texture page order")
        index = int(match[1]) if match else 0
        if index in pages:
            raise ValueError(f"Ambiguous texture page {index}")
        pages[index] = tex
    if not pages or sorted(pages) != list(range(len(pages))):
        raise ValueError("Missing referenced texture pages")
    refs = dict(Moc=moc_file, Textures=[], Expressions=[], Motions={})
    for index, tex in sorted(pages.items()):
        filename = f"texture_{index:02d}.png"
        tex.read().image.save(dest / filename)
        refs["Textures"].append(filename)
    motions, defaults = [], []
    for obj, _ in graph.expressions.values():
        filename, data = expression(obj)
        if (dest / filename).exists():
            raise ValueError(f"Duplicate expression filename: {filename}")
        write_json(dest / filename, data)
        refs["Expressions"].append(dict(Name=filename.removesuffix(".exp3.json"), File=filename))
    for obj, token in graph.fades.values():
        candidates = list(graph.clips[token].values())
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous AnimationClip for {obj.peek_name()}")
        clip = candidates[0].read() if candidates else None
        filename, data = parse_motion(Fields(obj), clip, graph.targets)
        if not re.fullmatch(r"[\w-]+\.motion3\.json", filename) or (dest / filename).exists():
            raise ValueError(f"Invalid or duplicate motion filename: {filename}")
        write_json(dest / filename, data)
        motions.append(dict(File=filename))
        if clip is None:
            defaults.append(dict(file=filename, Fps=30.0, Loop=False, reason="No matching AnimationClip; export timing defaults"))
    if motions:
        motions.sort(key=lambda m: m["File"])
        refs["Motions"] = {"Idle": motions[:1], "Action": motions[1:]}
    write_json(dest / (name + ".model3.json"), dict(Version=3, FileReferences=refs))
    stats = dict(textures=len(pages), motions=len(motions), expressions=len(graph.expressions))
    if defaults:
        stats["motion_timing_defaults"] = defaults
    return stats


def previous_model_files(folder, name):
    """Recognize only the flat, fully referenced layout produced by this exporter."""
    if folder.resolve() != folder.parent.resolve() / folder.name or not folder.is_dir():
        raise ValueError(f"Refusing to replace non-model destination: {folder.name}")
    entries = list(folder.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in entries):
        raise ValueError(f"Unowned entries in existing model directory: {folder.name}")
    manifest = name + ".model3.json"
    data = json.loads((folder / manifest).read_text(encoding="utf8"))
    refs = data["FileReferences"]
    if (set(data) != {"Version", "FileReferences"} or data["Version"] != 3
            or set(refs) != {"Moc", "Textures", "Expressions", "Motions"} or refs["Moc"] != name + ".moc3"):
        raise ValueError(f"Unrecognized existing model directory: {folder.name}")
    owned = {manifest, refs["Moc"]}
    groups = ((refs["Textures"], r"texture_\d+\.png"),
              ((e["File"] for e in refs["Expressions"]), r"[\w-]+\.exp3\.json"),
              ((m["File"] for group in refs["Motions"].values() for m in group), r"[\w-]+\.motion3\.json"))
    for filenames, pattern in groups:
        for filename in filenames:
            if not re.fullmatch(pattern, filename):
                raise ValueError(f"Unrecognized existing model reference: {filename!r}")
            owned.add(filename)
    stray = {entry.name for entry in entries} - owned
    if stray:
        raise ValueError(f"Unowned files in {folder.name}: {', '.join(sorted(stray))}")
    return [entry.name for entry in entries]


def publish_model(staged, dest, name):
    if not dest.exists() and not dest.is_symlink():
        staged.rename(dest)
        return
    owned = previous_model_files(dest, name)
    # Windows cannot replace a nonempty directory. Keep the backup outside the
    # auto-cleaned staging area so even a failed rollback cannot erase the last model.
    backup = dest.with_name(".previous-" + uuid4().hex)
    dest.rename(backup)
    try:
        staged.rename(dest)
    except BaseException:
        backup.rename(dest)
        raise
    try:
        for filename in owned:
            (backup / filename).unlink()
        backup.rmdir()
    except OSError:
        return f"Previous export retained in {backup.name}; new model is complete"


def catalog(env):
    for typ, obj in ScriptKinds().objects(env):
        if typ == "CubismMoc" and (name := object_name(obj)):
            yield name


def export(env, out, *, accept=None, source_id=""):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    models, issues = [], []
    kind = ScriptKinds()
    mocs, data, owners, bundles = [], {}, defaultdict(list), []

    def issue(obj, reason):
        issues.append(dict(source=source(obj), name=object_name(obj) or kind(obj), path_id=obj.path_id,
                           status="skipped", reason=str(reason)))

    for typ, obj in kind.objects(env):
        if typ == "AssetBundle":
            bundles.append(obj)
        elif typ == "CubismMoc":
            mocs.append(obj)
        elif typ in {"CubismExpressionData", "CubismFadeMotionData"}:
            data[identity(obj)] = obj
        elif typ == "CubismModel":
            try:
                moc = component_ref(obj, typ)
                if moc is None or kind(moc) != "CubismMoc":
                    raise ValueError("Missing model MOC reference")
                owners[identity(moc)].append(obj.read(check_read=False).m_GameObject.deref())
            except READ_ERRORS as exc:
                issue(obj, exc)
    names = Counter(object_name(m).casefold() for m in mocs)
    wanted = {identity(m) for m in mocs} - owners.keys()
    scopes = defaultdict(dict)
    if wanted:
        for bundle in bundles:
            try:
                preload_graphs(bundle, kind, wanted, scopes)
            except READ_ERRORS as exc:
                issue(bundle, exc)
    used = set()
    # A CAB basename and path ID can recur in distinct files loaded in one environment.
    reserved = set(names)
    for moc in mocs:
        name = object_name(moc)
        if accept is not None and not accept(name):
            continue
        try:
            if not re.fullmatch(r"[\w-]+", name) or Path(name).is_reserved():
                raise ValueError("Invalid model name")
            roots = owners[identity(moc)]
            if len(roots) == 1:
                graph = dependency_graph(roots[0], kind)
            elif not roots and len(scopes[identity(moc)]) == 1:
                members = next(iter(scopes[identity(moc)].values()))
                graph = Dependencies()
                for obj in members.values():
                    if obj.type.name == "Texture2D" and not re.fullmatch(r"texture_\d+", obj.peek_name() or "", re.I):
                        raise ValueError("Unindexed preload texture has no CubismRenderer binding")
                    graph.collect(obj, kind)
            else:
                raise ValueError("Missing or ambiguous model root / prefab dependencies")
            folder = name
            if source_id or names[name.casefold()] > 1:
                stamp = hashlib.sha256(f"{source_id}:{source(moc)}:{moc.path_id}".encode()).hexdigest()[:12]
                base = folder = name + "-" + stamp
                suffix = 2
                while folder.casefold() in reserved:
                    folder = f"{base}-{suffix}"
                    suffix += 1
                reserved.add(folder.casefold())
            with tempfile.TemporaryDirectory(dir=out) as stage:
                staged = Path(stage) / "new"
                staged.mkdir()
                stats = model_files(moc, graph, staged)
                stats["outputs"] = sorted(f"{folder}/{p.name}" for p in staged.iterdir())
                if warning := publish_model(staged, out / folder, name):
                    stats["warning"] = warning
            used.update(graph.expressions)
            used.update(graph.fades)
            models.append(dict(name=name, path_id=moc.path_id, source=source(moc),
                               file=f"{folder}/{name}.model3.json", **stats))
        except READ_ERRORS as exc:
            issue(moc, exc)
    if accept is None:
        for key, obj in data.items():
            if key not in used:
                issue(obj, "No successfully exported model references this asset")
    return {"models": models, "issues": issues}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "exports/live2d")
    args = ap.parse_args()
    report = export(UnityPy.load(str(args.bundle)), args.out)
    with tempfile.TemporaryDirectory(dir=args.out) as tmp:
        path = Path(tmp) / "manifest.json"
        write_json(path, report)
        path.replace(args.out / path.name)
    print(f"{len(report['models'])} model(s), {len(report['issues'])} issue(s)")


if __name__ == "__main__":
    main()
