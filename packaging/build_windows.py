"""Build the portable Windows directory, add notices, then ZIP the release."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def copy_msvc(app):
    # Use the official app-local VC redist from Visual Studio, not DLLs gathered
    # from System32 or from an arbitrary Python distribution.
    base = Path(os.environ["ProgramFiles"]) / "Microsoft Visual Studio"
    candidates = list(base.glob("*/*/VC/Redist/MSVC/*/x64/Microsoft.VC*.CRT"))
    source = max(candidates, key=lambda p: tuple(map(int, p.parents[1].name.split("."))), default=None)
    if not source:
        raise RuntimeError("Install the Visual Studio C++ x64 Redistributable Code build component.")
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copy2(source / name, app / "_internal" / name)
    verify = ("foreach ($n in 'msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll') {"
              "$sig=Get-AuthenticodeSignature -LiteralPath (Join-Path $env:VIEWER_REDIST_DIR $n); "
              "if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch "
              "'O=Microsoft Corporation') { throw \"Unexpected signature: $n ($($sig.Status))\" } }")
    subprocess.run([shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", verify],
                   env={**os.environ, "VIEWER_REDIST_DIR": str(app / "_internal")}, check=True)


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise SystemExit("Build on 64-bit Windows with 64-bit Python.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    out = parser.parse_args().out.resolve()
    env = dict(os.environ)
    local = ROOT / ".tools/unity"
    if local.is_dir() and not env.get("PYTHONPATH"):
        env["PYTHONPATH"] = str(local)
    out.mkdir(exist_ok=True, parents=True)
    archive = out / "NekoConneViewer-windows-x64.zip"
    # A user may have exports beside a previous EXE. Never let the freezer erase
    # that directory, and never zip previous files or game content along with the build.
    work = ROOT / ".tools/build-work"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as stage:
        app = Path(stage) / "NekoConneViewer"
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", stage,
                        "--workpath", str(work), str(ROOT / "packaging/NekoConneViewer.spec")],
                       cwd=ROOT, env=env, check=True)
        copy_msvc(app)
        for name in ("LICENSE", "README.md"):
            shutil.copy2(ROOT / name, app / name)
        for script in ("media.py", "licenses.py"):
            extra = ["--dist", str(out)] if script == "media.py" else []
            subprocess.run([sys.executable, str(ROOT / "packaging" / script), str(app), *extra], cwd=ROOT, env=env, check=True)
        with zipfile.ZipFile(Path(stage) / archive.name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in sorted(app.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(stage))
        (Path(stage) / archive.name).replace(archive)
        if not (out / app.name).exists():
            shutil.copytree(app, out / app.name)
        else:
            print(f"Existing {out / app.name} left unchanged; extract the new ZIP into a separate directory.")
    print(archive)


if __name__ == "__main__":
    main()
