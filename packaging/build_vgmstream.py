"""Build the audio CLI using CMake/Ninja and MinGW-w64 (Windows) or native cc/c++.

Input: the unmodified vgmstream r2168 GitHub codeload archive, or the
source-only copy supplied in our corresponding-source download.
"""
import argparse
import gzip
import hashlib
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
from urllib.request import urlopen

TAG = "r2168"
PREFIX = "vgmstream-" + TAG
DEFAULT = Path(__file__).resolve().parents[1] / ".tools/media-build"
UPSTREAM_SHA256 = "6390ac616c4a248616f1b9bf6352fe949fcf2350a84844ba9416feac94f02225"
SOURCE_SHA256 = "031e1e748432ddc69152dd3f245eacd5f2bc23e4bab30a63a1d68d7458431b31"
CODECS = ("MPEG", "VORBIS", "FFMPEG", "G7221", "G719", "ATRAC9", "CELT", "SPEEX")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(archive, source, trimmed):
    digest = sha256(archive)
    if digest not in (UPSTREAM_SHA256, SOURCE_SHA256):
        raise ValueError(f"Unexpected hash for vgmstream {TAG}: {digest}")
    if digest == UPSTREAM_SHA256:
        trimmed.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as old, trimmed.open("wb") as stream, \
                gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=9) as gz, \
                tarfile.open(fileobj=gz, mode="w") as new:
            for member in old:
                if member.isfile() and not member.name.lower().endswith((".dll", ".exe", ".lib")):
                    new.addfile(member, old.extractfile(member))
        archive = trimmed
    with tarfile.open(archive) as data:
        for member in data:
            path = PurePosixPath(member.name)
            if not member.isfile() or path.parts[0] != PREFIX or ".." in path.parts:
                raise ValueError("Unsafe source archive")
            dest = source.joinpath(*path.parts[1:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            payload = data.extractfile(member).read()
            if not dest.exists() or dest.read_bytes() != payload:
                dest.write_bytes(payload)
    return archive


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=DEFAULT)
    p.add_argument("--archive", type=Path)
    p.add_argument("--jobs", type=int, default=8)
    args = p.parse_args()
    root = args.root.resolve()
    if args.jobs < 1:
        p.error("--jobs must be positive")
    win = sys.platform == "win32"
    source = root / "work-vgmstream-src"
    build = root / "work-vgmstream-cli"
    archive = args.archive or root / "sources" / (PREFIX + ".tar.gz")
    if not archive.is_file():
        if args.archive:
            p.error(f"Archive not found: {archive}")
        url = f"https://codeload.github.com/vgmstream/vgmstream/tar.gz/refs/tags/{TAG}"
        with urlopen(url, timeout=60) as response:
            payload = response.read()
        if hashlib.sha256(payload).hexdigest() != UPSTREAM_SHA256:
            raise ValueError("Downloaded vgmstream source hash mismatch")
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(payload)
    trimmed = root / "sources" / (PREFIX + "-source-only.tar.gz")
    prepare(archive, source, trimmed)
    options = ["-DBUILD_CLI=ON", "-DBUILD_SHARED_LIBS=OFF", "-DBUILD_AUDACIOUS=OFF", "-DBUILD_V123=OFF",
               "-DBUILD_WINAMP=OFF", "-DBUILD_XMPLAY=OFF", "-DBUILD_FB2K=OFF", "-DCMAKE_BUILD_TYPE=Release",
               f"-DVGMSTREAM_VERSION={TAG}", *(f"-DUSE_{codec}=OFF" for codec in CODECS)]
    if win:
        options.extend(["-DBUILD_STATIC=ON", "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_CXX_COMPILER=g++",
                        "-DCMAKE_EXE_LINKER_FLAGS=-Wl,--no-insert-timestamp"])
    prefix_map = f'"-ffile-prefix-map={source.as_posix()}={PREFIX}"'
    # Upstream getopt uses legacy C prototypes that GCC's C23 default rejects.
    options.extend([f"-DCMAKE_C_FLAGS=-std=gnu17 {prefix_map}", f"-DCMAKE_CXX_FLAGS={prefix_map}"])
    subprocess.run(["cmake", "-S", source, "-B", build, "-G", "Ninja", *options], check=True)
    subprocess.run(["cmake", "--build", build, "--target", "vgmstream_cli", "--parallel", str(args.jobs)], check=True)
    binary = build / "cli" / ("vgmstream-cli.exe" if win else "vgmstream-cli")
    if win:
        inspect = subprocess.check_output(["objdump", "-p", binary], text=True, errors="replace")
        deps = [s.lower() for s in re.findall(r"DLL Name:\s*(\S+)", inspect)]
        if "pei-x86-64" not in inspect or not deps or any(
                not (n in {"kernel32.dll", "msvcrt.dll", "ucrtbase.dll"} or n.startswith("api-ms-win-")) for n in deps):
            raise ValueError(f"Expected an x64 CLI importing only Windows system DLLs; got {deps}")
        print("Windows imports:", ", ".join(deps))
    else:
        subprocess.run(["otool", "-L", binary] if sys.platform == "darwin" else ["ldd", binary], check=True)
    output = root / "bin"
    output.mkdir(exist_ok=True)
    shutil.copy2(binary, output / binary.name)
    notices = root / "notices/vgmstream"
    notices.mkdir(exist_ok=True, parents=True)
    shutil.copyfile(source / "COPYING", notices / "COPYING")
    shutil.copyfile(source / "ext_libs/licenses/ffmpeg-9.0.2.COPYING.LGPLv2.1", notices / "COPYING.LIB")
    (notices / "GETOPT.txt").write_text("The Windows CLI links the upstream GNU getopt code in "
        "ext_libs/Getopt. It is covered by the GNU Lesser General Public License 2.1 or later. "
        "See COPYING.LIB. Its source and the build recipe are in the corresponding-source download.\n",
        encoding="utf8")
    print(f"{output / binary.name}: {sha256(binary)}")


if __name__ == "__main__":
    main()
