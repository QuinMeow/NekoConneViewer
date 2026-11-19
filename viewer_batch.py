"""Build a cancellable queue of model motions, independent of Tk controls."""
import subprocess

import viewer_core as core


def render_queue(models, cache, options, *, override=None, all_poses=False, cancelled, log):
    for asset in models:
        if cancelled():
            return
        try:
            spine = asset.kind == "spine"
            meta = core.spine_meta(asset) if spine else None
            if override is None:
                entries = [(name, *pair) for name, pair in core.motion_choices(asset, metadata=meta).items()]
                if spine and not all_poses:
                    poses = meta.get("skins") or [""]
                    pose = next((name for name in poses if name.casefold() == "default"), poses[0])
                    entries = [entry for entry in entries if entry[1] == pose]
            else:
                entries = override
            if not entries:
                log(f"[{asset.title}] 没有可渲染的动作\n")
                continue
            newest = max((f.stat().st_mtime_ns for f in asset.path.parent.rglob("*") if f.is_file()), default=0) if spine else 0
            for name, group, index in entries:
                if cancelled():
                    return
                label = f"{asset.title} / {name}" if len(models) > 1 else name
                if spine:
                    outputs = core.render_paths(asset, cache, group, index)
                    if all(p.is_file() and p.stat().st_size and p.stat().st_mtime_ns >= newest for p in outputs):
                        log(f"[{label}] 跳过已有结果\n")
                        continue
                yield label, core.render_command(asset, cache, group, index, metadata=meta,
                                                  **({} if spine else options))
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
            log(f"[{asset.title}] 无法准备渲染：{exc}\n")
