"""Demux CRI USM video tracks into unchanged VP9 WebM; keep the source mapping."""
import argparse
import csv
import html
import os
import shutil
import struct
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from .usm_audio import decode
else:
    from usm_audio import decode

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "exports/video"


def sources(root):
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) == b"CRID":
                    yield path


def split(src, scratch):
    tracks = {}
    with src.open("rb") as stream:
        try:
            while h := stream.read(32):
                if len(h) != 32:
                    raise ValueError("truncated USM header")
                length = int.from_bytes(h[4:8], "big") - 24
                off, pad = h[9] - 24, int.from_bytes(h[10:12], "big")
                if off < 0 or not off + pad <= length <= 128 * 1024**2:
                    raise ValueError("invalid CRI chunk size")
                data = stream.read(length)
                if len(data) != length:
                    raise ValueError("incomplete CRI chunk")
                if h[15] != 0:
                    continue
                key = (h[:4], h[12])
                if key not in tracks:
                    tracks[key] = (scratch / f"track-{len(tracks)}").open("wb")
                tracks[key].write(data[off:length-pad])
        finally:
            for f in tracks.values():
                f.close()
    return {key: Path(f.name) for key, f in tracks.items()}


def export(src, root, dest, ffmpeg, vgmstream=None, *, output_stem=None):
    row = {"source": src.relative_to(root).as_posix(), "file": "", "audio": "",
           "frames": "", "seconds": "", "size": "", "status": "", "preview": "", "subtitles": ""}

    def run(*args):
        p = subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-xerror", "-y", *map(str, args)],
                           capture_output=True)
        if p.returncode or p.stderr.strip():
            raise ValueError(p.stderr.decode(errors="replace")[:240] or f"ffmpeg code {p.returncode}")

    try:
        with tempfile.TemporaryDirectory(dir=dest) as tmp:
            scratch = Path(tmp)
            tracks = split(src, scratch)
            videos = [p for (t, _), p in tracks.items() if t == b"@SFV"]
            audio = [p for (t, _), p in tracks.items() if t == b"@SFA"]
            subs = [(ch, p) for (t, ch), p in tracks.items() if t == b"@SBT"]
            if len(videos) != 1 or len(videos) + len(audio) + len(subs) != len(tracks):
                raise ValueError(f"unexpected track layout: {list(tracks)}")
            with videos[0].open("rb") as f:
                h = f.read(32)
            if h[:4] != b"DKIF" or h[8:12] != b"VP90":
                raise ValueError(f"unsupported video: {h[:12]!r}")
            w, ht, num, den, frames = struct.unpack_from("<HHIII", h, 12)
            row.update(frames=frames, seconds=round(frames * den / num, 3), size=f"{w}x{ht}")
            args = ["-f", "ivf", "-i", videos[0]]
            row["audio"] = "no track"
            audio_results = []
            audio_count = 0
            for number, aud in enumerate(audio):
                with aud.open("rb") as f:
                    header = bytes(v & 127 for v in f.read(96))
                pos = header.find(b"ciph")
                if pos >= 0 and header[pos+4:pos+6] == b"\x008":
                    if not vgmstream:
                        audio_results.append("omitted: keyed HCA (vgmstream unavailable)")
                        continue
                    # The CLI needs an extension. Prefer the original container for
                    # single-track USMs, but don't duplicate track 0 in multi-track files.
                    if len(audio) == 1:
                        source = scratch / "input.usm"
                        try:
                            os.link(src, source)
                        except OSError:
                            shutil.copyfile(src, source)
                    else:
                        source = aud.with_suffix(".hca")
                        aud.rename(source)
                    wav = scratch / f"audio-{number}.wav"
                    decode(source, wav, vgmstream)
                    args.extend(["-f", "wav", "-i", wav])
                else:
                    args.extend(["-f", "hca", "-i", aud])
                audio_results.append("Opus")
                audio_count += 1
            if audio_results:
                row["audio"] = "; ".join(audio_results)
            output = scratch / "out.webm"
            run(*args, "-map", "0:v", *(v for i in range(1, audio_count+1) for v in ("-map", f"{i}:a")),
                "-c:v", "copy", "-c:a", "libopus", output)
            # Decode the entire result: a successfully written container alone proves little.
            run("-threads", "2", "-i", output, "-map", "0:v:0",
                *(v for i in range(audio_count) for v in ("-map", f"0:a:{i}")), "-f", "null", "-")
            thumb = scratch / "thumb.jpg"
            run("-ss", str(min(4.0, row["seconds"] * 0.35)), "-i", output, "-frames:v", "1",
                "-vf", "scale=320:-2", thumb)
            if not thumb.is_file():
                raise ValueError("preview frame not produced")
            stem = output_stem or src.name
            dest_video = dest / (stem + ".webm")
            output.replace(dest_video)
            thumb.replace(dest / "previews" / (stem + ".jpg"))
            for ch, subtitle in subs:
                name = f"{stem}-{ch}.sbt"
                subtitle.replace(dest / name)
                row["subtitles"] += name + " "
            row.update(file=dest_video.name, preview=f"previews/{stem}.jpg", status="decoded")
    except (OSError, ValueError, struct.error) as exc:
        row["status"] = str(exc).replace("\n", " ").strip()
    return row


