"""Discover read-only package sources and keep raw header probes separate from resolved names."""
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path

from . import resource_names
from .import_bundles import Image, UnityPy, MemoryFileSystem, files, key

FORMATS = {b"UnityFS\x00": "unity", b"DDS ": "dds", b"@UTF": "acb", b"AFS2": "awb", b"CRID": "usm",
           b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpeg"}
PROJECT_CATALOG = "asset_bundles/app_data/scriptable_objects/project_resource.ab"
CHARA_DDS_ORIENTATION = "chara-dds-top-down-v1"
IMAGE_KINDS = frozenset({"dds", "png", "jpeg"})


@dataclass(frozen=True)
class Source:
    relative: str
    path: Path
    kind: str
    revision: tuple[int, int]
    name: str = ""
    supplemental: bool = False

    @property
    def display_path(self):
        return Path(self.name) if self.name else self.path

    @property
    def signature(self):
        return [*self.revision, self.kind, *([self.name] if self.name else [])]

    @property
    def output_revision(self):
        return [*self.signature, CHARA_DDS_ORIENTATION] if character_dds(self.name) and self.kind == "dds" else self.signature

    def open(self):
        return open_input(self.path, self.name)

    def image_ref(self, kind=None):
        return dict(source=self.relative, title=self.display_path.stem, serialized_file=None, path_id=None,
                    type=kind or self.kind, kind="image")


def read(path, default):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def write(path, data):
    pending = path.with_suffix(".partial")
    pending.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    pending.replace(path)


def stamp(path):
    s = path.stat()
    return [s.st_size, s.st_mtime_ns]


def inventory(game, previous, issue, assets=None):
    areas = [game / rel for rel in ("resources", "NekoConne_Data/CYU6", "NekoConne_Data/Resources",
                                    "NekoConne_Data/StreamingAssets", "NekoConne_Data/data.unity3d")]
    found = {}
    roots = [(root, game, "") for root in areas]
    if assets:
        roots.append((assets, assets, supplemental_prefix(assets)))
    for root, base, prefix in roots:
        if not root.exists():
            continue
        for p, _ in files(root, lambda p, err: issue(p, err, "failed")):
            rel = prefix + p.relative_to(base).as_posix()
            try:
                sig = stamp(p)
                if previous.get(rel, ())[:2] == sig and (previous[rel][2] != "unknown" or len(previous[rel]) == 4):
                    found[rel] = previous[rel]
                    continue
                with p.open("rb") as stream:
                    magic = stream.read(8)
                kind = next((kind for key, kind in FORMATS.items() if magic.startswith(key)), "unknown")
                if p.name.casefold().endswith(".model3.json"):
                    kind = "loose_live2d"
                found[rel] = [*sig, kind, *([magic.hex()] if kind == "unknown" else [])]
            except OSError as exc:
                issue(rel, str(exc), "failed")
    return found


def supplemental_prefix(assets):
    return "supplement-" + key(os.path.normcase(str(assets))) + "/"


def input_path(game, assets, rel):
    prefix = supplemental_prefix(assets) if assets else None
    return assets / rel[len(prefix):] if prefix and rel.startswith(prefix) else game / rel


def open_input(path, logical):
    return io.BytesIO(resource_names.decode(path.read_bytes(), logical)) if logical else path.open("rb")


def load_env(stream, name):
    env = UnityPy.Environment(fs=MemoryFileSystem(), path="")
    env.load_file(stream, name=name)
    return env


def resolve_wrapped(game, assets, scanned, listing, issue):
    proposed = resource_names.names_from_file(listing) if listing else set()
    unknown = {}
    for rel, sig in scanned.items():
        if sig[2] == "unknown":
            unknown.setdefault(bytes.fromhex(sig[3]), []).append(rel)

    def match(names):
        found = {}
        for probe, options in resource_names.candidates(names).items():
            if len(options) == 1:
                for rel in unknown.get(probe, ()):
                    found[rel] = options[0]
        return found

    records = []
    seeds = match({PROJECT_CATALOG})
    for rel, (logical, _) in seeds.items():
        try:
            with open_input(input_path(game, assets, rel), logical) as stream:
                env = load_env(stream, logical)
                for obj in env.objects:
                    if obj.type.name != "MonoBehaviour":
                        continue
                    tree = obj.read_typetree(check_read=False)
                    records.extend(tree.get("_assetBundleResources", []))
                    proposed.update(row["_assetBundleName"] for row in records if row.get("_assetBundleName"))
                    proposed.update(name for row in tree.get("_webResources", [])
                                    if resource_names.NAME.fullmatch(name := "PC/" + row.get("_path", "") + ".dds"))
        except Exception as exc:
            issue(rel, str(exc), "catalog_failed")
    wrapped = match(proposed | {PROJECT_CATALOG})
    result = dict(scanned)
    for rel, (logical, kind) in wrapped.items():
        result[rel] = [*scanned[rel][:2], kind, logical]
    return result, records


def character_dds(logical):
    folder, _, name = logical.rpartition("/")
    return folder == "PC/Images/Chara" and name.endswith(".dds")


def display_bitmap(im, logical):
    # These game textures are stored bottom-up. Unity's Texture2D/Sprite decoder handles its own orientation.
    return im.transpose(Image.Transpose.FLIP_TOP_BOTTOM) if im.format == "DDS" and character_dds(logical) else im


def check_paths(game, cache, assets):
    if not game.is_dir() or game.is_relative_to(cache) or cache.is_relative_to(game):
        raise ValueError("Keep the game installation and export cache separate.")
    if assets and (not assets.is_dir() or assets.is_relative_to(cache) or cache.is_relative_to(assets)
                   or assets.is_relative_to(game) or game.is_relative_to(assets)):
        raise ValueError("The additional asset directory must be separate from the game and cache.")
    if (cache / "builtin").exists():
        raise ValueError("This is an old cache. Select a new empty folder.")


def discover(game, cache, assets, listing, issue):
    idx = cache / "index"
    idx.mkdir(parents=True, exist_ok=True)
    raw_store = idx / "inventory.json"
    before = read(raw_store, {})
    previous = before["files"] if before.get("game") == str(game) else {}
    raw = inventory(game, previous, issue, assets)
    write(raw_store, {"game": str(game), "files": raw})
    resolved, records = resolve_wrapped(game, assets, raw, listing, issue)
    prefix = supplemental_prefix(assets) if assets else ""
    sources = {}
    for rel, data in resolved.items():
        kind = data[2]
        name = data[3] if len(data) == 4 and kind != "unknown" else ""
        extra = bool(prefix and rel.startswith(prefix))
        path = assets / rel[len(prefix):] if extra else game / rel
        sources[rel] = Source(rel, path, kind, tuple(data[:2]), name, extra)
    return sources, records
