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

ROOT = Path(__file__).resolve().parent
MODEL_KINDS = {"cubism", "spine"}


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
    external_id: str = ""


def library(cache):
    cache = Path(cache).resolve()
    items = []
    for kind, pattern in (("cubism", "builtin/cubism/*/*.model3.json"),
                          ("spine", "builtin/spine/*/*.skel"), ("image", "builtin/images/*.png"),
                          ("atlas", "builtin/unpaired-pages/*/*.png"), ("skin", "skins/*/*.png"),
                          ("image", "images/*/*/*.png"), ("portrait", "portraits/*.png"),
                          ("portrait", "renders/*/*.png"), ("video", "renders/*/*.webm"),
                          ("video", "video/*.webm")):
        for path in cache.glob(pattern):
            preview = path if path.suffix == ".png" else None
            if kind in MODEL_KINDS:
                title = path.parent.name
                candidate = cache / "portraits" / (title + ".png")
                if not candidate.is_file():
                    candidate = next((cache / "renders" / title).glob("*.png"), candidate)
                preview = candidate if candidate.is_file() else None
            else:
                title = path.stem
                if path.suffix == ".webm":
                    candidate = (path.parent / "previews" / (path.stem + ".jpg")
                                 if path.parent == cache / "video" else path.with_suffix(".png"))
                    preview = candidate if candidate.is_file() else None
            items.append(Asset(kind, title, path, preview))
    for index in (cache / "external").glob("*/manifest.json"):
        base = index.parent.resolve()
        if not base.is_relative_to(cache):
            continue
        for row in json.loads(index.read_text(encoding="utf-8-sig")).get("assets", []):
            kind = row.get("kind")
            path = (base / row["file"]).resolve()
            if kind not in {"image", "atlas", "spine"} or not path.is_relative_to(base) or not path.is_file():
                continue
            suffix = hashlib.sha256(path.relative_to(base).as_posix().encode()).hexdigest()[:12]
            items.append(Asset(kind, row["title"], path, path if path.suffix.lower() == ".png" else None,
                               base.name + "-" + suffix))
    # Use decoded manifests instead of stat'ing tens of thousands of audio files on every refresh.
    seen = set()
    for index in sorted((cache / "audio").glob("decoded-*.csv")):
        with index.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                rel = Path(row.get("wav") or "")
                if rel.suffix != ".wav" or rel.anchor or ".." in rel.parts or rel in seen:
                    continue
                seen.add(rel)
                items.append(Asset("audio", row.get("cue") or rel.stem, cache / "audio" / rel))
    return sorted(items, key=lambda a: (a.kind, a.title.casefold(), str(a.path)))


def jobs(game, cache):
    game, cache = Path(game).resolve(), Path(cache).resolve()
    if game.is_relative_to(cache) or cache.is_relative_to(game):
        raise ValueError("Keep the game installation and export cache separate.")
    bundle = game / "NekoConne_Data/data.unity3d"
    if not bundle.is_file() or not (game / "resources").is_dir():
        raise ValueError("Select the NekoConne installation, containing NekoConne_Data and resources.")
    def cmd(script, *args):
        return command(script, *args)
    return {
        "builtin": cmd("export_builtin.py", bundle, "--out", cache / "builtin"),
        "cubism": cmd("export_cubism.py", bundle, "--out", cache / "builtin/cubism"),
        "images": cmd("export_images.py", bundle, "--out", cache / "images"),
        "audio": cmd("export_audio.py", "--game", game, "--out", cache / "audio", "--reindex", "--select", "^voice_"),
        "video": cmd("export_video.py", "--source", game / "resources", "--out", cache / "video"),
    }


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
    cached = None if asset.external_id else asset.path.parents[3] / "renders" / asset.path.parent.name / "spine.json"
    if cached and cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8-sig"))
    from tools.render_spine import query
    return query(asset.path)


def render_paths(asset, cache, group, index):
    directory = Path(cache).resolve() / "renders" / (asset.external_id or asset.path.parent.name)
    stem = re.sub(r"[^\w-]", "_", f"{asset.path.parent.name}-{group}-{index}")
    return directory / (stem + ".png"), directory / (stem + ".webm")


def render_command(asset, cache, group, index):
    if (group, index) not in motion_choices(asset).values():
        raise ValueError("Unknown model motion")
    png, video = render_paths(asset, cache, group, index)
    if asset.kind == "spine":
        anim = spine_meta(asset)["animations"][index]["name"]
        return command("render_spine.py", asset.path,
                       "--animation", anim, *(("--skin", group) if group else ()), "--png", png, "--webm", video)
    return command("render_wallpaper.py", asset.path, "--group", group, "--index", index,
                   "--width", "1200", "--height", "1400", "--scale", "0.85", "--png", png, "--webm", video)


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
        return build(asset, asset.path.parents[3], Path(destination))
    if asset.kind not in {"image", "portrait", "skin", "video"}:
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
        tag = (f'<video src="{filename}" muted autoplay loop playsinline></video>' if asset.kind == "video"
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
