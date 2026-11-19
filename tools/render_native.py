"""Offline motion3 rendering with the Core DLL supplied by the selected game."""
import argparse
from bisect import bisect_left
from contextlib import ExitStack, contextmanager, nullcontext
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


def motion_tracks(curves, parameters):
    tracks, opacity = [], None
    for curve in curves:
        channel = curve["Target"], curve["Id"]
        if channel == ("Model", "Opacity"):
            if opacity is not None:
                raise ValueError("Duplicate Model/Opacity curve")
            opacity = Curve(curve["Segments"])
        elif channel[0] == "Parameter" and channel[1] in parameters:
            tracks.append((parameters[channel[1]], Curve(curve["Segments"])))
        else:
            raise ValueError(f"Unsupported motion target: {channel[0]}/{channel[1]}")
    return tracks, opacity


def apply_model_opacity(image, value):
    if not math.isfinite(value):
        raise ValueError("Nonfinite model opacity")
    # GPU compositing and unpremultiplication are complete. On straight RGBA,
    # attenuate only alpha, once for the whole model, before adding a backdrop.
    if value < 1:
        image.putalpha(image.getchannel("A").point(lambda a: round(a * max(0, value))))


def fit_view(bounds, size, fill):
    x0, y0, x1, y1 = bounds
    if not (x1 > x0 and y1 > y0) or not all(map(math.isfinite, bounds)):
        raise ValueError("Invalid model bounds")
    return ((x0+x1)/2, (y0+y1)/2, min(size[0]/(x1-x0), size[1]/(y1-y0))*fill)


def world_box(box, view, size):
    # The raster has a top-left origin; Core's coordinates have a bottom-left
    # origin. Keep one probe texel around the union for subpixel coverage.
    x, y, px = view
    hw, hh = size[0]/2, size[1]/2
    l, t, r, b = box
    return (x+(l-1-hw)/px, y+(hh-b-1)/px, x+(r+1-hw)/px, y+(hh-t+1)/px)


def motion_bounds(renderer, apply_pose, frames):
    union = None
    for i in range(frames):
        if apply_pose(i) <= 0:
            continue
        geometry = renderer.geometry_bounds()
        if not geometry or geometry[0] >= geometry[2] or geometry[1] >= geometry[3]:
            continue
        view = fit_view(geometry, renderer.size, .98)
        renderer.set_view(view)
        box = renderer.frame(alpha_only=True).getbbox()
        if box:
            x0, y0, x1, y1 = world_box(box, view, renderer.size)
            union = ((min(union[0], x0), min(union[1], y0), max(union[2], x1), max(union[3], y1))
                     if union else (x0, y0, x1, y1))
    if not union:
        raise ValueError("The motion contains no visible character pixels")
    return union


def make_background(path, size):
    from PIL import Image, ImageOps
    canvas = Image.new("RGB", size, (29, 38, 48))
    if path:
        with Image.open(path) as source:
            image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGBA"), size,
                                 method=Image.Resampling.LANCZOS)
        canvas.paste(image, mask=image.getchannel("A"))
    return canvas


@contextmanager
def webm_input(path, size, fps, transparent):
    from viewer_runtime import tool_home
    tools = tool_home() / "video"
    ffmpeg = tools / "ffmpeg.exe"
    if not ffmpeg.is_file():
        ffmpeg = next(tools.rglob("ffmpeg*.exe"), None)
    if not ffmpeg:
        raise FileNotFoundError("The portable FFmpeg component is missing; re-extract the complete release archive.")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
           "-pix_fmt", "rgba" if transparent else "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", str(fps),
           "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p" if transparent else "yuv420p",
           "-crf", "24", "-b:v", "0", "-row-mt", "1", "-cpu-used", "4", "-threads", "4", "-auto-alt-ref", "0",
           "-f", "webm", path]
    with tempfile.TemporaryFile() as log, subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log) as writer:
        error = None
        try:
            with writer.stdin as pipe:
                yield pipe
        except BrokenPipeError as exc:
            error = exc
        code = writer.wait()
        if code or error:
            log.seek(0)
            raise RuntimeError(f"FFmpeg failed (exit {code}): {log.read().decode('utf-8', 'replace').strip()}") from error


