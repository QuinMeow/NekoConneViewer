"""Exercise the built release with Python/Conda removed from the child environment."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time


def check_gui(app, env, cwd):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    p = subprocess.Popen([app / "NekoConneViewer.exe"], env=env, cwd=cwd, startupinfo=startup)
    windows = []
    @callback
    def find(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == p.pid:
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, len(title))
            if title.value.startswith("NekoConne"):
                windows.append(hwnd)
        return True
    try:
        limit = time.monotonic() + 25
        while p.poll() is None and time.monotonic() < limit and not windows:
            user.EnumWindows(find, 0)
            time.sleep(.1)
        if not windows:
            raise RuntimeError(f"Viewer window not found (process status {p.poll()})")
        user.PostMessageW(windows[0], 0x10, 0, 0)
        if p.wait(timeout=10) != 0:
            raise RuntimeError("Viewer did not exit cleanly")
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("app", type=Path)
    p.add_argument("--gui", action="store_true", help="verify GUI window creation on an interactive Windows desktop")
    p.add_argument("--bundle", type=Path, help="optional real NekoConne data.unity3d")
    p.add_argument("--model", type=Path, help="optional exported Cubism model: render and decode every motion (needs --bundle)")
    args = p.parse_args()
    app = args.app.resolve()
    worker = app / "NekoConneWorker.exe"
    with tempfile.TemporaryDirectory() as temp:
        tmp = Path(temp)
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("PYTHON", "CONDA", "NEKOCONNE", "TCL_", "TK_"))}
        env.update(PATH=str(Path(os.environ["SystemRoot"]) / "System32"), NEKOCONNE_CACHE=str(tmp / "cache"))
        # A 2x1 uncompressed BGRA DDS with two distinct pixels.
        dds = tmp / "fixture"
        header = (124, 0x100F, 1, 2, 8, 0, 0, *([0]*11), 32, 0x41, 0, 32,
                  0xff0000, 0xff00, 0xff, 0xff000000, 0x1000, 0, 0, 0, 0)
        dds.write_bytes(b"DDS " + struct.pack("<31I", *header) + b"\0\0\xff\xff\0\xff\0\xff")
        run = dict(cwd=tmp, env=env, check=True, timeout=120)
        output = tmp / "images"
        subprocess.run([worker, "import_bundles", dds, "--out", output], **run)
        manifest, = output.glob("*/manifest.json")
        row, = json.loads(manifest.read_text(encoding="utf8"))["assets"]
        png = (manifest.parent / row["file"]).read_bytes()
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or struct.unpack_from(">II", png, 16) != (2, 1):
            raise ValueError("Expected a decoded 2x1 PNG")
        if args.bundle:
            subprocess.run([worker, "export_images", args.bundle.resolve(), "--out", tmp / "real",
                            "--match", "^title_bg_pc$"], **run)
            if not any((tmp / "real").rglob("*.png")):
                raise ValueError("Real bundle did not yield title art")
        if args.model:
            if not args.bundle:
                p.error("--model requires --bundle to locate the game's own Core")
            model = args.model.resolve()
            groups = json.loads(model.read_text(encoding="utf-8-sig"))["FileReferences"]["Motions"]
            for group, motions in groups.items():
                for i in range(len(motions)):
                    clip = tmp / "sample.webm"
                    subprocess.run([worker, "render_native", model, "--game", args.bundle.resolve().parents[1],
                                    "--group", group, "--index", str(i), "--width", "320", "--height", "400",
                                    "--fps", "12", "--png", tmp / "poster.png", "--webm", clip], **run)
                    raw = subprocess.run([app / ".tools/video/ffmpeg.exe", "-v", "error", "-xerror", "-c:v",
                                          "libvpx-vp9", "-i", clip, "-vf", "scale=96:120", "-pix_fmt", "rgba",
                                          "-f", "rawvideo", "pipe:1"], stdout=subprocess.PIPE, **run).stdout
                    size = 96*120*4
                    if not raw or len(raw) % size or min(raw[3::4]) != 0 or max(raw[3::4]) < 250:
                        raise ValueError("Motion did not decode to transparent character frames")
                    if not any(raw[i:i+size] != raw[:size] for i in range(size, len(raw), size)):
                        raise ValueError("Expected a moving clip in the NekoConne smoke fixture")
        if args.gui:
            check_gui(app, env, tmp)
    print("Frozen worker and requested smoke checks passed.")


if __name__ == "__main__":
    main()