def export_all(a, inputs=None, reuse=None):
    ffmpeg = a.ffmpeg or next((ROOT / ".tools/video").rglob("ffmpeg-*.exe"), None) or shutil.which("ffmpeg")
    if not ffmpeg:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    vgmstream = a.vgmstream or shutil.which("vgmstream-cli") or next(
        (ROOT / ".tools/audio").glob("vgmstream-*/vgmstream-cli.exe"), None)
    (a.out / "previews").mkdir(parents=True, exist_ok=True)
    inputs = sorted(sources(a.source) if inputs is None else inputs)
    if not inputs:
        return []
    from collections import Counter
    from hashlib import sha256
    counts = Counter(p.name.casefold() for p in inputs)
    def convert(src):
        rel = src.relative_to(a.source).as_posix()
        if rel in old and counts[src.name.casefold()] == 1:
            return old[rel]
        stem = src.name
        if counts[stem.casefold()] > 1:
            stem += "-" + sha256(rel.encode()).hexdigest()[:12]
        return export(src, a.source, a.out, ffmpeg, vgmstream, output_stem=stem)
    rows = []
    old = {}
    index = getattr(a, "index_dir", a.out) / "index.csv"
    if (a.retry_failed or reuse is not None) and index.exists():
        with index.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if reuse is not None and row["source"] not in reuse:
                    continue
                if row["status"] == "decoded" and all((a.out / row[k]).is_file() for k in ("file", "preview")):
                    if vgmstream and "omitted" in row["audio"]:
                        continue
                    row.setdefault("subtitles", "")
                    old[row["source"]] = row
    staging = index.with_suffix(".partial.csv")
    with ThreadPoolExecutor(a.jobs) as pool, staging.open("w", newline="", encoding="utf-8-sig") as f:
        for row in pool.map(convert, inputs):
            if not rows:
                writer = csv.DictWriter(f, fieldnames=row)
                writer.writeheader()
            rows.append(row)
            writer.writerow(row)
            f.flush()
            print(f"{len(rows)}/{len(inputs)} {row['file']} {row['status']}", flush=True)
    staging.replace(index)
    cards = "".join(f'<a href="{html.escape(r["file"])}"><img loading="lazy" src="{r["preview"]}"><br>'
                    f'{html.escape(r["file"])}<br>{r["size"]} | {r["seconds"]}s | {r["audio"]}</a>'
                    for r in rows if r["file"])
    (a.out / "index.html").write_text('<meta charset=utf-8><title>NekoConne | USM</title>'
        '<style>body{background:#1b2126;color:#eee;font:16px sans-serif}main{display:flex;gap:1em;flex-wrap:wrap}'
        'a{color:#bef;display:block}img{width:256px}</style><h1>USM / VP9</h1><main>' + cards + '</main>', encoding="utf-8")


    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, required=True, help="game resources directory")
    p.add_argument("--out", type=Path, default=DEST)
    p.add_argument("--ffmpeg", type=Path)
    p.add_argument("--vgmstream", type=Path, help="local vgmstream-cli for supported HCA audio tracks")
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--retry-failed", action="store_true", help="keep previous entries whose outputs still exist")
    if not export_all(p.parse_args()):
        p.error("No CRI USM input found")


if __name__ == "__main__":
    main()
