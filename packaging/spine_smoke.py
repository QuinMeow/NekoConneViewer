"""Verify cached Spine rendering with system .NET, FFmpeg and network paths disabled."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("skeleton", type=Path)
    p.add_argument("--tools", type=Path, required=True, help="prepared component cache containing video/ffmpeg.exe")
    p.add_argument("--worker", type=Path, help="test the frozen worker instead of the source script")
    args = p.parse_args()
    home = args.tools.resolve()
    ffmpeg = home / "video/ffmpeg.exe"
    if not ffmpeg.is_file() or not any(home.glob("spine-*-selfcontained/SpineViewerCLI.exe")):
        raise SystemExit("First enable Spine online and supply the portable FFmpeg in the chosen tools directory.")
    command = ([args.worker.resolve(), "render_spine"] if args.worker else
               [sys.executable, Path(__file__).resolve().parents[1] / "tools/render_spine.py"])
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(
            ("PYTHON", "DOTNET", "CONDA", "NEKOCONNE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"))}
        env.update(NEKOCONNE_TOOLS=str(home), LOCALAPPDATA=str(work / "profile"),
                   DOTNET_ROOT=str(work / "no-dotnet"), DOTNET_ROOT_X64=str(work / "no-dotnet"),
                   DOTNET_MULTILEVEL_LOOKUP="0", PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
                   HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="")
        run = dict(cwd=work, env=env, check=True, capture_output=True, timeout=120)
        skeleton = args.skeleton.resolve()
        catalog = json.loads(subprocess.run([*command, skeleton, "--list"], **run).stdout.decode("utf-8-sig"))
        motion = next(a["name"] for a in catalog["animations"] if a["duration"] > .15)
        skin = next((s for s in catalog.get("skins", []) if s.casefold() != "default"), None)
        png, webm = work / "test.png", work / "test.webm"
        subprocess.run([*command, skeleton, "--animation", motion, *(["--skin", skin] if skin else []),
                        "--png", png, "--webm", webm, "--fps", "8", "--max-resolution", "192"], **run)
        image = png.read_bytes()
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or min(struct.unpack_from(">II", image, 16)) < 4:
            raise ValueError("Spine PNG is missing or empty")
        subprocess.run([ffmpeg, "-v", "error", "-xerror", "-i", webm, "-f", "null", "-"], **run)
        print(f"Offline Spine: {len(catalog['animations'])} motions, {len(catalog['skins'])} skins; "
              f"rendered {motion}: PNG {len(image)} bytes, WebM {webm.stat().st_size} bytes.")


if __name__ == "__main__":
    main()
