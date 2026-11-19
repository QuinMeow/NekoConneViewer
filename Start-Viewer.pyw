"""Explorer entry point for source checkouts (Windows Python association, no CMD)."""
import os
from pathlib import Path
import runpy
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    packaged = root / "dist/NekoConneViewer/NekoConneViewer.exe"
    if packaged.is_file():
        env = os.environ.copy()
        if (root / "exports").is_dir() and not (root / "exports/builtin").exists():
            env.setdefault("NEKOCONNE_CACHE", str(root / "exports"))
        subprocess.Popen([str(packaged)], cwd=root, env=env)
        return
    venv = root / ".venv/Scripts/pythonw.exe"
    if venv.is_file() and Path(sys.executable).resolve().parent != venv.parent.resolve():
        subprocess.Popen([str(venv), __file__], cwd=root)
        return
    os.chdir(root)
    runpy.run_path(str(root / "viewer_gui.py"), run_name="__main__")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import ctypes
        import traceback
        ctypes.windll.user32.MessageBoxW(None, traceback.format_exc(), "NekoConne Viewer", 0x10)
        sys.exit(1)
