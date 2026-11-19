import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.import_bundles import Image, Importer, run


class ExternalImportTests(unittest.TestCase):
    def test_extensionless_dds_duplicate_names_and_unsupported(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / 'input'
            for group, color in [('a', 'red'), ('b', 'blue')]:
                (src / group).mkdir(parents=True)
                Image.new('RGB', (5, 3), color).save(src / group / 'texture', format='DDS')
            (src / 'plain.dds').write_bytes(b'plain data')
            (src / 'data.unity3d').write_bytes(b'UnityFS\0' + bytes(20))
            originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob('*') if p.is_file()}
            manifest = run(src, root / 'cache', [src / 'data.unity3d'])
            result = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(result['counts'], {'DDS': 2, 'UnityFS': 0, 'unknown': 1, 'skipped': 1})
            self.assertEqual(len({r['file'] for r in result['assets']}), 2)
            for asset in result['assets']:
                with Image.open(manifest.parent / asset['file']) as image:
                    self.assertEqual(image.size, (5, 3))
                    self.assertEqual(image.getpixel((0, 0))[:3], (255, 0, 0) if asset['source'] == 'a/texture' else (0, 0, 255))
            self.assertEqual({p: hashlib.sha256(p.read_bytes()).hexdigest() for p in originals}, originals)
            with self.assertRaisesRegex(ValueError, 'separate'):
                run(src, src / 'cache')

    def test_model_only_when_unique_and_complete(self):
        with tempfile.TemporaryDirectory() as d:
            work = Importer(Path(d))
            ref = dict(source='models', serialized_file='CAB-a', path_id=11, title='hero.skel', type='TextAsset')
            atlas = dict(ref, path_id=12, title='hero.atlas')
            skel = (ref, 'hero', 'skel', b'skeleton test data')
            work.texts = [skel, (atlas, 'hero', 'atlas', b'part.png\nsize: 2,2\n\npart2.png\nsize: 2,2\n')]
            work.image(Image.new('RGBA', (2, 2)), dict(ref, type='Texture2D', path_id=13, title='part'))
            work.models()
            self.assertFalse(any(a['kind'] == 'spine' for a in work.assets))
            self.assertIn('incomplete Spine', work.issues[0]['reason'])
            work.image(Image.new('RGBA', (2, 2)), dict(ref, type='Texture2D', path_id=14, title='part2'))
            work.models()
            model, = [a for a in work.assets if a['kind'] == 'spine']
            self.assertEqual((model['path_id'], model['atlas']['path_id']), (11, 12))
            folder = (Path(d) / model['file']).parent
            self.assertTrue((folder / 'part2.png').is_file())
            self.assertEqual((Path(d) / model['file']).read_bytes(), skel[-1])
            work.texts[1] = (atlas, 'hero', 'atlas', b'../escape.png\nsize: 2,2\n')
            work.models()
            self.assertIn('unsupported atlas page filename', work.issues[-1]['reason'])
