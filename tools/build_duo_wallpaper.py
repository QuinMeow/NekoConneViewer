"""Build the offline 10102/10106 Wallpaper Engine project from exported assets."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.duo_bake import bake
from viewer_core import scene_image


ACTORS = (("10102", 34), ("10106", 66))
BACKGROUNDS = (("title", "标题", "title_bg_pc"), ("garden", "花园", "bg_quest_upside_01"))
VOICE_TYPES = ("petCat_a", "petCat_b", "hiredCat_a", "hiredCat_b")
PROJECT = "NekoConne-Duo-10102-10106"
TEMPLATE = ROOT / "wallpaper" / "duo"


def background_image(cache, title):
    image = scene_image(cache, title)
    if image:
        return image
    # The original bulk image export used a CSV relative to the images directory.
    directory = (cache / "images").resolve()
    index = directory / "index.csv"
    if index.is_file():
        with index.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["name"] == title and row["type"] in {"Texture2D", "Sprite"}:
                    image = (directory / row["file"]).resolve()
                    if image.is_relative_to(directory) and image.is_file():
                        return image
    raise FileNotFoundError(f"Missing exported background: {title}")


def actor_model(models, number):
    title = number + "_01_S01"
    exact = models / title / (title + ".model3.json")
    if exact.is_file():
        return exact
    paths = sorted(models.glob(f"{title}-*/{title}.model3.json"))
    if len(paths) != 1:
        raise ValueError(f"Expected one exported {title} model in {models}; found {len(paths)}.")
    return paths[0]


def voice_files(cache, number):
    source = cache / "audio" / "wav" / "voice_home" / number
    voices = [source / f"voice_home_{number}_charaTap_{label}.wav" for label in VOICE_TYPES]
    for voice in voices:
        if not voice.is_file():
            raise FileNotFoundError(f"Missing exported character voice: {voice}")
    return voices


def project_metadata(actors):
    props = {"background": {"type": "combo", "order": 0, "text": "背景", "value": "garden",
                            "options": [{"label": name, "value": key} for key, name, _ in BACKGROUNDS]}}
    for actor in actors:
        for key, text, lo, hi in (("x", "水平位置", 0, 100), ("y", "脚底高度（向下）", 0, 100),
                                  ("scale", "大小（%）", 25, 180)):
            props[f"p{actor['id']}{key}"] = dict(type="slider", order=len(props), min=lo, max=hi,
                                                  value=actor[key], text=f"{actor['name']} · {text}")
    props["gaze"] = dict(type="bool", order=len(props), text="视线跟随鼠标", value=True)
    props["volume"] = dict(type="slider", order=len(props), text="语音音量", min=0, max=100, value=70)
    return {"title": "NekoConne | 10102 & 10106", "type": "web", "file": "index.html",
            "preview": "preview.jpg", "description": "点击角色播放各自的语音。在属性中切换背景、调整两人的位置和大小。",
            "general": {"properties": props}}


def build(cache, models, core, target):
    from PIL import Image, ImageOps
    cache, models, target = Path(cache), Path(models), Path(target).resolve()
    if target.exists():
        raise FileExistsError(f"Project already exists: {target}")
    core = Path(core).resolve(strict=True)
    images = [(key, name, background_image(cache, title)) for key, name, title in BACKGROUNDS]
    inputs = [(number, x, actor_model(models, number), voice_files(cache, number)) for number, x in ACTORS]
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as tmp:
        stage = Path(tmp) / "project"
        (stage / "backgrounds").mkdir(parents=True)
        scene = {"actors": [], "backgrounds": []}
        for key, name, source in images:
            path = f"backgrounds/{key}.jpg"
            with Image.open(source) as image:
                image.convert("RGB").save(stage / path, quality=94)
            scene["backgrounds"].append(dict(id=key, name=name, file=path))
        for number, x, model, voices in inputs:
            prefix = "actors/" + number
            print(f"Baking {model.stem}...", flush=True)
            baked = bake(model, core, stage / prefix, prefix)
            audio = stage / "audio" / number
            audio.mkdir(parents=True)
            for voice in voices:
                shutil.copyfile(voice, audio / voice.name)
            scene["actors"].append(dict(id=number, name=number, x=x, y=98, scale=100, model=baked,
                                        voices=[f"audio/{number}/{f.name}" for f in voices]))
        data = json.dumps(scene, ensure_ascii=False, separators=(",", ":"))
        (stage / "scene-data.js").write_text("window.NEKO_SCENE=" + data + ";\n", encoding="utf8")
        metadata = project_metadata(scene["actors"])
        (stage / "project.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf8")
        with Image.open(stage / "backgrounds/garden.jpg") as garden:
            preview = ImageOps.fit(garden.convert("RGBA"), (640, 360), Image.Resampling.LANCZOS)
        for actor in scene["actors"]:
            with Image.open(stage / actor["model"]["portrait"]) as image:
                h = round(preview.height * .85 * actor["scale"] / 100)
                sprite = image.convert("RGBA").resize((max(1, round(h * image.width / image.height)), h),
                                                      Image.Resampling.LANCZOS)
                preview.alpha_composite(sprite, (round(preview.width * actor["x"] / 100 - sprite.width / 2),
                                                 round(preview.height * actor["y"] / 100 - sprite.height)))
        preview.convert("RGB").save(stage / "preview.jpg", quality=92)
        for required in ("index.html", "scene.js", "renderer.js", "scene.css"):
            shutil.copyfile(TEMPLATE / required, stage / required)
        shutil.copyfile(TEMPLATE.parent / "host.js", stage / "host.js")
        stage.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True, help="Installed NekoConne root")
    parser.add_argument("--cache", type=Path, default=ROOT / "exports", help="Image and audio export cache")
    parser.add_argument("--models", type=Path, help="Live2D model directory (defaults to CACHE/live2d)")
    parser.add_argument("--dest", type=Path, help="New project directory (defaults to CACHE/projects/" + PROJECT + ")")
    args = parser.parse_args()
    try:
        import PIL
    except ImportError:
        sys.path.append(str(ROOT / ".tools/unity"))
    core = args.game / "NekoConne_Data/Plugins/x86_64/Live2DCubismCore.dll"
    print(build(args.cache, args.models or args.cache / "live2d", core, args.dest or args.cache / "projects" / PROJECT))


if __name__ == "__main__":
    main()
