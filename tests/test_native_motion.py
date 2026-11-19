import unittest

from tools.render_native import Curve


class NativeMotionTests(unittest.TestCase):
    def test_constant_linear_and_clamped_endpoints(self):
        self.assertEqual(Curve([0, 4]).sample(5), 4)
        curve = Curve([0, 1, 0, 2, 5])
        self.assertEqual([curve.sample(t) for t in (-1, 1, 7)], [1, 3, 5])

    def test_step_and_inverse_step(self):
        curve = Curve([0, 2, 2, 1, 4, 3, 2, 6])
        self.assertEqual([curve.sample(t) for t in (.5, 1, 1.5)], [2, 4, 6])

    def test_weighted_bezier_solves_for_time_not_normalized_fraction(self):
        curve = Curve([0, 0, 1, 0, 0, 0, 1, 1, 1])
        self.assertAlmostEqual(curve.sample(.125), .5, places=4)

    def test_unsupported_segment_does_not_silently_drop_keyframes(self):
        with self.assertRaises(ValueError):
            Curve([0, 0, 99, 1, 1])
