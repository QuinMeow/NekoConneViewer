"""Use the standalone SpineViewer CLI (v0.16.34) to query or render a skeleton."""
import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
import zipfile

_HOME = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
VERSION = "v0.16.34"
URL = f"https://github.com/ww-rm/SpineViewer/releases/download/{VERSION}/SpineViewer-{VERSION}-SelfContained.zip"
# Published by GitHub at /ww-rm/SpineViewer/releases/expanded_assets/v0.16.34.
SHA256 = "41a8c8a9dd49e7c5f4ca95d6a5f0aa7e7a4a31c171d3dbb590cf3f7c344228e5"
ARCHIVE_BYTES = 75609303
_lock = threading.Lock()


def tool_home():
    return Path(os.environ.get("NEKOCONNE_TOOLS", _HOME / ".tools"))


def _ready(folder):
    return all((folder / name).is_file() for name in
               ("SpineViewerCLI.exe", "SpineViewerCLI.dll", "coreclr.dll", "hostfxr.dll", "hostpolicy.dll"))


def _download(folder):
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".spine-install-", dir=folder.parent) as tmp:
        stage = Path(tmp) / "component"
        archive = Path(tmp) / "upstream.zip"
        digest, size = hashlib.sha256(), 0
        with urlopen(Request(URL, headers={"User-Agent": "NekoConneViewer/Spine"}), timeout=60) as src, archive.open("wb") as dst:
            while block := src.read(1 << 20):
                size += len(block)
                if size > ARCHIVE_BYTES:
                    raise ValueError("Spine component exceeds the pinned archive size")
                digest.update(block)
                dst.write(block)
        if size != ARCHIVE_BYTES or digest.hexdigest() != SHA256:
            raise ValueError("Spine component checksum mismatch; no files installed")
        # Only the exact pinned upstream bytes reach ZipFile. Publish the complete runtime atomically.
        with zipfile.ZipFile(archive) as z:
            z.extractall(stage)
        if not _ready(stage):
            raise ValueError("Spine component has no self-contained Windows CLI")
        try:
            stage.rename(folder)
        except OSError:
            if not _ready(folder):  # A parallel process may have installed the same release.
                raise


def ensure_cli():
    if sys.platform != "win32":
        raise RuntimeError("The portable Spine component requires Windows x64")
    portable = tool_home() / f"spine-{VERSION}-selfcontained"
    local_data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NekoConneViewer/components" / portable.name
    with _lock:
        for folder in (portable, local_data):
            if _ready(folder):
                return folder / "SpineViewerCLI.exe"
        try:
            try:
                _download(portable)
                return portable / "SpineViewerCLI.exe"
            except PermissionError:
                _download(local_data)
                return local_data / "SpineViewerCLI.exe"
        except (OSError, ValueError, zipfile.BadZipFile) as e:
            raise RuntimeError(f"Spine 组件首次启用需要连接 GitHub。安装未完成：{e}") from e


def child_env(use_ffmpeg):
    env = os.environ.copy()
    if use_ffmpeg:
        ffmpeg = tool_home() / "video/ffmpeg.exe"
        if ffmpeg.is_file():
            env["PATH"] = str(ffmpeg.parent) + os.pathsep + env.get("PATH", "")
        elif (tool_home() / "spine/ffmpeg.exe").is_file():  # Existing source workspaces.
            env["PATH"] = str(tool_home() / "spine") + os.pathsep + env.get("PATH", "")
        elif not shutil.which("ffmpeg"):
            raise RuntimeError("Spine 视频导出需要便携版内的 .tools/video/ffmpeg.exe")
    return env


def pillow():
    try:
        from PIL import Image
    except ModuleNotFoundError:
        if getattr(sys, "frozen", False) or not (_HOME / ".tools/unity/PIL").is_dir():
            raise
        sys.path.insert(0, str(_HOME / ".tools/unity"))
        from PIL import Image
    return Image


def ordered_frames(folder, fps):
    frame_name = re.compile(rf"frame_{fps}_([0-9]+)\.png\Z")
    found = sorted((int(match[1]), p) for p in folder.iterdir() if (match := frame_name.fullmatch(p.name)))
    if not found or any(n != i for i, (n, _) in enumerate(found)):
        raise ValueError("Spine renderer did not produce a continuous frame sequence starting at zero")
    return [p for _, p in found]


def joint_crop(frames, padding=2):
    image = pillow()
    union, canvas = None, None
    for path in frames:
        with image.open(path) as frame:
            if "A" not in frame.getbands() or (canvas and frame.size != canvas):
                raise ValueError("Expected RGBA frames in a fixed coordinate system")
            canvas = frame.size
            box = frame.getchannel("A").getbbox()
        if box is not None:
            union = ((min(union[0], box[0]), min(union[1], box[1]), max(union[2], box[2]), max(union[3], box[3]))
                     if union else box)
    if union is None:
        raise ValueError("Spine animation is transparent in every frame; check the selected skin")
    left, top, right, bottom = union
    # Even output dimensions for 4:2:0; Pillow supplies transparent padding outside the original canvas.
    return (left - padding, top - padding, right + padding + ((right-left) % 2),
            bottom + padding + ((bottom-top) % 2))


