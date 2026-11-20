# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from viewer_runtime import WORKERS
datas = [(str(root / name), str(Path(name).parent))
         for name in ("wallpaper/index.html", "wallpaper/project.json",
                      "wallpaper/spine/index.html", "wallpaper/spine/card.js", "wallpaper/host.js")]
datas += collect_data_files("archspec", includes=["json/**/*.json"])
datas += collect_data_files("UnityPy", includes=["resources/*.tpk"])
a = Analysis(
    [str(root / "packaging/entry.py")], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=["tools." + name for name in WORKERS] + ["UnityPy", "PIL.ImageTk", "viewer_gui", "glcontext.wgl"],
    excludes=["fmod_toolkit", "pyfmodex", "numpy", "imageio_ffmpeg", "OpenGL", "live2d", "pygame",
              "IPython", "pytest", "matplotlib", "scipy"],
)
pyz = PYZ(a.pure)
# Windows supplies UCRT. build_windows.py stages only Microsoft's app-local VC
# redist after freezing, rather than redistributing random DLLs from the build PC.
def system_runtime(name):
    name = Path(name).name.lower()
    return (name.startswith(("api-ms-win-", "vcruntime140", "msvcp140")) or name == "ucrtbase.dll")
a.binaries = [row for row in a.binaries if not system_runtime(row[0])]
def launcher(name, console):
    return EXE(pyz, a.scripts, [], exclude_binaries=True, name=name,
               console=console, strip=False, upx=False)
gui = launcher("NekoConneViewer", False)
worker = launcher("NekoConneWorker", True)
dist = COLLECT(gui, worker, a.binaries, a.datas, name="NekoConneViewer", strip=False, upx=False)
