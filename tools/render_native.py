"""Offline motion3 rendering with the Core DLL supplied by the selected game."""
import argparse
from bisect import bisect_left
import json
import math
from pathlib import Path
import subprocess
import tempfile

def cubic(a, b, c, d, t):
    u = 1-t
    return u*u*u*a + 3*u*u*t*b + 3*u*t*t*c + t*t*t*d


class Curve:
    def __init__(self, flat):
        x, y, *rest = flat
        self.initial, self.segments, self.ends = (x, y), [], []
        i = 0
        while i < len(rest):
            kind = rest[i]
            if kind not in (0, 1, 2, 3):
                raise ValueError(f"Unsupported motion segment: {kind}")
            end = i + (7 if kind == 1 else 3)
            points = (x, y, *rest[i+1:end])
            if len(points) != (8 if kind == 1 else 4):
                raise ValueError("Incomplete motion segment")
            x, y = points[-2:]
            if x < points[0]:
                raise ValueError("Unordered motion timeline")
            self.segments.append((kind, points))
            self.ends.append(x)
            i = end

    def sample(self, sec):
        if not self.ends or sec <= self.initial[0]:
            return self.initial[1]
        i = bisect_left(self.ends, sec)
        if i == len(self.segments):
            return self.segments[-1][1][-1]
        kind, p = self.segments[i]
        if sec == p[-2]:
            return p[-1]
        if kind == 0:
            return p[1]+(p[3]-p[1])*(sec-p[0])/(p[2]-p[0])
        if kind == 2:
            return p[1]
        if kind == 3:
            return p[3]
        lo, hi = 0., 1.
        for _ in range(16):
            t = (lo+hi)/2
            if cubic(*p[::2], t) < sec:
                lo = t
            else:
                hi = t
        return cubic(*p[1::2], (lo+hi)/2)


def main():
    from .cubism_gpu import Model, Renderer
    from viewer_core import detect_game
    from viewer_runtime import tool_home
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("model", type=Path)
    p.add_argument("--game", type=Path)
    p.add_argument("--png", type=Path, required=True)
    p.add_argument("--webm", type=Path)
    p.add_argument("--group", default="Idle")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--width", type=int, default=1200)
    p.add_argument("--height", type=int, default=1400)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--scale", type=float, default=.85)
    args = p.parse_args()
    if not (1 <= args.fps <= 120 and 0 < args.scale <= 4 and
            all(2 <= v <= 4096 and v % 2 == 0 for v in (args.width, args.height))):
        p.error("Use even dimensions (2..4096), 1..120 fps and a positive scale up to 4")
    game = args.game or detect_game()
    if not game:
        p.error("Select your installed game directory in the viewer before rendering.")
    core = game / "NekoConne_Data/Plugins/x86_64/Live2DCubismCore.dll"
    base = args.model.resolve().parent
    def reference(name):
        result = (base / name).resolve(strict=True)
        if not result.is_relative_to(base):
            raise ValueError("Model references must stay inside its own directory")
        return result
    files = json.loads(args.model.read_text(encoding="utf-8-sig"))["FileReferences"]
    motion_path = reference(files["Motions"][args.group][args.index]["File"])
    motion = json.loads(motion_path.read_text(encoding="utf-8-sig"))
    duration = float(motion["Meta"]["Duration"])
    if not 0 < duration <= 600 or not math.isfinite(duration):
        p.error("Invalid motion duration")
    model = Model(core, reference(files["Moc"]))
    curves = []
    for curve in motion["Curves"]:
        if curve["Target"] != "Parameter" or curve["Id"] not in model.parameters:
            raise ValueError(f"Unsupported motion target: {curve['Id']}")
        curves.append((model.parameters[curve["Id"]], Curve(curve["Segments"])))
    size = args.width, args.height
    render = Renderer(model, [reference(t) for t in files["Textures"]], size, args.scale)
    frames = max(1, round(duration * args.fps))
    poster = min(round(args.fps/2), frames-1)
    print(f"{motion_path.name}: {duration:.3f}s, {frames} frames, {len(curves)} curves", flush=True)
    args.png.parent.mkdir(parents=True, exist_ok=True)
    if args.webm:
        args.webm.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=(args.webm or args.png).parent) as tmp:
        folder, writer = Path(tmp), None
        if args.webm:
            ffmpeg = tool_home() / "video/ffmpeg.exe"
            if not ffmpeg.is_file():
                ffmpeg = next((tool_home() / "video").rglob("ffmpeg*.exe"), None)
            if not ffmpeg:
                raise FileNotFoundError("The portable FFmpeg component is missing; re-extract the complete release archive.")
            writer = subprocess.Popen([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{size[0]}x{size[1]}", "-r", str(args.fps),
                "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "24",
                "-b:v", "0", "-row-mt", "1", "-cpu-used", "4", "-threads", "4", "-auto-alt-ref", "0",
                str(folder / "motion.webm")], stdin=subprocess.PIPE)
        try:
            for i in (range(frames) if writer else [poster]):
                model.apply((idx, curve.sample(i / args.fps)) for idx, curve in curves)
                image = render.frame()
                if i == poster:
                    if not image.getchannel("A").getbbox():
                        raise RuntimeError("Blank model frame")
                    image.save(folder / "poster.png")
                if writer:
                    writer.stdin.write(image.tobytes())
        finally:
            if writer:
                writer.stdin.close()
                code = writer.wait()
                if code:
                    raise RuntimeError(f"FFmpeg failed (exit {code})")
        if writer:
            (folder / "motion.webm").replace(args.webm)
        (folder / "poster.png").replace(args.png)


if __name__ == "__main__":
    main()
