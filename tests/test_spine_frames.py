import tempfile
import unittest
from pathlib import Path

from tools.render_spine import joint_crop, ordered_frames, pillow, write_sequence


class SpineFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = pillow()

    def frame(self, index, point=None, color=(91, 172, 209, 128), size=(32, 32)):
        result = self.root / f"frame_12_{index}.png"
        frame = self.image.new("RGBA", size)
        if point is not None:
            frame.putpixel(point, color)
        frame.save(result)
        return result

    def test_whole_motion_bounds_preserve_faint_pixels_and_first_frame_geometry(self):
        first = self.frame(0, (8, 11))
        blank = self.frame(1)
        last = self.frame(2, (0, 13), (117, 113, 203, 1))
        target = self.root / "poster.png"
        box = write_sequence([first, blank, last], 12, target, None, {})
        self.assertEqual(box, (-2, 9, 12, 17))
        with self.image.open(target) as output:
            self.assertEqual(output.size, (14, 8))
            self.assertEqual(output.getpixel((10, 2)), (91, 172, 209, 128))
            self.assertEqual(output.getpixel((0, 0)), (0, 0, 0, 0))

    def test_numeric_order_and_missing_number(self):
        expected = [self.frame(i) for i in range(11)]
        self.assertEqual(ordered_frames(self.root, 12), expected)
        expected[2].unlink()
        with self.assertRaisesRegex(ValueError, "continuous"):
            ordered_frames(self.root, 12)

    def test_all_transparent_sequence_or_changed_canvas_fails(self):
        empty = self.frame(0)
        with self.assertRaisesRegex(ValueError, "transparent"):
            joint_crop([empty])
        with self.assertRaisesRegex(ValueError, "coordinate system"):
            joint_crop([empty, self.frame(1, size=(31, 32))])


if __name__ == "__main__":
    unittest.main()
