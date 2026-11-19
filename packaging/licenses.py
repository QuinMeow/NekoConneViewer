"""Collect third-party notices for the Windows portable build (never run by the viewer)."""

import hashlib
from importlib import metadata
import io
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import sysconfig
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
RAW = "https://raw.githubusercontent.com/"
PYPI = "https://files.pythonhosted.org/packages/"

# The native wheel wrappers do not include all upstream notices in their LICENSE file.
SUPPLEMENTS = {
    "Arm-astcenc": ("ARM-software/astc-encoder/5.0.0/LICENSE.txt", "494accc32e50eb523a0e384d0ae6d4b702db867a89d6971216760e92b240ee12"),
    "etcpak": ("wolfpld/etcpak/master/LICENSE.txt", "1de8a55cc43c86e8d58e3272f4e2b4e24107a4a571bbd1ee2ec00c4dbd4245e2"),
    "lz4": ("lz4/lz4/v1.9.4/lib/LICENSE", "8b58c446121a109ccf32edc094bba3010a3d85e4ee3702950db55e4d3e87736c"),
    "Tcl": ("tcltk/tcl/core-8-6-12/license.terms", "c0a69a2bfd757361ec7e6143973b103c90409316b49e9c88db26ad6388e79f16"),
    "Tk": ("tcltk/tk/core-8-6-12/license.terms", "2cde822b93ca16ae535c954b7dfe658b4ad10df2a193628d1b358f1765e8b198"),
    "Expat": ("python/cpython/v3.10.9/Modules/expat/COPYING", "122f2c27000472a201d337b9b31f7eb2b52d091b02857061a8880371612d9534"),
    "bzip2": ("libarchive/bzip2/bzip2-1.0.8/LICENSE", "c6dbbf828498be844a89eaa3b84adbab3199e342eb5cb2ed2f0d4ba7ec0f38a3"),
    "libffi": ("libffi/libffi/v3.4.2/LICENSE", "a61d06e8f7be57928e71e800eb9273b05cb8868c484108afe41e4305bb320dde"),
    "liblzma": ("tukaani-project/xz/v5.2.10/COPYING", "bcb02973ef6e87ea73d331b3a80df7748407f17efdb784b61b47e0e610d3bb5c"),
    "zlib": ("madler/zlib/v1.2.13/LICENSE", "845efc77857d485d91fb3e0b884aaa929368c717ae8186b66fe1ed2495753243"),
}
SOURCES = {
    "astc-encoder-py": ("4f/1e/fca57bdb9b429a1d2e2f83ad0621b4ab6af65ee5fefc38a4f318afad0d8e/astc_encoder_py-0.1.12.tar.gz", "03759ac32804cae32bc238e72f94c5ba27e228507df00816b741f7961f1184f9"),
    "etcpak": ("76/21/c504ccfe89dbff7154a7733b6792885b3af88a6a5db534b381ae6c3048fd/etcpak-0.9.15.tar.gz", "fe09e32d8aa75b8163832428c3d6df65a51b2b7e0237292c10c1224ca5728a43"),
    "texture2ddecoder": ("d8/25/603483a6c5f0fcd694732cbb079c83a026aa4ff153278a1f400c2bdf65e7/texture2ddecoder-1.0.6.tar.gz", "88f04d8951018ac13b8dacd07e57e8adad4a6f1cfe5509646e8a1020346a81ca"),
}


def checked(url, digest):
    cache = ROOT / ".tools/notice-cache" / digest
    if cache.exists():
        data = cache.read_bytes()
    else:
        with urlopen(url, timeout=30) as response:
            data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError(f"Notice input changed: {url}")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(data)
    return data


def codec_notices(blob):
    blocks = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for entry in archive:
            name = PurePosixPath(entry.name)
            if not entry.isfile() or name.suffix not in {".cpp", ".c", ".h", ".hpp"}:
                continue
            text = archive.extractfile(entry).read().decode("utf8", "replace")
            for match in re.finditer(r"/\*[\s\S]*?\*/|(?:^[ \t]*//[^\n]*\n?)+", text, flags=re.M):
                block = match[0].strip()
                if re.search(r"copyright|public domain|SPDX-License-Identifier", block, re.I):
                    blocks.setdefault(block, name.as_posix())
    if not blocks:
        raise ValueError("Missing native code notices")
    return "\n\n".join(f"{name}\n{block}" for block, name in blocks.items()) + "\n"


def collect(destination: Path):
    destination = Path(destination).resolve()
    if not (destination / "NekoConneViewer.exe").is_file():
        raise ValueError("Specify the portable distribution directory containing NekoConneViewer.exe")
    out = destination / "THIRD_PARTY_LICENSES"
    out.mkdir(exist_ok=True)
    local = ROOT / ".tools/unity"
    if local.is_dir():
        sys.path.insert(0, str(local))
    lines = ["The viewer's MIT license does not relicense these components.", ""]
    wheels = ("UnityPy", "Pillow", "attrs", "fsspec", "archspec", "Brotli", "lz4",
              "astc-encoder-py", "etcpak", "texture2ddecoder", "PyInstaller", "moderngl", "glcontext")
    for name in wheels:
        dist = metadata.distribution(name)
        notices = [f for f in dist.files or [] if f.name.lower().startswith(("license", "notice", "copying"))]
        if not notices:
            raise RuntimeError(f"{name}: wheel license not found")
        folder = out / name
        for f in notices:
            target = folder / Path(*f.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dist.locate_file(f), target)
        if name in SOURCES:
            path, digest = SOURCES[name]
            if not path.endswith(f"-{dist.version}.tar.gz"):
                raise ValueError(f"Review native notices for updated {name} {dist.version} before publishing")
            (folder / "CODEC-NOTICES.txt").write_text(codec_notices(checked(PYPI + path, digest)), encoding="utf8")
        lines.append(f"{name} {dist.version}: see {name}/")

    home = Path(sys.base_prefix)
    license_file = next((p for p in [home / "LICENSE.txt", home / "LICENSE_PYTHON.txt", home / "LICENSE",
                                     Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"]
                         if p.is_file()), None)
    if not license_file:
        raise RuntimeError("Cannot locate the license of the Python build interpreter")
    shutil.copyfile(license_file, out / "CPython.txt")
    lines.append(f"CPython {sys.version.split()[0]}: see CPython.txt")
    supplements = SUPPLEMENTS.copy()
    if any((destination / "_internal").glob("libcrypto-1_1*.dll")):
        supplements["OpenSSL-1.1.1"] = ("openssl/openssl/OpenSSL_1_1_1s/LICENSE", "c32913b33252e71190af2066f08115c69bc9fddadf3bf29296e20c835389841c")
    if any((destination / "_internal").glob("libcrypto-3*.dll")):
        supplements["OpenSSL-3"] = ("openssl/openssl/openssl-3.0.16/LICENSE.txt", "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a")
    for name, (path, digest) in supplements.items():
        (out / (name + "-upstream.txt")).write_bytes(checked(RAW + path, digest))
        lines.append(f"{name}: {RAW}{path} (SHA256 {digest})")
    lines.extend(["", "Microsoft Visual C++ redistributable code is licensed separately from the MIT viewer.",
                  "Distributable code list: https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution",
                  "Terms: https://visualstudio.microsoft.com/license-terms/vs2022-ga-community/",
                  "This directory describes the viewer and its frozen Python runtime.",
                  "Tools distributed alongside the viewer have their own notices and licensing terms."])
    (out / "INDEX.txt").write_text("\n".join(lines) + "\n", encoding="utf8")
    print("Third-party notices:", out)


if __name__ == "__main__":
    collect(Path(sys.argv[1]))
