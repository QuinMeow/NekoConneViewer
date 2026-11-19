"""Render a Cubism model to a straight-alpha PNG and optional looping WebM."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Only use the workspace wheel if it matches this Python's native ABI.
local = Path(os.environ.get("NEKOCONNE_TOOLS", Path(__file__).resolve().parents[1] / ".tools")) / "render"
abi = f"cp{sys.version_info.major}{sys.version_info.minor}"
if any(f"Tag: {abi}-{abi}-" in f.read_text() for f in local.glob("live2d_py-*.dist-info/WHEEL")):
    sys.path.insert(0, str(local))
import imageio_ffmpeg
import live2d.v3 as cubism
from OpenGL import GL
from PIL import Image
import pygame


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("model", type=Path)
    p.add_argument("--png", type=Path, required=True)
    p.add_argument("--webm", type=Path)
    p.add_argument("--group")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--width", type=int, default=1000)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--scale", type=float, default=1.0)
    args = p.parse_args()
    if min(args.width, args.height, args.fps, args.scale) <= 0:
        p.error("resolution, fps and scale must be positive")
    if args.webm and (args.width % 2 or args.height % 2):
        p.error("WebM requires even width and height")
    source = args.model.resolve()
    groups = json.loads(source.read_text(encoding="utf-8-sig"))["FileReferences"]["Motions"]
    group = args.group or next((g for g in groups if "idle" in g.lower()), next(iter(groups)))
    motion_file = source.parent / groups[group][args.index]["File"]
    motion = json.loads(motion_file.read_text(encoding="utf-8-sig"))
    duration = float(motion["Meta"]["Duration"])
    count = max(1, round(duration * args.fps))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"invalid motion duration: {duration}")
    print(f"{group}[{args.index}] {motion_file.name}: {duration:g}s / {count} frames", flush=True)

    size = args.width, args.height
    pygame.display.init()
    pygame.display.gl_set_attribute(pygame.GL_ALPHA_SIZE, 8)
    pygame.display.set_mode(size, pygame.OPENGL | pygame.DOUBLEBUF | pygame.HIDDEN)
    cubism.init()
    cubism.glInit()
    if GL.glGetIntegerv(GL.GL_ALPHA_BITS) < 8:
        raise RuntimeError("framebuffer has no 8-bit alpha channel")
    model = cubism.Model()
    writer = None
    try:
        model.LoadModelJson(str(source))
        model.CreateRenderer(2)
        model.Resize(*size)
        model.SetScale(args.scale)
        model.SetAutoBreath(False)
        model.SetAutoBlink(False)
        model.StartMotion(group, args.index, 3)
        if model.IsMotionFinished():
            raise RuntimeError(f"Cubism failed to start {motion_file.name}; inspect the parser log")
        if args.webm:
            args.webm.parent.mkdir(parents=True, exist_ok=True)
            writer = subprocess.Popen([
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{args.width}x{args.height}",
                "-framerate", str(args.fps), "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9",
                "-pix_fmt", "yuva420p", "-crf", "24", "-b:v", "0", "-row-mt", "1",
                "-cpu-used", "4", "-threads", "4", "-auto-alt-ref", "0", str(args.webm)
            ], stdin=subprocess.PIPE)
        args.png.parent.mkdir(parents=True, exist_ok=True)
        # Capture after the initial motion fade-in, not the default setup pose.
        poster = min(count - 1, round(0.5 * args.fps))
        bounds = None
        for i in range(count if writer else poster + 1):
            pygame.event.pump()
            cubism.clearBuffer()
            model.Update(duration / count)
            model.Draw()
            if not writer and i != poster:
                continue
            pixels = GL.glReadPixels(0, 0, *size, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            frame = Image.frombytes("RGBa", size, pixels, "raw", "RGBa", 0, -1).convert("RGBA")
            if i == poster:
                bounds = frame.getchannel("A").getbbox()
                if not bounds:
                    raise RuntimeError("renderer returned a blank frame")
                frame.save(args.png)
            if writer:
                writer.stdin.write(frame.tobytes())
        print(f"PNG framing: {bounds} on {size}", flush=True)
    finally:
        if writer:
            writer.stdin.close()
            status = writer.wait()
            if status:
                raise RuntimeError(f"ffmpeg exited {status}")
        model.DestroyRenderer()
        del model
        cubism.glRelease()
        cubism.dispose()
        pygame.display.quit()


if __name__ == "__main__":
    main()
