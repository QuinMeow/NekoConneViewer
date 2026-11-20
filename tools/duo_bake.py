"""Bake a moving Cubism actor and synchronized, pointer-driven eyes for offline HTML."""
import base64
import json
import math
from itertools import product
from pathlib import Path

FPS = 24
GRID = 5
HEIGHT = 1440


def _gaze_value(center, limits, offset):
    lo, hi = limits
    return center + offset * ((center - lo) if offset < 0 else (hi - center))


def _patch_view(view, size, box):
    # Reuse the full frame's pixel scale and pixel centers on the smaller FBO.
    cx, cy, px = view
    l, t, r, b = box
    return cx + (l + r - size[0]) / (2*px), cy - (t + b - size[1]) / (2*px), px


def bake(model_path, core_dll, asset_dir, asset_prefix):
    """Export an alpha WebM, pose-indexed gaze atlases and per-pose alpha masks."""
    from PIL import Image, ImageChops
    from tools.cubism_gpu import Model, Renderer
    from tools.render_native import apply_model_opacity, fit_view, motion_bounds, motion_tracks, webm_input

    model_path = Path(model_path).resolve()
    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    refs = json.loads(model_path.read_text(encoding="utf-8-sig"))["FileReferences"]

    def ref(name):
        path = (model_path.parent / name).resolve(strict=True)
        if not path.is_relative_to(model_path.parent):
            raise ValueError(f"External model resource: {name}")
        return path

    motion = json.loads(ref(refs["Motions"]["Idle"][0]["File"]).read_text(encoding="utf-8-sig"))
    duration = float(motion["Meta"]["Duration"])
    if not 0 < duration <= 30:
        raise ValueError(f"Unexpected idle duration: {duration}")
    n = max(1, round(duration * FPS))
    model = Model(core_dll, ref(refs["Moc"]))
    tracks, opacity = motion_tracks(motion["Curves"], model.parameters)
    eyes = [model.parameters[k] for k in ("ParamEyeBallX", "ParamEyeBallY")]
    lids = [model.parameters[k] for k in ("ParamEyeLOpen", "ParamEyeROpen")]
    defaults = {idx: model.values[idx] for idx in eyes + lids}
    samples = []
    for i in range(n):
        time = i / FPS
        pose = {**defaults, **{idx: curve.sample(time) for idx, curve in tracks}}
        # These two loops have no lid curves. Animate the actual native lids,
        # with different blink offsets so the actors do not blink in unison.
        phase = time - duration * (.62 if model_path.name.startswith("10102") else .36)
        if -.09 < phase < .16:
            openness = min(1, -phase / .09 if phase < 0 else phase / .16)
            for idx in lids:
                pose[idx] = _gaze_value(pose[idx], model.ranges[idx], openness - 1)
        samples.append((pose, opacity.sample(time) if opacity else 1.))

    def apply_pose(i):
        pose, alpha = samples[i]
        model.apply(pose.items())
        return alpha

    def apply_gaze(i, dx, dy):
        pose, _ = samples[i]
        model.apply((idx, _gaze_value(pose[idx], model.ranges[idx], value))
                    for idx, value in zip(eyes, (dx, dy)))

    renderer = Renderer(model, [ref(s) for s in refs["Textures"]], (384, 384))
    try:
        bounds = motion_bounds(renderer, apply_pose, n)
        width = math.ceil(HEIGHT * (bounds[2]-bounds[0]) / (bounds[3]-bounds[1]) / 2) * 2
        size = (width, HEIGHT)
        view = fit_view(bounds, size, .986)
        renderer.resize(size)
        renderer.set_view(view)
        boxes, masks = [], bytearray()
        with webm_input(asset_dir / "idle.webm", size, FPS, True) as pipe:
            for i in range(n):
                alpha = apply_pose(i)
                image = renderer.frame()
                apply_model_opacity(image, alpha)
                if i == 0:
                    image.save(asset_dir / "portrait.webp", lossless=True)
                pipe.write(image.tobytes())
                mask = image.getchannel("A")
                mask.thumbnail((320, 320), Image.Resampling.BOX)
                bits = mask.point(lambda a: 255 if a >= 28 else 0).convert("1", dither=Image.Dither.NONE)
                masks.extend(bits.tobytes())
                for sign in (-1, 1):
                    apply_gaze(i, sign, sign)
                    moved = renderer.frame()
                    apply_model_opacity(moved, alpha)
                    box = ImageChops.difference(image, moved).getbbox(alpha_only=False)
                    if box:
                        boxes.append(box)
        if not boxes:
            raise ValueError(f"No visible eye movement: {model_path.name}")
        l, t = max(0, min(a[0] for a in boxes)-10), max(0, min(a[1] for a in boxes)-10)
        r, b = min(width, max(a[2] for a in boxes)+10), min(HEIGHT, max(a[3] for a in boxes)+10)
        w, h = r-l, b-t
        if w > width*.65 or h > HEIGHT*.25:
            raise ValueError(f"Eye parameters affect more than the expected face region: {w}x{h}")
        renderer.resize((w, h))
        renderer.set_view(_patch_view(view, size, (l, t, r, b)))
        # Keep each decoded atlas well below common texture-size limits.
        per_page = max(1, min(10, 3072 // (h * GRID)))
        atlases = []
        for start in range(0, n, per_page):
            rows = min(per_page, n - start)
            atlas = Image.new("RGBA", (GRID*w, rows*GRID*h))
            for j in range(rows):
                i = start + j
                alpha = apply_pose(i)
                for y, x in product(range(GRID), repeat=2):
                    apply_gaze(i, x * 2/(GRID-1) - 1, 1 - y * 2/(GRID-1))
                    tile = renderer.frame()
                    apply_model_opacity(tile, alpha)
                    atlas.paste(tile, (x*w, (j*GRID+y)*h))
            name = f"eyes-{len(atlases):02}.webp"
            atlas.save(asset_dir / name, lossless=True, method=2)
            atlases.append(f"{asset_prefix}/{name}")
    finally:
        renderer.ctx.release()
    return {
        "width": width, "height": HEIGHT, "video": f"{asset_prefix}/idle.webm",
        "portrait": f"{asset_prefix}/portrait.webp", "fps": FPS, "frames": n,
        "grid": GRID, "patch": [l, t, w, h], "atlases": atlases, "framesPerAtlas": per_page,
        "mask": {"width": bits.width, "height": bits.height, "stride": 1,
                 "bits": base64.b64encode(masks).decode("ascii")},
    }
