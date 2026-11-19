import struct
import tempfile
import unittest
from pathlib import Path

from tools.export_cubism import segments
from tools.export_video import split


class FormatTests(unittest.TestCase):
    def test_holds_use_step_instead_of_nonfinite_bezier(self):
        flat, segs, points = segments([(0, 1, 0, float("inf"), 0, 0, 0), (1, 2, 0, 0, 0, 0, 0)])
        self.assertEqual((flat, segs, points), ([0, 1, 2, 1, 2], 1, 2))

    def test_hermite_tangents_map_to_bezier_control_points(self):
        flat, segs, points = segments([(0, 2, 0, 1, 0, 0, 0), (3, 5, 1, 0, 0, 0, 0)])
        self.assertEqual((flat, segs, points), ([0, 2, 1, 1, 3, 2, 4, 3, 5], 1, 4))

    def test_demux_excludes_padding_and_metadata(self):
        def chunk(kind, data, mode, padding):
            head = bytearray(32)
            head[:4] = kind
            struct.pack_into(">I", head, 4, 24+len(data)+padding)
            head[9], head[15] = 24, mode
            struct.pack_into(">H", head, 10, padding)
            return head + data + bytes(padding)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "movie"
            source.write_bytes(chunk(b"CRID", b"metadata", 1, 8) + chunk(b"@SFV", b"video", 0, 5))
            tracks = split(source, root)
            self.assertEqual(list(tracks), [(b"@SFV", 0)])
            self.assertEqual(tracks[b"@SFV", 0].read_bytes(), b"video")
            source.write_bytes(b"CRID")
            with self.assertRaises(ValueError):
                split(source, root)