def main():
    from .cubism_gpu import Model, Renderer
    from viewer_core import detect_game
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("model", type=Path)
    p.add_argument("--game", type=Path)
    p.add_argument("--png", type=Path, required=True)
    p.add_argument("--webm", type=Path)
    p.add_argument("--poster-first", action="store_true", help="measure and render only pose zero for a model preview")
    p.add_argument("--group", default="Idle")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--scale", type=float, default=.96, help="fraction of the available frame occupied (0..1)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--transparent", action="store_true", help="export the character without a background")
    mode.add_argument("--background", type=Path, help="center-crop this image to cover the output")
    args = p.parse_args()
    if not (1 <= args.fps <= 120 and 0 < args.scale <= 1 and
            all(2 <= v and v % 2 == 0 for v in (args.width, args.height))):
        p.error("Use positive even dimensions, 1..120 fps and a positive scale up to 1")
    if args.webm and args.png.resolve() == args.webm.resolve():
        p.error("PNG and WebM must have separate output paths")
    if args.poster_first and args.webm:
        p.error("--poster-first is a PNG preview, not a video")
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
    if args.poster_first and not any(files.get("Motions", {}).values()):
        motion_path = args.model
        motion = {"Meta": {"Duration": 1}, "Curves": []}  # A model without motion starts in its setup pose.
    else:
        motion_path = reference(files["Motions"][args.group][args.index]["File"])
        motion = json.loads(motion_path.read_text(encoding="utf-8-sig"))
    duration = float(motion["Meta"]["Duration"])
    if not 0 < duration or not math.isfinite(duration):
        p.error("Invalid motion duration")
    model = Model(core, reference(files["Moc"]))
    curves, opacity = motion_tracks(motion["Curves"], model.parameters)
    size = args.width, args.height
    render = Renderer(model, [reference(t) for t in files["Textures"]], (384, 384))
    limit = render.ctx.info["GL_MAX_TEXTURE_SIZE"]
    viewport = render.ctx.info.get("GL_MAX_VIEWPORT_DIMS", (limit, limit))
    if any(v > min(limit, cap) for v, cap in zip(size, viewport)):
        p.error(f"GPU does not support a {size[0]}x{size[1]} framebuffer (texture limit {limit}, viewport {viewport})")
    backdrop = None if args.transparent else make_background(args.background, size)
    frames = 1 if args.poster_first else max(1, round(duration * args.fps))
    poster = min(round(args.fps/2), frames-1)
    def apply_pose(frame):
        sec = frame / args.fps
        model.apply((idx, curve.sample(sec)) for idx, curve in curves)
        return opacity.sample(sec) if opacity is not None else 1.
    print(f"{motion_path.name}: measuring {frames} poses for a fixed {size[0]}x{size[1]} frame", flush=True)
    bounds = motion_bounds(render, apply_pose, frames)
    render.resize(size)
    render.set_view(fit_view(bounds, size, args.scale))
    with ExitStack() as stack:
        staged = {}
        for dest in (args.webm, args.png):
            if dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Stage on each destination volume so replacing existing files remains atomic.
                folder = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=dest.parent)))
                staged[dest] = folder / dest.name
        with (webm_input(staged[args.webm], size, args.fps, args.transparent) if args.webm else nullcontext()) as pipe:
            for i in (range(frames) if pipe else (poster,)):
                alpha = apply_pose(i)
                image = render.frame()
                apply_model_opacity(image, alpha)
                if backdrop is not None:
                    composite = backdrop.copy()
                    composite.paste(image, mask=image.getchannel("A"))
                    image = composite
                if i == poster:
                    image.save(staged[args.png], format="PNG")
                if pipe:
                    pipe.write(image.tobytes())
        for dest, output in staged.items():
            output.replace(dest)


if __name__ == "__main__":
    main()
