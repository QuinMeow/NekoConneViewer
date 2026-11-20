import unittest

from tools.duo_bake import _gaze_value, _patch_view


class DuoPoseTests(unittest.TestCase):
    def test_center_retains_the_current_animation_and_extremes_reach_the_model_limits(self):
        self.assertEqual([_gaze_value(.5, (-2, 3), d) for d in (-1, -.5, 0, .5, 1)],
                         [-2, -.75, .5, 1.75, 3])

    def test_local_eye_render_and_full_body_render_share_pixel_centers(self):
        view, size, box = (2.5, -1.75, 123.4), (802, 1440), (501, 170, 654, 259)
        cx, cy, px = _patch_view(view, size, box)
        w, h = box[2]-box[0], box[3]-box[1]
        # Check both ends and an interior pixel on an odd-sized patch.
        for x, y in ((.5, .5), (w-.5, h-.5), (72.5, 36.5)):
            self.assertAlmostEqual(cx + (x-w/2)/px, view[0] + (box[0]+x-size[0]/2)/px)
            self.assertAlmostEqual(cy - (y-h/2)/px, view[1] - (box[1]+y-size[1]/2)/px)


if __name__ == "__main__":
    unittest.main()
