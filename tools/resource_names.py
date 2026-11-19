"""Resolve supplied resource names against local package headers."""
from collections import defaultdict
from hashlib import sha512
import json
from pathlib import Path
import re


NAME = re.compile(r"(?:[\w.-]+/)+[\w.-]+\.(?:ab|dds|png|jpg|jpeg)", re.IGNORECASE)
SIGNATURES = {b"UnityFS\0": "unity", b"DDS |\0\0\0": "dds", b"\x89PNG\r\n\x1a\n": "png"}


def names_from_file(path):
    def strings(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            yield from obj
            for value in obj.values():
                yield from strings(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from strings(value)
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = strings(json.loads(text)) if Path(path).suffix.casefold() == ".json" else text.splitlines()
    return {name for value in lines if NAME.fullmatch(name := value.strip()) and ".." not in name.split("/")}


def mask(name):
    seed = sha512(name.encode("utf8")).digest()
    return seed + sha512(seed).digest()


def candidates(names):
    table = defaultdict(list)
    for name in sorted(names):
        cipher = mask(name)
        for header, kind in SIGNATURES.items():
            table[bytes(a ^ b for a, b in zip(cipher, header))].append((name, kind))
    return table


def decode(data, name):
    key = mask(name)
    size = 128 * 512
    repeated = key * (size // len(key))
    full_mask = int.from_bytes(repeated, "little")
    parts = []
    for p in range(0, len(data), size):
        part = data[p:p + size]
        bits = full_mask if len(part) == size else int.from_bytes(repeated[:len(part)], "little")
        parts.append((int.from_bytes(part, "little") ^ bits).to_bytes(len(part), "little"))
    return b"".join(parts)
