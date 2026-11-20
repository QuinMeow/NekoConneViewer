"""Lightweight metadata shared by the Spine importer and exporter."""
from pathlib import PureWindowsPath
import re


def atlas_pages(data):
    pages = []
    for block in re.split(r'\n[ \t]*\n+', data.decode('utf-8-sig').replace('\r', '').strip()):
        name = block.split('\n', 1)[0].strip()
        if not re.fullmatch(r'[\w .()-]+\.png', name, re.I) or PureWindowsPath(name).is_reserved():
            raise ValueError(f'unsupported atlas page filename: {name!r}')
        if name.casefold() in {p[0].casefold() for p in pages}:
            raise ValueError('duplicate page name')
        size = None
        for line in block.splitlines()[1:]:
            if ':' not in line:
                break
            match = re.fullmatch(r'\s*size:\s*(\d+)\s*,\s*(\d+)\s*', line, re.I)
            if match:
                size = tuple(map(int, match.groups()))
        pages.append((name, size))
    return pages
