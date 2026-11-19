"""Build the role picker index and thumbnails without exporting assets."""
from collections import Counter
from pathlib import Path

from . import character_index, export_cubism
from .import_bundles import Importer
from .resource_sources import IMAGE_KINDS, Image, check_paths, discover, display_bitmap, load_env, read, write


class PreviewImporter(Importer):
    def image(self, bitmap, ref):
        bitmap = bitmap.convert("RGBA")
        bitmap.thumbnail((96, 96), Image.Resampling.LANCZOS)
        super().image(bitmap, ref)


def inspect_preview(source, root):
    importer = PreviewImporter(root)
    models = []
    with source.open() as stream:
        if source.kind == "unity":
            env = load_env(stream, source.name or source.relative)
            importer.read_env(env, source.relative, spine=False, accept_image=character_index.avatar)
            if not source.name:
                models.extend(character_index.identifier(title, "live2d") for title in export_cubism.catalog(env))
                for obj in env.objects:
                    if obj.type.name == "TextAsset" and (title := obj.peek_name() or "").endswith(".skel"):
                        models.append(character_index.identifier(title, "spine"))
        else:
            with Image.open(stream) as im:
                importer.image(display_bitmap(im, source.name), source.image_ref(im.format))
    return dict(signature=source.output_revision, avatars=importer.assets, models=[m for m in models if m],
                issues=importer.issues)


def role_count(source):
    if source.kind in IMAGE_KINDS:
        return character_index.image_id(source.image_ref())
    if source.kind == "loose_live2d":
        return character_index.identifier(source.path.name.removesuffix(".model3.json"), "live2d")
    return character_index.package_id(source.name)


def catalog(game, cache, assets=None, listing=None):
    game, cache = game.resolve(), cache.resolve()
    check_paths(game, cache, assets)
    issues = []

    def issue(source, reason, status):
        issues.append(dict(source=source, reason=reason, status=status))

    sources, records = discover(game, cache, assets, listing, issue)
    avatar_bundles = {row["_assetBundleName"] for row in records if row.get("_assetBundleName") and
                      character_index.avatar(character_index.bundle_image(row))}
    idx = cache / "index"
    snapshot_path = idx / "catalog-state.json"
    old = read(snapshot_path, {})
    previous = old.get("entries", {}) if old.get("game") == str(game) else {}
    snapshots, portraits, counts = {}, [], Counter()
    preview_root = idx / "catalog-previews"

    for rel, source in sources.items():
        if role := role_count(source):
            counts[role] += 1
        inspect = (source.kind == "unity" and (not source.name or source.name in avatar_bundles)
                   or source.kind in IMAGE_KINDS and character_index.avatar(source.image_ref()))
        if not inspect:
            continue
        saved = previous.get(rel, {})
        if saved.get("signature") != source.output_revision or any(
                not (preview_root / row["file"]).is_file() for row in saved["avatars"]):
            try:
                saved = inspect_preview(source, preview_root)
            except Exception as exc:
                issue(rel, str(exc), "catalog_failed")
                continue
        snapshots[rel] = saved
        issues.extend(saved.get("issues", []))
        counts.update(role for raw in saved["models"] if (role := character_index.normalize_role(raw)))
        portraits.extend({**row, "file": (Path("index/catalog-previews") / row["file"]).as_posix()}
                         for row in saved["avatars"])

    entries = character_index.index(portraits)
    for role in sorted(entries.keys() | counts.keys()):
        entries.setdefault(role, {"names": {}, "avatars": []})["assets"] = counts[role]
    write(snapshot_path, {"game": str(game), "entries": snapshots})
    write(idx / "catalog.json", dict(characters=entries, issues=issues,
                                    files=dict(Counter(source.kind for source in sources.values())),
                                    named_bundles=sum(source.kind == "unity" and bool(source.name) for source in sources.values())))
    print(f"Catalog: {len(entries)} IDs, {len(portraits)} verified avatars; see index/catalog.json.", flush=True)