def write_sequence(frames, fps, png, video, env):
    image = pillow()
    box = joint_crop(frames)
    w, h = box[2] - box[0], box[3] - box[1]
    if png is not None:
        with image.open(frames[0]) as frame:
            frame.crop(box).convert("RGBA").save(png, format="PNG")
    if video is not None:
        ffmpeg = shutil.which("ffmpeg", path=env["PATH"])
        if not ffmpeg:
            raise RuntimeError("Spine video export requires FFmpeg")
        # The upstream Frames exporter encodes standard straight-alpha PNGs. Do not unpremultiply again.
        cmd = [ffmpeg, "-v", "error", "-xerror", "-f", "rawvideo", "-r", str(fps), "-pix_fmt", "rgba",
               "-s", f"{w}x{h}", "-i", "pipe:0", "-f", "webm", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
               "-crf", "23", "-b:v", "0", "-y", video]
        with tempfile.TemporaryFile() as log, subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as encoder:
            try:
                with encoder.stdin as pipe:
                    for path in frames:
                        with image.open(path) as frame:
                            pipe.write(frame.crop(box).convert("RGBA").tobytes())
            except BrokenPipeError:
                pass  # Report FFmpeg's own diagnostic instead.
            if encoder.wait() or not video.is_file() or not video.stat().st_size:
                log.seek(0)
                raise RuntimeError("Spine WebM encoding failed: " + log.read().decode("utf-8", "replace"))
    return box


def run_cli(cmd, **kwargs):
    # The executable must resolve its own DLLs, not PyInstaller's _internal folder.
    # Its working directory stays with the CLI: model & destination arguments are absolute.
    return subprocess.run(cmd, cwd=Path(cmd[0]).parent, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)


def query(skel: Path) -> dict:
    """Return animation/skin choices without needing a prebuilt catalog."""
    info = {"skins": [], "animations": []}
    skel = Path(skel).resolve()
    with skel.open("rb") as f:
        version = re.search(rb"\d+\.\d+\.\d+", f.read(64))
    if version:
        info["version"] = version.group().decode("ascii")
    result = run_cli([ensure_cli(), "query", skel, "--animation", "--skin", "-q"],
                            capture_output=True, check=True)
    section = None
    for line in result.stdout.decode("utf-8-sig").splitlines():
        if line.startswith(">"):
            section = "skins" if "Skins" in line else "animations" if "Animations" in line else None
        elif line.startswith("<"):
            section = None
        elif line and line not in {"Name", "Name\tDuration"}:
            if section == "skins":
                info["skins"].append(line)
            elif section == "animations":
                name, duration = line.rsplit("\t", 1)
                info["animations"].append({"name": name, "duration": float(duration)})
    return info


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skeleton", type=Path)
    p.add_argument("--list", action="store_true", help="list available animations and skins")
    p.add_argument("--catalog", type=Path, help="write animation/skin choices to this JSON file")
    p.add_argument("--animation")
    p.add_argument("--skin", action="append", default=[])
    p.add_argument("--png", type=Path)
    p.add_argument("--webm", type=Path)
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--max-resolution", type=int, default=850, help="video render resolution cap; PNG-only uses 2048")
    args = p.parse_args()
    if not args.skeleton.is_file():
        p.error("skeleton file not found")
    if args.list or args.catalog:
        data = json.dumps(query(args.skeleton), ensure_ascii=False, indent=2)
        if args.catalog:
            args.catalog.parent.mkdir(parents=True, exist_ok=True)
            args.catalog.write_text(data, encoding="utf-8")
        if args.list:
            print(data)
        return
    if not args.animation or not (args.png or args.webm):
        p.error("specify --animation and at least one of --png or --webm")
    if args.png and args.webm and args.png.resolve() == args.webm.resolve():
        p.error("PNG and WebM must have separate output paths")
    if not (1 <= args.fps <= 120 and 32 <= args.max_resolution <= 4096):
        p.error("invalid frame rate (1..120) or resolution (32..4096)")
    base = [ensure_cli(), "export", args.skeleton.resolve(), "-a", args.animation, "--pma",
            "--color", "#00000000", "--margin", "12", "--no-progress"]
    if args.skin:
        base.extend(["--skins", *args.skin])
    env = child_env(bool(args.webm))
    with ExitStack() as stack:
        staged = {}
        for dest in (args.png, args.webm):
            if dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                parent = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=dest.parent))).resolve()
                staged[dest] = parent / dest.name
        folder = next(iter(staged.values())).parent / "frames"
        cmd = [*base, "-f", "Frames", "-o", folder, "--fps", str(args.fps),
               "--max-resolution", str(args.max_resolution if args.webm else 2048)]
        run = run_cli(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        log = run.stdout.decode("utf-8", errors="replace")
        if run.returncode or "[E]" in log or not folder.is_dir():
            raise RuntimeError(log or "Spine render failed")
        frames = ordered_frames(folder, args.fps)
        box = write_sequence(frames, args.fps, staged.get(args.png), staged.get(args.webm), env)
        for dest, output in staged.items():
            output.replace(dest)
            print(f"{args.skeleton.stem} {args.animation}: {len(frames)} frames, crop {box} -> {dest}", flush=True)


if __name__ == "__main__":
    main()
