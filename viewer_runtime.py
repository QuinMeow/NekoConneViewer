"""Resolve worker commands for both a source checkout and a portable Windows build."""
import os
import shutil
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
APP_HOME = Path(sys.executable).resolve().parent if FROZEN else SOURCE


def tool_home():
    return Path(os.environ.get("NEKOCONNE_TOOLS", APP_HOME / ".tools")).resolve()


def command(script, *args):
    if not FROZEN:
        return list(map(str, (sys.executable, "-u", SOURCE / "tools" / script, *args)))
    home = tool_home()
    if script == "render_wallpaper.py":
        script = "render_native.py"
    if script in {"export_audio.py", "export_video.py"}:
        vgmstream = (next((home / "audio").glob("vgmstream-*/vgmstream-cli.exe"), None)
                     or shutil.which("vgmstream-cli"))
        if vgmstream:
            args += ("--cli" if script == "export_audio.py" else "--vgmstream", vgmstream)
        elif script == "export_audio.py":
            args += ("--cli", home / "audio/vgmstream-r2168/vgmstream-cli.exe")
    if script == "export_video.py":
        ffmpeg = next((home / "video").rglob("ffmpeg*.exe"), None) or shutil.which("ffmpeg")
        if ffmpeg:
            args += ("--ffmpeg", ffmpeg)
    return list(map(str, (APP_HOME / "NekoConneWorker.exe", Path(script).stem, *args)))
