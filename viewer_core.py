"""Local asset library and export operations shared by the desktop viewer."""
from dataclasses import dataclass
import csv
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from viewer_runtime import command, tool_home
from tools.character_index import identifier, image_id, package_id, normalize_role, implemented_role, merge_role_catalog

ROOT = Path(__file__).resolve().parent
MODEL_KINDS = {"cubism", "spine"}
CATEGORIES = {"images": "图片", "spine": "Spine", "live2d": "Live2D", "audio": "音频", "video": "游戏影片"}
KINDS = {"image": "图片", "atlas": "图集贴图", "spine": "骨架模型", "cubism": "模型",
         "texture": "模型贴图", "portrait": "渲染静帧", "action": "动作视频", "video": "游戏影片", "audio": "音频"}


def detect_game():
    env = os.environ.get("NEKOCONNE_GAME")
    if env and (Path(env) / "NekoConne_Data/data.unity3d").is_file():
        return Path(env)
    homes = [Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam"]
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                homes.insert(0, Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
    libraries = list(homes)
    for home in homes:
        try:
            vdf = (home / "steamapps/libraryfolders.vdf").read_text(encoding="utf-8-sig")
        except OSError:
            continue
        libraries.extend(Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s*"([^"]+)"', vdf))
    for library_root in libraries:
        path = library_root / "steamapps/common/NekoConne"
        if (path / "NekoConne_Data/data.unity3d").is_file():
            return path
    return None


@dataclass(frozen=True)
class Asset:
    kind: str
    title: str
    path: Path
    preview: Path | None = None
    category: str = ""
    character_id: str = ""
    note: str = ""


def library(cache):
    cache = Path(cache).resolve()
    if (cache / "builtin").exists():
        return []
    items = []
    meta = cache / "index/characters.json"
    characters = merge_role_catalog(json.loads(meta.read_text(encoding="utf8"))) if meta.is_file() else {}
    indexed = set()
    models = {}
    def note(role):
        return " / ".join(characters.get(role, {}).get("names", {}).values())

    for manifest in sorted((cache / "index").glob("assets*.json")):
        for row in json.loads(manifest.read_text(encoding="utf8")):
            path = (cache / row["file"]).resolve()
            if path in indexed or not path.is_relative_to(cache) or not path.is_file():
                continue
            indexed.add(path)
            category, kind, title = row["category"], row["kind"], row["title"]
            number = image_id(row) or package_id(row.get("resource_name", "")) or identifier(title, category)
            render_dir = cache / "renders" / category / path.parent.name
            preview = path if path.suffix == ".png" else None
            if kind in MODEL_KINDS:
                models[render_dir] = number
                preview = next(render_dir.glob("*.png"), None)
            items.append(Asset(kind, title, path, preview, category=category, character_id=number, note=note(number)))
            if kind in MODEL_KINDS:
                for rel in row.get("outputs", []):
                    tex = (cache / rel).resolve()
                    if tex.is_relative_to(path.parent) and tex.suffix.lower() == ".png" and tex.is_file():
                        items.append(Asset("texture", tex.name, tex, tex, category=category, character_id=number, note=note(number)))
    for directory, role in models.items():
        for ext, kind in ((".png", "portrait"), (".webm", "action")):
            for path in directory.glob(f"*{ext}"):
                image = path.with_suffix(".png")
                items.append(Asset(kind, path.stem, path, image if image.is_file() else None,
                                   category=directory.parent.name, character_id=role, note=note(role)))
    video = cache / "video"
    manifest = cache / "index/video/index.csv"
    if manifest.is_file():
        with manifest.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                path = (video / row.get("file", "")).resolve()
                image = (video / row.get("preview", "")).resolve()
                if path.suffix == ".webm" and path.is_relative_to(video) and path.is_file():
                    preview = image if image.is_relative_to(video) and image.is_file() else None
                    items.append(Asset("video", path.stem, path, preview, category="video"))
    # Use decoded manifests instead of stat'ing tens of thousands of audio files on every refresh.
    seen = set()
    for index in sorted((cache / "index/audio").glob("decoded-*.csv")):
        with index.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                rel = Path(row.get("wav") or "")
                if rel.suffix != ".wav" or rel.anchor or ".." in rel.parts or rel in seen:
                    continue
                seen.add(rel)
                title = row.get("cue") or rel.stem
                number = identifier(row.get("sheet") or title, "audio")
                items.append(Asset("audio", title, cache / "audio" / rel, category="audio", character_id=number,
                                   note=note(number)))
    return sorted(items, key=lambda a: (a.kind, a.title.casefold(), str(a.path)))


def scan_args(game, cache, assets=None, resource_names=None):
    game, cache = Path(game).resolve(), Path(cache).resolve()
    if game.is_relative_to(cache) or cache.is_relative_to(game):
        raise ValueError("Keep the game installation and export cache separate.")
    bundle = game / "NekoConne_Data/data.unity3d"
    if not bundle.is_file() or not (game / "resources").is_dir():
        raise ValueError("Select the NekoConne installation, containing NekoConne_Data and resources.")
    if (cache / "builtin").exists():
        raise ValueError("旧版缓存不会迁移，请选择新的空目录。")
    return ["--game", game, "--out", cache, *(("--assets", Path(assets).resolve()) if assets else ()),
            *(("--resource-names", Path(resource_names).resolve()) if resource_names else ())]


def scan_command(game, cache, categories, *, characters=(), assets=None, resource_names=None, include_other_roles=False):
    return command("scan_assets.py", *scan_args(game, cache, assets, resource_names), *categories,
                   *(arg for number in characters for arg in ("--character", number)),
                   *(("--include-other-roles",) if include_other_roles else ()))


def catalog_command(game, cache, *, assets=None, resource_names=None):
    return command("scan_assets.py", *scan_args(game, cache, assets, resource_names), "--catalog")


def motion_choices(asset):
    if asset.kind == "spine":
        meta = spine_meta(asset)
        return {f"{skin or '(setup)'} / {a['name']}": (skin, i)
                for skin in (meta.get("skins") or [""]) for i, a in enumerate(meta.get("animations", []))}
    if asset.kind != "cubism":
        return {}
    groups = json.loads(asset.path.read_text(encoding="utf-8-sig"))["FileReferences"].get("Motions", {})
    return {f"{group} / {Path(m['File']).name}": (group, i)
            for group, motions in groups.items() for i, m in enumerate(motions)}


def spine_meta(asset):
    cached = asset.path.parents[2] / "renders/spine" / asset.path.parent.name / "spine.json"
    if cached.is_file() and cached.stat().st_mtime_ns >= asset.path.stat().st_mtime_ns:
        return json.loads(cached.read_text(encoding="utf-8-sig"))
    from tools.render_spine import query
    meta = query(asset.path)
    cached.parent.mkdir(parents=True, exist_ok=True)
    pending = cached.with_suffix(".partial")
    pending.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf8")
    pending.replace(cached)
    return meta


def render_paths(asset, cache, group, index, *, transparent=False):
    category = "spine" if asset.kind == "spine" else "live2d"
    directory = Path(cache).resolve() / "renders" / category / asset.path.parent.name
    raw = f"{asset.path.parent.name}-{group}-{index}"
    stem = re.sub(r"[^\w-]", "_", raw)
    if stem != raw:
        stem += "-" + hashlib.sha256(raw.encode()).hexdigest()[:10]
    if transparent and category == "live2d":
        stem += "-alpha"
    return directory / (stem + ".png"), directory / (stem + ".webm")


def render_command(asset, cache, group, index, *, width=1920, height=1080, background=None,
                   transparent=False, game=None, metadata=None):
    if metadata is None and (group, index) not in motion_choices(asset).values():
        raise ValueError("Unknown model motion")
    png, video = render_paths(asset, cache, group, index, transparent=transparent)
    if asset.kind == "spine":
        anim = (metadata or spine_meta(asset))["animations"][index]["name"]
        return command("render_spine.py", asset.path,
                       "--animation", anim, *(("--skin", group) if group else ()), "--png", png, "--webm", video)
    return command("render_native.py", asset.path, "--group", group, "--index", index,
                   "--width", width, "--height", height, "--png", png, "--webm", video,
                   *(("--game", game) if game else ()), *(("--background", background) if background else ()),
                   *(("--transparent",) if transparent else ()))


def scene_image(cache, *names):
    path = Path(cache) / "index/assets.json"
    if not path.is_file():
        return None
    images = json.loads(path.read_text(encoding="utf8"))
    for name in names:
        for row in images:
            if row["category"] == "images" and row["title"] == name:
                image = (Path(cache) / row["file"]).resolve()
                if image.is_relative_to(Path(cache).resolve()) and image.is_file():
                    return image
    return None


def copy_asset(asset, destination):
    src = asset.path.parent if asset.kind in MODEL_KINDS else asset.path
    dst = Path(destination).resolve() / src.name
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists() or dst.is_relative_to(src.resolve()):
        raise FileExistsError(f"Cannot overwrite or copy into the source: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if asset.kind in MODEL_KINDS:
        shutil.copytree(src, dst)
    else:
        with src.open("rb") as source, dst.open("xb") as out:
            shutil.copyfileobj(source, out)
    return dst


def wallpaper(asset, destination):
    if asset.kind == "cubism":
        from tools.build_scene import build
        return build(asset, asset.path.parents[2], Path(destination))
    if asset.kind not in {"image", "portrait", "video", "action"}:
        raise ValueError("Select a finished image or video. Render a model motion to video first.")
    if not asset.path.is_file():
        raise FileNotFoundError(asset.path)
    folder = Path(destination).resolve() / ("NekoConne-" + re.sub(r"[^\w-]", "_", asset.path.stem))
    folder.mkdir(exist_ok=False, parents=True)
    try:
        filename = "media" + asset.path.suffix.lower()
        shutil.copyfile(asset.path, folder / filename)
        project = {"title": "NekoConne | " + asset.title, "type": "web", "file": "index.html"}
        if asset.preview and asset.preview.is_file():
            if str(ROOT / ".tools/unity") not in sys.path:
                sys.path.insert(0, str(ROOT / ".tools/unity"))
            from PIL import Image
            with Image.open(asset.preview) as im:
                im.thumbnail((480, 320))
                matte = Image.new("RGBA", im.size, "#30343f")
                matte.alpha_composite(im.convert("RGBA"))
                matte.convert("RGB").save(folder / "preview.jpg", quality=85)
            project["preview"] = "preview.jpg"
        tag = (f'<video src="{filename}" muted autoplay loop playsinline></video>' if asset.kind in {"video", "action"}
               else f'<img src="{filename}" alt="NekoConne">')
        (folder / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>NekoConne</title>'
            '<style>*{box-sizing:border-box}body{margin:0;overflow:hidden;background:radial-gradient(ellipse '
            'at 60% 40%,#37424d,#17191e)}img,video{display:block;width:100vw;height:100vh;object-fit:contain}'
            'img{animation:drift 16s ease-in-out alternate infinite}@keyframes drift{to{transform:scale(1.025)}}'
            '@media(prefers-reduced-motion:reduce){img{animation:none}}</style>' + tag + '</html>', encoding="utf8")
        (folder / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf8")
    except Exception:
        # Only remove known files in the new, previously nonexistent project.
        for name in (filename, "preview.jpg", "index.html", "project.json"):
            (folder / name).unlink(missing_ok=True)
        folder.rmdir()
        raise
    return folder
