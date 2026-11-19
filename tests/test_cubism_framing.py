import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from tools.render_native import fit_view, make_background, motion_bounds


class CubismFramingTests(unittest.TestCase):
    def test_fits_both_orientations_without_stretching(self):
        # A 1x2 model is height-limited in landscape, width-limited in portrait.
        self.assertEqual(fit_view((3, 2, 4, 4), (200, 100), .9), (3.5, 3, 45))
        self.assertEqual(fit_view((3, 2, 4, 4), (100, 240), .9), (3.5, 3, 90))

    def test_alpha_union_fits_visible_poses_in_world_space(self):
        class Probe:
            size = (100, 100)

            def geometry_bounds(self):
                return (50, -150, 150, -50)

            def set_view(self, view):
                self.view = view

            def frame(self, alpha_only):
                img = Image.new("L", self.size)
                img.paste(255, [(20, 40, 30, 65), (0, 0, 100, 100), (30, 25, 55, 45)][self.pose])
                return img

        probe = Probe()
        def pose(i):
            probe.pose = i
            return 0 if i == 1 else 1
        # The middle pose is fully transparent via Model/Opacity, despite its fullscreen geometry.
        bounds = motion_bounds(probe, pose, 3)
        self.assertEqual(bounds, (100-31/.98, -100-16/.98, 100+6/.98, -100+26/.98))

    def test_no_visible_poses_are_not_written_as_blank_wallpapers(self):
        class NoMeshes:
            def geometry_bounds(self):
                return None
        with self.assertRaisesRegex(ValueError, "no visible"):
            motion_bounds(NoMeshes(), lambda _: 1, 3)

    def test_background_covers_by_cropping_and_always_flattens(self):
        with TemporaryDirectory() as tmp:
            image = Image.new("RGBA", (12, 4), (255, 0, 0, 255))
            image.paste((0, 0, 255, 255), (3, 0, 9, 4))
            image.paste((0, 0, 0, 0), (5, 0, 7, 4))
            image.save(Path(tmp) / "wide.png")
            canvas = make_background(Path(tmp) / "wide.png", (4, 4))
            self.assertEqual(canvas.mode, "RGB")
            self.assertEqual(canvas.getpixel((0, 2)), (0, 0, 255))
            self.assertEqual(canvas.getpixel((3, 2)), (0, 0, 255))
            self.assertEqual(canvas.getpixel((1, 2)), make_background(None, (1, 1)).getpixel((0, 0)))
