"""Import locally selected standard UnityFS and DDS resources; never change the input."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/unity'))
from PIL import Image
import UnityPy
from fsspec.implementations.memory import MemoryFileSystem
from tools.spine_assets import atlas_pages


def slug(value):
    return re.sub(r'[^\w-]+', '_', value)[:60].strip('_') or 'asset'


def key(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()[:16]


def linked(path):
    attrs = path.lstat()
    return stat.S_ISLNK(attrs.st_mode) or bool(getattr(attrs, 'st_file_attributes', 0) & 1024)


def files(source, issue):
    if source.is_file():
        yield source, source.name
        return
    for base, dirs, names in os.walk(source, onerror=lambda e: issue(str(e.filename), str(e))):
        regular = set(names)
        for name in sorted(dirs + names):
            path = Path(base) / name
            rel = path.relative_to(source).as_posix()
            try:
                if linked(path):
                    issue(rel, 'skipped symbolic link or reparse point')
                    if name in dirs:
                        dirs.remove(name)
                elif name in regular and path.is_file():
                    yield path, rel
            except OSError as exc:
                issue(rel, str(exc))
        dirs.sort()


class Importer:
    def __init__(self, folder):
        self.folder = folder
        self.assets, self.issues, self.texts = [], [], []

    def issue(self, source, reason, **fields):
        self.issues.append(dict(source=source, reason=reason, **fields))

    def image(self, bitmap, ref):
        file = f"images/{slug(ref['title'])}_{key(ref['source'], ref['serialized_file'], ref['path_id'])}.png"
        dest = self.folder / file
        dest.parent.mkdir(parents=True, exist_ok=True)
        bitmap.save(dest, format='PNG', compress_level=3)
        self.assets.append(dict(ref, kind='image', file=file, width=bitmap.width, height=bitmap.height))

    def bundle(self, path, rel):
        # Resolve Unity pointers inside the selected bundle only, not against the host filesystem.
        env = UnityPy.Environment(fs=MemoryFileSystem(), path='')
        with path.open('rb') as file:
            env.load_file(file, name=rel)
            self.read_env(env, rel)

    def read_env(self, env, rel, *, images=True, spine=True, accept_image=None, accept_spine=None):
        first_asset = len(self.assets)
        objects = list(env.objects)
        aliases = {}
        for name, pp in env.container.items():
            try:
                target = pp.deref()
            except (KeyError, ValueError, OSError):
                continue
            if target is not None:
                aliases.setdefault((target.assets_file.name, target.path_id), []).append(name)
        if images or spine:
            atlas_ids = {}
            textures = []
            for obj in objects:
                typ, ser = obj.type.name, obj.assets_file.name
                if typ not in {'Texture2D', 'Sprite', 'SpriteAtlas', 'TextAsset'}:
                    continue
                ref = dict(source=rel, serialized_file=ser, path_id=obj.path_id, type=typ, title='')
                try:
                    name = ref['title'] = obj.peek_name() or str(obj.path_id)
                    if paths := aliases.get((ser, obj.path_id)):
                        ref['container'] = sorted(paths)
                    if typ == 'SpriteAtlas' and images:
                        for _, target in obj.read().m_RenderDataMap:
                            tex = target.texture.deref()
                            if tex is not None:
                                refs = atlas_ids.setdefault((tex.assets_file.name, tex.path_id), [])
                                if ref not in refs:
                                    refs.append(ref)
                    elif typ == 'Texture2D' and (images or spine):
                        textures.append((obj, ref))
                    elif typ == 'Sprite' and images:
                        textures.append((obj, ref))
                    elif typ == 'TextAsset' and spine and re.search(r'\.(atlas|skel|json)(?:\.(txt|bytes))?$', name, re.I):
                        text = obj.read().m_Script
                        data = text.encode('utf-8', 'surrogateescape') if isinstance(text, str) else bytes(text)
                        stem, ext = re.sub(r'\.(txt|bytes)$', '', name, flags=re.I).rsplit('.', 1)
                        if ext.lower() == 'json':
                            try:
                                skeleton = json.loads(data)
                            except (ValueError, UnicodeError):
                                continue
                            if not isinstance(skeleton, dict) or not isinstance(skeleton.get('bones'), list) or not skeleton.get('skeleton', {}).get('spine'):
                                continue
                        elif ext.lower() == 'skel' and not re.search(rb'\d+\.\d+\.\d+', data[:96]):
                            raise ValueError('no recognized Spine skeleton version')
                        self.texts.append((ref, stem.casefold(), ext.lower(), data))
                except Exception as exc:
                    self.issue(rel, str(exc), serialized_file=ser, path_id=obj.path_id, type=typ)
            pages = set()
            if spine and (not images or accept_image is not None):
                for ref, _, ext, data in self.texts:
                    if ext == 'atlas':
                        try:
                            pages.update(Path(name).stem.casefold() for name, _ in atlas_pages(data))
                        except ValueError as exc:
                            self.issue(ref['source'], str(exc), type='TextAsset')
            for obj, ref in textures:
                publish = images and (accept_image is None or accept_image(ref))
                needed = obj.type.name == 'Texture2D' and ref['title'].casefold() in pages
                if publish or needed:
                    try:
                        self.image(obj.read().image, {**ref, **({} if publish else {'private_texture': True})})
                    except Exception as exc:
                        self.issue(rel, str(exc), serialized_file=ref['serialized_file'], path_id=ref['path_id'], type=ref['type'])
            for asset in self.assets[first_asset:]:
                if asset['source'] == rel and (asset['serialized_file'], asset['path_id']) in atlas_ids:
                    asset['kind'] = 'atlas'
                    asset['sprite_atlases'] = atlas_ids[(asset['serialized_file'], asset['path_id'])]

    @staticmethod
    def match(ref, choices):
        # Prefer a unique source-local match. Never arbitrarily choose among duplicate names.
        for subset in ([v for v in choices if (v['source'], v['serialized_file']) == (ref['source'], ref['serialized_file'])],
                       [v for v in choices if v['source'] == ref['source']],
                       [v for v in choices if Path(v['source']).parent == Path(ref['source']).parent], choices):
            if subset:
                if len(subset) == 1:
                    return subset[0]
                break
        raise ValueError(f'expected one matching input but found {len(subset)}')

    def models(self, accept=None):
        atlases = [(r, stem, data) for r, stem, ext, data in self.texts if ext == 'atlas']
        for skel, stem, ext, payload in self.texts:
            if ext == 'atlas':
                continue
            if accept is not None and not accept(skel['title']):
                continue
            try:
                atlas = self.match(skel, [r for r, s, _ in atlases if s == stem])
                atlas_data = next(d for r, _, d in atlases if r is atlas)
                pages = []
                for name, size in atlas_pages(atlas_data):
                    tex = self.match(atlas, [a for a in self.assets if a['type'] in {'DDS', 'Texture2D'}
                                             and a['title'].casefold() in {name.casefold(), Path(name).stem.casefold()}])
                    if size and all(size) and size[0] * tex['height'] != size[1] * tex['width']:
                        raise ValueError(f'{name}: image dimensions disagree with atlas aspect ratio')
                    pages.append((name, tex))
                model = f"{slug(stem)}_{key(skel['source'], skel['serialized_file'], skel['path_id'])}"
                folder = self.folder / 'spine' / model
                folder.mkdir(parents=True, exist_ok=True)
                (folder / (model + '.atlas')).write_bytes(atlas_data)
                for name, tex in pages:
                    shutil.copyfile(self.folder / tex['file'], folder / name)
                    tex['kind'] = 'atlas'
                skeleton = folder / (model + '.' + ext)
                skeleton.write_bytes(payload)
                self.assets.append(dict(**skel, kind='spine', file=skeleton.relative_to(self.folder).as_posix(),
                                        atlas=atlas, pages=[{'page': name, 'image': tex['file']} for name, tex in pages]))
            except (OSError, ValueError) as exc:
                self.issue(skel['source'], 'incomplete Spine set: ' + str(exc), status='missing_dependency',
                           serialized_file=skel['serialized_file'], path_id=skel['path_id'])
        for atlas, stem, _ in atlases:
            if not any(s == stem and ext != 'atlas' for _, s, ext, _ in self.texts):
                self.issue(atlas['source'], 'atlas without skeleton', status='missing_dependency',
                           serialized_file=atlas['serialized_file'], path_id=atlas['path_id'])
