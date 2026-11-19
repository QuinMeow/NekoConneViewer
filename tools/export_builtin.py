"""Export the directly readable built-in UnityFS media without changing the installation."""

import argparse
import csv
from collections import Counter, defaultdict
from html import escape
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools" / "unity"))
import UnityPy
from PIL import Image


SCENE = re.compile(r"^(title_bg(_pc)?|bg_(battle_[0-9]+|birthday_00|chat|dungeon_choice|dungeon_top_02|gvg_area_[ab]_0[12]|quest_upside_01|tower_00)|Icon_1024)$")
WRITABLE_NAME = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?$")


def safe_name(name):
    if not WRITABLE_NAME.fullmatch(name):
        raise ValueError(f"Unexpected asset filename: {name!r}")
    return name


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "exports/builtin")
    args = ap.parse_args()
    out = args.out
    with args.bundle.open("rb") as stream:
        if stream.read(8) != b"UnityFS\0":
            ap.error("Input must be a standard UnityFS bundle")
    env = UnityPy.load(str(args.bundle))
    objs = list(env.objects)
    kinds = Counter(obj.type.name for obj in objs)
    sources, images, texts, models = [], defaultdict(list), {}, []
    saved = set()

    def provenance(obj, path, **details):
        row = dict(file=path.relative_to(out).as_posix(), source=obj.assets_file.name,
                   path_id=obj.path_id, type=obj.type.name, name=obj.peek_name(), **details)
        sources.append(row)
        saved.add((obj.assets_file.name, obj.path_id))

    def png(obj, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        bitmap = obj.read().image
        bitmap.save(path, compress_level=3)
        with Image.open(path) as check:
            check.load()
            if check.size != bitmap.size or check.format != "PNG":
                raise ValueError(f"Invalid PNG: {path}")
        provenance(obj, path, width=bitmap.width, height=bitmap.height)

    def text_payload(obj, path):
        payload = obj.read().m_Script.encode("utf-8", "surrogateescape")
        path.write_bytes(payload)
        if path.read_bytes() != payload:
            raise ValueError(f"Payload mismatch: {path}")
        provenance(obj, path, bytes=len(payload))
        return payload

    for obj in objs:
        if obj.type.name == "Texture2D":
            name = obj.peek_name() or ""
            images[name].append(obj)
            if SCENE.fullmatch(name):
                png(obj, out / "images" / (safe_name(name) + ".png"))
        elif obj.type.name == "TextAsset":
            name = obj.peek_name()
            if name and name.endswith((".atlas", ".skel")):
                if name in texts:
                    raise ValueError(f"Duplicate model source: {name}")
                texts[name] = obj

    for atlas_name, atlas in sorted(texts.items()):
        if not atlas_name.endswith(".atlas"):
            continue
        model_id = safe_name(atlas_name[:-6])
        target = out / "spine" / model_id
        target.mkdir(parents=True, exist_ok=True)
        atlas_text = text_payload(atlas, target / safe_name(atlas_name)).decode("utf-8")
        skeleton_name = model_id + ".skel"
        skel = text_payload(texts[skeleton_name], target / skeleton_name)
        version = re.match(rb"[0-9]+\.[0-9]+\.[0-9]+$", skel[9:9 + skel[8] - 1])
        if not version:
            raise ValueError(f"Invalid Spine version in {skeleton_name}")
        pages = []
        for match in re.finditer(r"(?m)^([\w.-]+\.png)\r?\nsize:\s*(\d+),\s*(\d+)\r?$", atlas_text):
            page, w, h = match.groups()
            safe_name(page)
            candidates = images[Path(page).stem]
            local = [tex for tex in candidates if tex.assets_file.name == atlas.assets_file.name]
            matched = local or candidates
            if len(matched) != 1:
                raise ValueError(f"Missing or ambiguous texture: {atlas_name}/{page}")
            png(matched[0], target / page)
            if int(w) * sources[-1]["height"] != int(h) * sources[-1]["width"]:
                raise ValueError(f"Atlas page has unexpected aspect ratio: {page}")
            pages.append(dict(file=page, atlas_size=[int(w), int(h)],
                              png_size=[sources[-1]["width"], sources[-1]["height"]]))
        if not pages:
            raise ValueError(f"No pages in {atlas_name}")
        models.append(dict(model=model_id, version=version[0].decode(), pages=pages))

    for name, group in images.items():
        if not re.fullmatch(r"\d{7}(?:_\d+)*", name):
            continue
        for obj in group:
            if (obj.assets_file.name, obj.path_id) not in saved:
                png(obj, out / "unpaired-pages" / safe_name(obj.assets_file.name) / (safe_name(name) + ".png"))

    inventory_types = {"Sprite", "Texture2D", "SpriteAtlas", "TextAsset", "AnimationClip", "AudioClip", "VideoClip"}
    with (out / "inventory.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source", "path_id", "type", "name"])
        writer.writerows((o.assets_file.name, o.path_id, o.type.name, o.peek_name())
                         for o in objs if o.type.name in inventory_types)

    with (out / "manifest.json").open("w", encoding="utf-8") as stream:
        json.dump(dict(bundle=args.bundle.name, types=dict(kinds), spine=models, exported=sources), stream, indent=2)
    gallery = []
    for row in sources:
        if row["file"].startswith("images/"):
            path = escape(row["file"], quote=True)
            gallery.append(f'<figure><a href="{path}"><img src="{path}" loading="lazy" alt="{escape(row["name"])}"></a>'
                           f'<figcaption>{escape(row["name"])} ({row["width"]} x {row["height"]})</figcaption></figure>')
    links = "".join(f'<li><a href="spine/{m["model"]}/">{m["model"]}</a>: Spine {m["version"]}, '
                    f'{len(m["pages"])} texture page(s)</li>' for m in models)
    (out / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>NekoConne | Built-in assets</title><style>body{max-width:1100px;margin:40px auto;padding:0 20px;'
        'font:16px/1.5 sans-serif;color:#332a28;background:#faf5ee}a{color:#964429}section{display:grid;'
        'grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}figure{margin:0}img{background:#e3d9cf;'
        'width:100%;height:220px;object-fit:contain}figcaption{overflow-wrap:anywhere}</style>'
        '<h1>NekoConne / data.unity3d</h1><p>Selected built-in images and original Spine model sets. '
        'Unity UI, fonts and generic effects are indexed rather than exported. Atlas pages are model parts, not portraits.</p>'
        '<p><a href="manifest.json">Export manifest</a> | <a href="inventory.csv">All named media &amp; native clips</a> | '
        '<a href="unpaired-pages/">Textures without a matching source atlas in this bundle</a></p>'
        f'<h2>Spine models</h2><ul>{links}</ul><h2>Scenes &amp; title images</h2><section>{"".join(gallery)}</section>',
        encoding="utf-8")
    print(json.dumps(dict(spine_models=len(models), atlas_pages=sum(len(m["pages"]) for m in models),
                          scenes=len(gallery), unpaired=sum(x["file"].startswith("unpaired-pages/") for x in sources),
                          clip_inventory=kinds["AnimationClip"], audio=kinds["AudioClip"], files=len(sources))))


if __name__ == "__main__":
    main()
