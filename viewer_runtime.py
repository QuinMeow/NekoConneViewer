"""Resolve worker commands for both a source checkout and a portable Windows build."""
import os
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
APP_HOME = Path(sys.executable).resolve().parent if FROZEN else SOURCE
WORKERS = ("scan_assets", "model_previews", "render_native", "render_spine", "export_audio", "export_video")


def tool_home():
    return Path(os.environ.get("NEKOCONNE_TOOLS", APP_HOME / ".tools")).resolve()


def command(script, *args):
    tool = Path(script).stem
    if not FROZEN:
        return list(map(str, (sys.executable, "-u", "-m", "tools." + tool, *args)))
    return list(map(str, (APP_HOME / "NekoConneWorker.exe", tool, *args)))
