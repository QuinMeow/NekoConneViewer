"""Scan an installed copy once, then export the selected categories."""
import argparse
from collections import Counter
import csv
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace

from viewer_runtime import tool_home
from . import character_index, export_audio, export_cubism, export_video
from .asset_catalog import catalog
from .import_bundles import Importer, files, key, linked
from .resource_sources import FORMATS, IMAGE_KINDS, Image, check_paths, discover, display_bitmap, load_env, read, stamp, write

CATEGORIES = ("images", "spine", "live2d", "audio", "video")
def loose_model(path):
    stat = read(path, {})
    refs = stat["FileReferences"]
    refs_list = [refs["Moc"], *refs["Textures"]]
    for motions in refs.get("Motions", {}).values():
        refs_list.extend(x["File"] for x in motions)
    refs_list.extend(x["File"] for x in refs.get("Expressions", []))
    refs_list.extend(refs[k] for k in ("Physics", "Pose", "UserData", "DisplayInfo") if refs.get(k))
    for motions in refs.get("Motions", {}).values():
        refs_list.extend(x["Sound"] for x in motions if x.get("Sound"))
    base = path.parent.resolve()
    files = []
    for rel in dict.fromkeys(refs_list):
        ref = (base / rel).resolve(strict=True)
        if not ref.is_relative_to(base) or not ref.is_file():
            raise ValueError(f"Model reference is not a local file: {rel}")
        files.append(ref)
    with files[0].open("rb") as stream:
        if stream.read(4) != b"MOC3":
            raise ValueError("Referenced model has no MOC3 signature")
    return files


def unpack(source, cache, selected, workspace, scope, references=()):
    results = {cat: {"assets": [], "issues": []} for cat in selected}
    path, rel = source.path, source.relative
    package_id = character_index.package_id(source.name)

    def matches(name, category):
        return scope.allows(character_index.identifier(name, category) or package_id)

    def image_match(row):
        return scope.allows(character_index.image_id({"kind": "image", **row}) or package_id)

    if source.kind == "loose_live2d":
        title = path.name[:-len(".model3.json")]
        if "live2d" in selected and matches(title, "live2d"):
            folder = cache / "live2d" / f"{path.stem}-{key(rel)}"
            outputs = []
            for f in [path, *references]:
                target = folder / f.relative_to(path.parent)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                outputs.append(target.relative_to(cache).as_posix())
            results["live2d"]["assets"] = [dict(title=title, kind="cubism", source=rel, file=outputs[0], outputs=outputs)]
        return results
    worker = Importer(workspace)
    if source.kind in IMAGE_KINDS:
        if image_match(source.image_ref()):
            with source.open() as stream, Image.open(stream, formats=("DDS", "PNG", "JPEG")) as im:
                worker.image(display_bitmap(im, source.name), source.image_ref(im.format))
        results["images"]["assets"] = publish_images(cache, workspace, worker.assets)
        return results

    with source.open() as stream:
        env = load_env(stream, source.name or rel)
        if selected & {"images", "spine"}:
            worker.read_env(env, rel, images="images" in selected, spine="spine" in selected,
                            accept_image=image_match if scope.filters else None)
        if "images" in results:
            results["images"] = {"assets": [r for r in worker.assets if not r.get("private_texture")],
                                  "issues": [r for r in worker.issues if r.get("type") != "TextAsset"]}
        if "spine" in results:
            worker.models((lambda title: matches(title, "spine")) if scope.filters else None)
            models = [r for r in worker.assets if r["kind"] == "spine"]
            for r in models:
                folder = Path(r["file"]).parent
                r["outputs"] = [(folder / f.name).as_posix() for f in (workspace / folder).iterdir() if f.is_file()]
                shutil.copytree(workspace / folder, cache / folder, dirs_exist_ok=True)
            results["spine"] = {"assets": models, "issues": [r for r in worker.issues if r.get("type") == "TextAsset" or r.get("status")]}
        publish_images(cache, workspace, results.get("images", {}).get("assets", []))
        if "live2d" in selected:
            report = export_cubism.export(env, cache / "live2d",
                                          accept=(lambda title: matches(title, "live2d")) if scope.filters else None,
                                          source_id="" if rel == "NekoConne_Data/data.unity3d" else rel)
            results["live2d"] = cubism_rows(rel, report)
    return results


