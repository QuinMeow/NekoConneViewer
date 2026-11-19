"""Decode a CRI audio track/container with an unconfigured local vgmstream CLI."""

from pathlib import Path
import subprocess
import wave


def decode(track: Path, wav: Path, cli: Path):
    result = subprocess.run([str(cli), "-i", "-o", str(wav), str(track)], capture_output=True)
    if result.returncode:
        why = (result.stderr or result.stdout).decode(errors="replace").strip()
        raise ValueError(f"vgmstream exited {result.returncode}: {why[:240]}")
    try:
        with wave.open(str(wav), "rb") as pcm:
            if not pcm.getnframes() or pcm.getsampwidth() != 2 or pcm.getframerate() <= 0:
                raise ValueError("vgmstream returned empty or non-PCM16 audio")
            pcm.setpos(pcm.getnframes() - 1)
            if len(pcm.readframes(1)) != pcm.getnchannels() * pcm.getsampwidth():
                raise ValueError("vgmstream returned truncated PCM")
    except (wave.Error, EOFError) as exc:
        raise ValueError("vgmstream returned an invalid WAV") from exc
