"""Assemble a local WebGL wallpaper from an exported Spine 4.1 card."""
import argparse
import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import tarfile
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.request import urlopen

from tools.spine_assets import atlas_pages
from viewer_runtime import tool_home

TEMPLATE = Path(__file__).resolve().parents[1] / "wallpaper/spine"
PACKAGE = "spine-webgl-4.1.56.tgz"
URL = "https://registry.npmjs.org/@esotericsoftware/spine-webgl/-/" + PACKAGE
SHA256 = "fc9c0c579e7d91fcba007fabdc7ecced6fad70fca84bd6e3374a3a4f6ac23e4d"
ARCHIVE_BYTES = 370416


def web_runtime():
    path = tool_home() / PACKAGE
    if path.is_file():
        archive = path.read_bytes()
    else:
        try:
            with urlopen(URL, timeout=45) as response:
                archive = response.read(ARCHIVE_BYTES + 1)
        except OSError as exc:
            raise RuntimeError("首次生成原生 Spine 壁纸需要下载 WebGL 运行库：" + str(exc)) from exc
    if len(archive) != ARCHIVE_BYTES or sha256(archive).hexdigest() != SHA256:
        raise ValueError("Spine WebGL package checksum mismatch")
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as pending:
            pending.write(archive)
        temp = Path(pending.name)
        try:
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as package:
        return (package.extractfile("package/dist/iife/spine-webgl.min.js").read(),
                package.extractfile("package/LICENSE").read())


def model_data(path):
    skeleton = path.read_bytes()
    version = re.search(rb"\d+\.\d+\.\d+", skeleton[:64])
    if not version or not version.group().startswith(b"4.1."):
        raise ValueError("Native HTML export requires a Spine 4.1 skeleton")
    atlas = path.with_suffix(".atlas").read_bytes()
    textures = {}
    for name, size in atlas_pages(atlas):
        if not size or not all(size):
            raise ValueError(f"Missing atlas page dimensions: {name}")
        page = (path.parent / name).resolve()
        if page.parent != path.parent:
            raise ValueError(f"Atlas image is outside the model directory: {name}")
        textures[name] = "data:image/png;base64," + base64.b64encode(page.read_bytes()).decode("ascii")
    return {"skeleton": base64.b64encode(skeleton).decode("ascii"), "atlas": atlas.decode("utf-8-sig"),
            "textures": textures}


def build(skeleton, target, title=None, *, metadata=None):
    skeleton = Path(skeleton).resolve()
    target = Path(target).resolve()
    if target.exists():
        raise FileExistsError(target)
    data = model_data(skeleton)
    if metadata is None:
        from tools.render_spine import query
        metadata = query(skeleton)
    if metadata["skins"] != ["default"] or [a["name"] for a in metadata["animations"]] != ["animation"]:
        raise ValueError("只有单一 default / animation 的 Spine 卡面可以直接生成壁纸。其他模型请先渲染动作。")
    runtime, license_text = web_runtime()
    project = {
        "title": "NekoConne | " + (title or skeleton.stem), "type": "web", "file": "index.html",
        "general": {"properties": {
            "wholecard": {"type": "bool", "order": 0, "text": "保留完整卡面", "value": False},
            "hidebg": {"type": "bool", "order": 1, "text": "隐藏卡面背景", "value": False},
        }},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as temp:
        stage = Path(temp) / "project"
        stage.mkdir()
        for name in ("index.html", "card.js"):
            shutil.copyfile(TEMPLATE / name, stage / name)
        shutil.copyfile(TEMPLATE.parent / "host.js", stage / "host.js")
        (stage / "spine-webgl.js").write_bytes(runtime)
        (stage / "SPINE-RUNTIMES-LICENSE.txt").write_bytes(license_text)
        # A classic script works offline from file: without weakening the browser's local-file policy.
        (stage / "model-data.js").write_text("window.NEKO_SPINE = " + json.dumps(data) + ";\n", encoding="ascii")
        (stage / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf8")
        stage.rename(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skeleton", type=Path)
    parser.add_argument("--dest", type=Path, required=True, help="New project directory")
    args = parser.parse_args()
    print(build(args.skeleton, args.dest))