def publish_images(cache, workspace, rows):
    for row in rows:
        target = cache / row["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        (workspace / row["file"]).replace(target)
    return rows


def cubism_rows(rel, report):
    rows = []
    for model in report["models"]:
        name = Path("live2d") / model["file"]
        rows.append(dict(kind="cubism", title=model["name"], source=rel, path_id=model["path_id"], file=name.as_posix(),
                         motion_timing_defaults=model.get("motion_timing_defaults", []),
                         outputs=[(Path("live2d") / file).as_posix() for file in model["outputs"]]))
    return {"assets": rows, "issues": report["issues"]}


def reusable(cache, row, revision, scope_fields):
    if row.get("stamp") != revision or row.get("retry") or any(row.get(k) != v for k, v in scope_fields.items()):
        return False
    return all((cache / file).is_file()
               for asset in row["assets"] for file in asset.get("outputs", [asset["file"]]))


def write_characters(idx):
    rows = [row for manifest in sorted(idx.glob("assets*.json")) for row in read(manifest, [])]
    write(idx / "characters.json", character_index.index(rows))


def import_local(source, cache):
    if linked(source):
        raise ValueError("Select a regular file or directory.")
    source, cache = source.resolve(strict=True), cache.resolve()
    if source.is_relative_to(cache) or cache.is_relative_to(source) or (cache / "builtin").exists():
        raise ValueError("Select a new cache separate from the input.")
    idx = cache / "index"
    idx.mkdir(parents=True, exist_ok=True)
    ident = key(os.path.normcase(str(source)))
    work = Importer(cache)
    for p, rel in files(source, work.issue):
        ref = f"import-{ident}/{rel}"
        try:
            with p.open("rb") as stream:
                magic = stream.read(8)
            fmt = next((kind for h, kind in FORMATS.items() if magic.startswith(h)), None)
            if fmt == "unity":
                work.bundle(p, ref)
            elif fmt in {"dds", "png", "jpeg"}:
                with Image.open(p, formats=("DDS", "PNG", "JPEG")) as im:
                    work.image(im, dict(source=ref, title=p.stem, serialized_file=None, path_id=None, type=im.format))
            else:
                work.issue(ref, "No supported image or Unity bundle signature", status="unrecognized")
        except Exception as exc:
            work.issue(ref, str(exc), status="failed")
    work.models()
    for row in work.assets:
        row["category"] = "spine" if row["kind"] == "spine" else "images"
        if row["category"] == "spine":
            folder = (cache / row["file"]).parent
            row["outputs"] = [f.relative_to(cache).as_posix() for f in folder.iterdir() if f.is_file()]
    write(idx / f"assets-{ident}.json", work.assets)
    write(idx / f"report-{ident}.json", {"input": source.name, "issues": work.issues})
    write_characters(idx)
    print(f"{len(work.assets)} imported assets, {len(work.issues)} issues", flush=True)


def media(cache, game, scanned, selected, index_dir, scope):
    home = tool_home()
    vgm = next((home / "audio").glob("vgmstream-*/vgmstream-cli.exe"), None)
    ffmpeg = next((home / "video").rglob("ffmpeg*.exe"), None)
    signatures = {k: stamp(p) if p else None for k, p in (("vgmstream", vgm), ("ffmpeg", ffmpeg))}
    if "audio" in selected and any(r[2] == "acb" for r in scanned.values()):
        dest = cache / "audio"
        dest.mkdir(exist_ok=True)
        reports = index_dir / "audio"
        reports.mkdir(exist_ok=True)
        state = read(index_dir / "audio-inputs.json", {})
        sources = {p: info for p, info in scanned.items() if info[2] in {"acb", "awb"}}
        old = state.get("sources", {})
        if state.get("game") != str(game) or signatures["vgmstream"] != state.get("codec"):
            old = {}
        old_sheets = read(reports / "sheets.json", []) if old else []
        sheets = old_sheets if old == sources and old_sheets else export_audio.catalog(
            game, dest, [game / p for p in sources], index_dir=reports)
        sheets = [s for s in sheets if scope.allows(character_index.identifier(s["name"], "audio"))]
        old_pairs = {(s["acb"], s["awb"]) for s in old_sheets}
        reused = {s["acb"] for s in sheets if (s["acb"], s["awb"]) in old_pairs and
                  all(p in old and old[p] == sources[p] for p in (s["acb"], s["awb"]) if p)}
        if not vgm:
            yield dict(category="audio", status="missing_dependency", reason="vgmstream-cli")
        elif sheets:
            export_audio.decode(sheets, SimpleNamespace(game=game, out=dest, jobs=min(6, os.cpu_count() or 1),
                                                        cli=vgm, select="", reuse=reused, index_dir=reports))
            write(index_dir / "audio-inputs.json", dict(game=str(game), sources=sources, codec=signatures["vgmstream"]))
            for name in ("failed-all.csv", "index-errors.csv"):
                with (reports / name).open(encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        yield dict(category="audio", source=row.get("acb") or row.get("source"), status="failed", reason=row["error"])
    if "video" in selected and scope.roles:
        yield dict(category="video", status="excluded_by_filter", reason="These clips have no verified character association.")
    if "video" in selected and not scope.roles and any(r[2] == "usm" for r in scanned.values()):
        clips = {p: info for p, info in scanned.items() if info[2] == "usm"}
        record = index_dir / "video-inputs.json"
        state = read(record, {})
        old = state.get("sources", {}) if state.get("game") == str(game) and state.get("codecs") == signatures else {}
        if not ffmpeg:
            yield dict(category="video", status="missing_dependency", reason="FFmpeg")
        else:
            reports = index_dir / "video"
            reports.mkdir(exist_ok=True)
            args = SimpleNamespace(source=game, out=cache / "video", ffmpeg=ffmpeg, vgmstream=vgm,
                                   jobs=min(2, os.cpu_count() or 1), retry_failed=False, index_dir=reports)
            for row in export_video.export_all(args, [game / p for p in clips], {p for p in clips if old.get(p) == clips[p]}):
                if row["status"] != "decoded" or "omitted" in row["audio"]:
                    yield dict(category="video", source=row["source"], status="failed" if not row["file"] else "missing_dependency",
                               reason=row["status"] if not row["file"] else row["audio"])
            write(record, dict(game=str(game), sources=clips, codecs=signatures))


def run(game, cache, selected, characters=(), assets=None, listing=None, include_other_roles=False):
    game, cache = game.resolve(), cache.resolve()
    check_paths(game, cache, assets)
    scope = character_index.RoleScope.from_ids(characters, include_other_roles)
    scope_fields = scope.manifest()
    idx = cache / "index"
    idx.mkdir(parents=True, exist_ok=True)
    scan_path = idx / "scan.json"
    last = read(scan_path, {})
    if last.get("game") and last["game"] != str(game):
        raise ValueError("Select a separate cache for this installation.")
    problems = []
    def issue(path, reason, status):
        problems.append(dict(source=str(path), status=status, reason=reason))
    scanned, records = discover(game, cache, assets, listing, issue)
    counters = Counter(source.kind for source in scanned.values())
    print("Input signatures:", dict(counters), flush=True)
    problems.extend(dict(source=p, status="unrecognized", reason="No supported signature")
                    for p, source in scanned.items() if source.kind == "unknown")
    state = last.get("assets", {})
    active = selected & {"images", "spine", "live2d"}
    associated_images = {}
    for row in records:
        number = character_index.image_id(character_index.bundle_image(row))
        if number and row.get("_assetBundleName"):
            associated_images.setdefault(row["_assetBundleName"], set()).add(number)
    hits = rebuilt = 0
    revisions = {}
    for rel, source in scanned.items():
        references = []
        signature = source.output_revision
        if source.kind == "loose_live2d" and ("live2d" in active or rel in state):
            try:
                references = loose_model(source.path)
                signature = [*signature, [[path.relative_to(source.path.parent).as_posix(), *stamp(path)]
                                         for path in references]]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                problems.append(dict(source=rel, category="live2d", status="failed", reason=str(exc)))
                continue
        revisions[rel] = signature
        categories = categories_for(source, active, scope, associated_images)
        if not categories:
            continue
        sources = state.setdefault(rel, {})
        pending = {k for k in categories if not reusable(cache, sources.get(k, {}), signature, scope_fields)}
        hits += len(categories - pending)
        if pending:
            rebuilt += len(pending)
            with tempfile.TemporaryDirectory(dir=idx) as temp:
                try:
                    results = unpack(source, cache, pending, Path(temp), scope, references)
                except Exception as exc:
                    results = {k: {"assets": [], "retry": True,
                                   "issues": [dict(source=rel, status="failed", reason=str(exc))]} for k in pending}
            for category, result in results.items():
                if source.name:
                    for asset in result["assets"]:
                        asset["resource_name"] = source.name
                sources[category] = dict(stamp=signature, **scope_fields, **result)
    output_assets = []
    for rel, categories in state.items():
        if rel not in scanned:
            continue
        for category, row in categories.items():
            if row["stamp"] == revisions.get(rel):
                output_assets.extend(dict(category=category, **it) for it in row["assets"])
                problems.extend({"category": category, "status": "failed", **it} for it in row["issues"])
    write(idx / "assets.json", output_assets)
    write_characters(idx)
    write(scan_path, dict(game=str(game), assets={r: v for r, v in state.items() if r in scanned}))
    report = idx / "report.json"
    previous_report = read(report, {})
    problems.extend(row for row in previous_report.get("issues", [])
                    if row.get("category") in {"audio", "video"} - selected)
    problems.extend(media(cache, game, {p: source.signature for p, source in scanned.items() if not source.supplemental},
                          selected, idx, scope))
    counts = Counter(row["category"] for row in output_assets)
    for category, rel in (("audio", "audio/decoded-all.csv"), ("video", "video/index.csv")):
        manifest = idx / rel
        if manifest.is_file():
            with manifest.open(encoding="utf-8-sig", newline="") as f:
                counts[category] = sum(bool(row.get("wav") or row.get("file")) for row in csv.DictReader(f))
    write(report, dict(signatures=counters, outputs=counts, issues=problems, reused=hits, rebuilt=rebuilt, **scope_fields,
                       named_bundles=sum(source.kind == "unity" and bool(source.name) for source in scanned.values())))
    print(f"Indexed {len(output_assets)} visual assets, reused {hits} source/category pairs, processed {rebuilt}; see index/report.json", flush=True)


def categories_for(source, active, scope, bundle_image_roles):
    if source.kind == "loose_live2d":
        return active & {"live2d"} if scope.allows(character_index.identifier(
            source.path.name.removesuffix(".model3.json"), "live2d")) else set()
    if source.kind in IMAGE_KINDS:
        return active & {"images"} if scope.allows(character_index.image_id(source.image_ref())) else set()
    if source.kind != "unity":
        return set()
    if not source.name or scope.allows(character_index.package_id(source.name)):
        return active
    return active & {"images"} if any(scope.allows(n) for n in bundle_image_roles.get(source.name, ())) else set()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--game", type=Path)
    mode.add_argument("--input", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--assets", type=Path, help="additional directory of standard Unity bundles, textures, or complete Cubism sets")
    p.add_argument("--resource-names", type=Path, help="additional local resource names (.txt / .json / .md)")
    p.add_argument("--character", action="append", default=[])
    p.add_argument("--include-other-roles", action="store_true")
    p.add_argument("--catalog", action="store_true")
    p.add_argument("categories", nargs="*")
    a = p.parse_args()
    if a.input:
        if a.categories:
            p.error("Categories apply to --game only")
        import_local(a.input, a.out)
    else:
        assets = a.assets.resolve(strict=True) if a.assets else None
        listing = a.resource_names
        if not listing:
            default = os.environ.get("NEKOCONNE_RESOURCE_NAMES")
            listing = Path(default) if default else tool_home() / "resource-names.txt"
            if not default and not listing.is_file():
                listing = None
        if a.catalog:
            if a.categories or a.character:
                p.error("--catalog discovers roles without exporting")
            catalog(a.game, a.out, assets, listing)
        elif not a.categories or not set(a.categories) <= set(CATEGORIES):
            p.error("Select one or more categories: " + ", ".join(CATEGORIES))
        else:
            if any(not re.fullmatch(r"\d{5}|\d{7,8}", n) for n in a.character):
                p.error("Character identifiers must be five, seven, or eight digits")
            run(a.game, a.out, set(a.categories), a.character, assets, listing, a.include_other_roles)
