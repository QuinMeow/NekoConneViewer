"""Rebuild the embedded Cubism model from its Unity runtime objects."""
import argparse
import json
import math
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools/unity"))
import UnityPy


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


def parse_motion(r, clips):
    original_path = r.string()
    _, _, fade_in, fade_out = r.values("4f")
    ids = [r.string() for _ in range(r.count())]
    curves, nsegments, npoints = [], 0, 0
    if r.count() != len(ids):
        raise ValueError("Mismatched number of animation curves")
    for param in ids:
        keys = [r.values("4fi2f") for _ in range(r.count())]
        r.values("3i")  # infinity modes and rotation order
        if not param.startswith("Param"):
            raise ValueError(f"Unsupported curve target: {param}")
        flat, ns, np = segments(keys)
        curves.append(dict(Target="Parameter", Id=param, Segments=flat))
        nsegments += ns
        npoints += np
    for name in ("FadeInTime", "FadeOutTime"):
        if r.count() != len(ids):
            raise ValueError("Mismatched per-curve fade array")
        for curve in curves:
            v, = r.values("f")
            if v >= 0:
                curve[name] = v
    duration, = r.values("f")
    r.end()
    clip = clips[r.name.removesuffix(".fade")]
    meta = dict(Duration=duration, Fps=clip.m_SampleRate,
                Loop=bool(getattr(clip.m_MuscleClip, "m_LoopTime", False)),
                AreBeziersRestricted=False, FadeInTime=fade_in, FadeOutTime=fade_out,
                CurveCount=len(curves), TotalSegmentCount=nsegments, TotalPointCount=npoints)
    return Path(original_path).name, dict(Version=3, Meta=meta, Curves=curves)


def write_json(path, obj):
    # Older Cubism Native parsers need whitespace between a final number and ']'.
    path.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "exports/builtin/cubism")
    args = ap.parse_args()
    env = UnityPy.load(str(args.bundle))
    assets = next(iter(env.files.values())).files["resources.assets"]
    objs, clips, textures = {}, {}, {}
    for o in assets.objects.values():
        if o.type.name == "MonoBehaviour":
            mono = o.read(check_read=False)
            if mono.m_Name and mono.m_Script.path_id:
                kind = mono.m_Script.deref().peek_name()
                if kind in ("CubismMoc", "CubismFadeMotionData", "CubismExpressionData"):
                    objs.setdefault(kind, []).append(o)
        elif o.type.name == "Texture2D" and re.fullmatch(r"texture_\d\d", o.peek_name() or ""):
            if o.peek_name() in textures:
                raise ValueError("Multiple unrelated texture sets")
            textures[o.peek_name()] = o
        elif o.type.name == "AnimationClip" and (o.peek_name() or "").startswith("motion_"):
            clips[o.peek_name()] = o.read()
    if len(objs.get("CubismMoc", [])) != 1:
        raise ValueError("Expected a single embedded model; refusing ambiguous texture pairing")
    moc = Fields(objs["CubismMoc"][0])
    if not re.fullmatch(r"[\w-]+", moc.name):
        raise ValueError("Invalid model name")
    dest = args.out / moc.name
    dest.mkdir(parents=True, exist_ok=True)
    n = moc.count()
    payload = moc.buf[moc.pos:moc.pos+n]
    moc.pos += n
    moc.end()
    if not payload.startswith(b"MOC3"):
        raise ValueError("Missing Cubism MOC3 signature")
    moc_file = moc.name + ".moc3"
    (dest / moc_file).write_bytes(payload)
    pages, motions, expressions = [], [], []
    if sorted(textures) != ["texture_00", "texture_01"]:
        raise ValueError("Expected two indexed texture pages")
    for tex in sorted(textures):
        filename = tex + ".png"
        textures[tex].read().image.save(dest / filename)
        pages.append(filename)
    for obj in objs["CubismExpressionData"]:
        r = Fields(obj)
        kind = r.string()
        fi, fo = r.values("2f")
        params = []
        for _ in range(r.count()):
            id_ = r.string()
            value, blend = r.values("fi")
            params.append(dict(Id=id_, Value=value, Blend={0:"Overwrite",1:"Add",2:"Multiply"}[blend]))
        r.end()
        if not re.fullmatch(r"[\w-]+\.exp3", r.name) or kind != "Live2D Expression":
            raise ValueError("Unexpected expression")
        file = r.name + ".json"
        write_json(dest / file, dict(Type=kind, FadeInTime=fi, FadeOutTime=fo, Parameters=params))
        expressions.append(dict(Name=r.name.split(".")[0], File=file))
    for obj in objs["CubismFadeMotionData"]:
        file, data = parse_motion(Fields(obj), clips)
        if not re.fullmatch(r"[\w-]+\.motion3\.json", file):
            raise ValueError("Unexpected motion filename")
        write_json(dest / file, data)
        motions.append(dict(File=file))
    motions.sort(key=lambda x:x["File"])
    write_json(dest / (moc.name + ".model3.json"),
               dict(Version=3, FileReferences=dict(Moc=moc_file, Textures=pages, Expressions=expressions,
                                                   Motions={"Idle": motions[:1], "Action": motions[1:]})))
    print(f"{moc.name}: {len(pages)} pages, {len(motions)} motions, {len(expressions)} expressions")


if __name__ == "__main__":
    main()
