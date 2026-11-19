"""Decode Unity sprites (including cropped atlas regions) and standalone textures."""
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".tools/unity"))
import UnityPy


def safe(s):
    return re.sub(r"[^\w.-]", "_", s).strip(". ")[:90] or "unnamed"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--match", default=".", help="regular expression for asset names")
    args = p.parse_args()
    pattern = re.compile(args.match, re.I)
    e = UnityPy.load(str(args.bundle))
    args.out.mkdir(parents=True, exist_ok=True)
    ok = bad = 0
    with (args.out / "index.csv").open("w", newline="", encoding="utf-8-sig") as index:
        writer = csv.writer(index)
        writer.writerow(["source", "path_id", "type", "name", "file", "error"])
        for o in e.objects:
            kind = o.type.name
            if kind not in {"Texture2D", "Sprite"}:
                continue
            name = o.peek_name() or "unnamed"
            if not pattern.search(name):
                continue
            src = o.assets_file.name
            file = Path(kind) / safe(src) / f"{o.path_id}_{safe(name)}.png"
            path = args.out / file
            try:
                data = o.read()
                if kind == "Texture2D" and (data.m_Width == 0 or data.m_Height == 0):
                    raise ValueError("Empty runtime texture (zero dimensions); no stored bitmap")
                im = data.image
                path.parent.mkdir(parents=True, exist_ok=True)
                im.save(path, compress_level=2)
                writer.writerow([src, o.path_id, kind, name, file.as_posix(), ""])
                ok += 1
            except Exception as err:
                writer.writerow([src, o.path_id, kind, name, "", str(err)])
                bad += 1
            if (ok+bad) % 100 == 0:
                print(f"{ok} PNG, {bad} skipped", flush=True)
    print(f"{ok} PNG, {bad} skipped; see {index.name}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
