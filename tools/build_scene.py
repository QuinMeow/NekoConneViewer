"""Assemble a local Wallpaper Engine project from exported art and rendered motions."""
import argparse
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from viewer_core import Asset, motion_choices, render_command, render_paths


def build(asset, cache, destination, background=None, render_missing=False):
    sys.path.insert(0, str(ROOT / ".tools/unity"))
    from PIL import Image
    cache, destination = Path(cache), Path(destination)
    target = destination / ("NekoConne-" + asset.path.parent.name)
    if target.exists():
        raise FileExistsError(target)
    art = cache / "builtin/images"
    bg = Path(background) if background else art / "bg_quest_upside_01.png"
    if not bg.is_file():
        raise FileNotFoundError("请先导出精选场景图片，或在命令行指定 --background。")
    groups = list(motion_choices(asset).values())
    groups.sort(key=lambda key: key[0].casefold() != "idle")
    if not groups:
        raise ValueError("No motion in this model.")
    clips = []
    for group, index in groups:
        png, video = render_paths(asset, cache, group, index)
        if not (png.is_file() and video.is_file()):
            if not render_missing:
                raise ValueError(f"尚未渲染 {group}[{index}]。先用查看器渲染动作，或运行 build_scene.py --render-missing。")
            subprocess.run(render_command(asset, cache, group, index), check=True)
        clips.append((png, video))
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination) as tmp:
        stage = Path(tmp) / "project"
        stage.mkdir()
        actions = []
        for i, (_, clip) in enumerate(clips):
            filename = f"action-{i}.webm" if i else "idle.webm"
            if i:
                actions.append(filename)
            shutil.copyfile(clip, stage / filename)
        title = art / "title_bg_pc.png"
        for src, filename in ((bg, "garden.jpg"), (title if title.is_file() else bg, "art.jpg")):
            with Image.open(src) as im:
                im.convert("RGB").save(stage / filename, quality=93)
        with Image.open(clips[0][0]) as actor, Image.open(stage / "garden.jpg") as scenery:
            actor.save(stage / "portrait.webp", lossless=True)
            preview = scenery.convert("RGBA")
            preview.thumbnail((480, 320))
            char = actor.convert("RGBA")
            char.thumbnail((int(preview.width * .62), preview.height))
            preview.alpha_composite(char, (int(preview.width * .63) - char.width//2, preview.height-char.height))
            preview.convert("RGB").save(stage / "preview.jpg", quality=88)
        page = (ROOT / "wallpaper/index.html").read_text(encoding="utf8")
        page = page.replace('<html ', f'<html data-actions="{html.escape(json.dumps(actions), quote=True)}" ', 1)
        (stage / "index.html").write_text(page, encoding="utf8")
        project = json.loads((ROOT / "wallpaper/project.json").read_text(encoding="utf8"))
        project.update(title="NekoConne | " + asset.title, description="Local Cubism motion study. Click the character to play an action.")
        (stage / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf8")
        stage.rename(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="exported .model3.json")
    parser.add_argument("--cache", type=Path, default=ROOT / "exports")
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--background", type=Path)
    parser.add_argument("--render-missing", action="store_true")
    a = parser.parse_args()
    print(build(Asset("cubism", a.model.parent.name, a.model.resolve()), a.cache, a.dest, a.background, a.render_missing))
