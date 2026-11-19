"""Prepare pinned upstream sources, or install the two compiled media helpers."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".tools/media-build"
SOURCES = WORK / "sources"
UPSTREAM = {
    "ffmpeg-8.1.1.tar.xz": ("https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz",
        "b6863adde98898f42602017462871b5f6333e65aec803fdd7a6308639c52edf3"),
    "libvpx-1.15.2.tar.gz": ("https://codeload.github.com/webmproject/libvpx/tar.gz/refs/tags/v1.15.2",
        "26fcd3db88045dee380e581862a6ef106f49b74b6396ee95c2993a260b4636aa"),
    "opus-1.5.2.tar.gz": ("https://codeload.github.com/xiph/opus/tar.gz/refs/tags/v1.5.2",
        "9480e329e989f70d69886ded470c7f8cfe6c0667cc4196d4837ac9e668fb7404"),
    "vgmstream-r2168.tar.gz": ("https://codeload.github.com/vgmstream/vgmstream/tar.gz/refs/tags/r2168",
        "6390ac616c4a248616f1b9bf6352fe949fcf2350a84844ba9416feac94f02225"),
}


def prepare():
    SOURCES.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in UPSTREAM.items():
        src = SOURCES / name
        if not src.is_file():
            with urllib.request.urlopen(url, timeout=90) as response, src.with_suffix(".partial").open("wb") as f:
                shutil.copyfileobj(response, f)
            src.with_suffix(".partial").replace(src)
        if hashlib.sha256(src.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Source checksum mismatch; remove this cached download and retry: {name}")
        subprocess.run(["tar", "-xf", src, "-C", WORK], check=True)


def install(app, dist):
    archive_name = "NekoConneViewer-media-sources-windows-x64.zip"
    licenses = app / "THIRD_PARTY_LICENSES/media"
    licenses.mkdir(parents=True, exist_ok=True)
    shutil.copytree(WORK / "notices/vgmstream", licenses / "vgmstream", dirs_exist_ok=True)
    for tool, dest in (("ffmpeg", "video"), ("vgmstream-cli", "audio/vgmstream-r2168")):
        output = app / ".tools" / dest
        output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WORK / "bin" / (tool + ".exe"), output)
    for dirname, files in {
        "ffmpeg-8.1.1": ("COPYING.LGPLv2.1", "COPYING.LGPLv3", "LICENSE.md"),
        "opus-1.5.2": ("COPYING", "LICENSE_PLEASE_READ.txt"),
        "libvpx-1.15.2": ("LICENSE", "PATENTS"),
    }.items():
        for name in files:
            shutil.copyfile(WORK / dirname / name, licenses / f"{dirname}-{name}.txt")
    (licenses / "SOURCES.txt").write_text(
        "FFmpeg and vgmstream run as standalone, replaceable command-line programs.\n"
        "FFmpeg uses libvpx and Opus; this build does not enable GPL or nonfree components.\n"
        "The vgmstream build uses built-in CRI/HCA support, not its external codec DLLs.\n"
        "For corresponding source and build recipes, download " + archive_name +
        " alongside the matching portable ZIP from https://github.com/QuinMeow/NekoConneViewer/releases/\n\n"
        + "".join(f"{name}: {url}  SHA256 {digest}\n" for name, (url, digest) in UPSTREAM.items()),
        encoding="utf8")
    dist.mkdir(exist_ok=True, parents=True)
    with zipfile.ZipFile(dist / archive_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for name in UPSTREAM:
            if name == "vgmstream-r2168.tar.gz":
                name = "vgmstream-r2168-source-only.tar.gz"
            z.write(SOURCES / name, "sources/" + name)
        for name in ("build_ffmpeg.sh", "build_vgmstream.py", "media.py"):
            z.write(ROOT / "packaging" / name, "packaging/" + name)
        for file in licenses.rglob("*"):
            if file.is_file():
                z.write(file, "licenses/" + file.relative_to(licenses).as_posix())
        z.writestr("BUILD.txt", "On Windows x64, use the repository's Windows release workflow for the toolchain.\n"
                   "Extract the included archives to .tools/media-build/ and put the scripts in packaging/.\n"
                   "Run bash packaging/build_ffmpeg.sh. For vgmstream, pass --archive sources/vgmstream-r2168-source-only.tar.gz\n"
                   "to python packaging/build_vgmstream.py from the project root.\n"
                   "The resulting replaceable executables are in .tools/media-build/bin/.\n")
    print(dist / archive_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("app", type=Path, nargs="?")
    args = parser.parse_args()
    if args.prepare == bool(args.app):
        parser.error("choose --prepare or a portable app directory")
    prepare() if args.prepare else install(args.app, args.dist)
