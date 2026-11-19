"""Render on-demand first frames without publishing or retaining full model exports."""
import argparse
from pathlib import Path
import subprocess
import tempfile

from PIL import Image
from viewer_runtime import APP_HOME, command
from . import render_spine
from .resource_sources import read

SIZE = 256


def render_first_frame(model: Path, png: Path, game: Path):
    model, png, game = model.resolve(strict=True), png.resolve(), game.resolve(strict=True)
    if model == png:
        raise ValueError("Choose a separate PNG output")
    png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=png.parent) as tmp:
        result = Path(tmp) / "first.png"
        if model.name.casefold().endswith(".model3.json"):
            groups = read(model, {})["FileReferences"].get("Motions", {})
            available = [group for group, motions in groups.items() if motions]
            group = next((g for g in available if g.casefold() == "idle"), next(iter(available), "Idle"))
            args = command("render_native", model, "--game", game, "--png", result, "--group", group,
                           "--poster-first", "--transparent", "--width", SIZE, "--height", SIZE)
            proc = subprocess.run(args, cwd=APP_HOME, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif model.suffix.lower() in {".skel", ".json"}:
            if model.suffix.lower() == ".json":
                data = read(model, {})
                if not isinstance(data.get("bones"), list) or not data.get("skeleton", {}).get("spine"):
                    raise ValueError("JSON is not a Spine skeleton")
            meta = render_spine.query(model)
            animations = [a["name"] for a in meta["animations"]]
            if not animations:
                raise ValueError("Spine model has no selectable animation")
            motion = next((n for n in animations if "idle" in n.lower()), animations[0])
            skins = [s for s in meta["skins"] if s.casefold() != "default"]
            skin = next((s for s in skins if s.lower().startswith("front")), next(iter(skins), None))
            args = [render_spine.ensure_cli(), "export", model, "-a", motion, "--pma", "-f", "Png", "-o", result,
                    "--time", "0", "--warm-up", "0", "--color", "#00000000", "--margin", "2",
                    "--max-resolution", str(2 * SIZE), "--no-progress"]
            if skin:
                args.extend(["--skins", skin])
            proc = render_spine.run_cli(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        else:
            raise ValueError("Not a Cubism model3 or Spine skeleton")
        log = proc.stdout.decode("utf8", "replace")
        if proc.returncode or "[E]" in log or not result.is_file():
            raise RuntimeError(log[-2000:] or "Renderer did not produce a first frame")
        with Image.open(result) as opened:
            bitmap = opened.convert("RGBA")
        box = bitmap.getchannel("A").getbbox()
        if not box:
            raise ValueError("First frame is completely transparent")
        bitmap = bitmap.crop((box[0]-2, box[1]-2, box[2]+2, box[3]+2))
        bitmap.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
        bitmap.save(result, format="PNG")
        result.replace(png)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--game", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--png", type=Path, required=True)
    args = p.parse_args()
    render_first_frame(args.model, args.png, args.game)


if __name__ == "__main__":
    main()
