"""Offline tests for M1.0 template matching (skipped when OpenCV is absent)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiseki_runtime import vision
from kiseki_runtime.errors import KisekiError

cv2 = vision.cv2
np = vision.np

PATCH = (120, 60)


def _make_images(folder: Path) -> tuple[Path, Path, Path]:
    rng = np.random.default_rng(7)
    base = rng.integers(0, 256, size=(240, 320), dtype=np.uint8)
    # A distinctive block: horizontal gradient with vertical stripes.
    patch = np.zeros((24, 32), dtype=np.uint8)
    for y in range(24):
        for x in range(32):
            patch[y, x] = 255 if (x // 4 + y // 4) % 2 == 0 else (x * 7 + y * 3) % 256
    x, y = PATCH
    base[y : y + 24, x : x + 32] = patch
    different = rng.integers(0, 256, size=(24, 32), dtype=np.uint8)
    base_path = folder / "frame.png"
    patch_path = folder / "patch.png"
    other_path = folder / "other.png"
    cv2.imwrite(str(base_path), base)
    cv2.imwrite(str(patch_path), patch)
    cv2.imwrite(str(other_path), different)
    return base_path, patch_path, other_path


@unittest.skipUnless(vision.available(), "opencv-python-headless is not installed")
class VisionMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.base, self.patch, self.other = _make_images(self.folder)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_finds_the_patch_at_its_original_position(self) -> None:
        result = vision.match(self.base, self.patch, threshold=0.9)
        self.assertTrue(result["found"])
        self.assertGreater(result["score"], 0.95)
        self.assertEqual(result["rect"], [PATCH[0], PATCH[1], 32, 24])

    def test_finds_the_patch_on_a_raw_rgb_buffer(self) -> None:
        frame = cv2.imread(str(self.base), cv2.IMREAD_GRAYSCALE)
        rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB).tobytes()
        result = vision.match_rgb(rgb, 320, 240, self.patch, threshold=0.9)
        self.assertTrue(result["found"])
        self.assertEqual(result["rect"], [PATCH[0], PATCH[1], 32, 24])

    def test_reports_a_miss_when_the_template_is_absent(self) -> None:
        result = vision.match(self.base, self.other, threshold=0.99)
        self.assertFalse(result["found"])
        self.assertIsNone(result["rect"])
        self.assertLess(result["score"], 0.99)

    def test_region_offsets_are_applied_to_the_rect(self) -> None:
        # Region covers x 0.3..0.7, y 0.2..0.6 of the 320x240 frame:
        # pixels x 96..224, y 48..144 — the patch at (120, 60) is inside.
        result = vision.match(
            self.base, self.patch, threshold=0.9, region=[0.3, 0.2, 0.7, 0.6]
        )
        self.assertTrue(result["found"])
        self.assertEqual(result["rect"], [PATCH[0], PATCH[1], 32, 24])

    def test_region_outside_the_patch_does_not_find_it(self) -> None:
        result = vision.match(
            self.base, self.patch, threshold=0.99, region=[0.0, 0.0, 0.2, 0.2]
        )
        self.assertFalse(result["found"])

    def test_template_larger_than_the_frame_is_rejected(self) -> None:
        big = self.folder / "big.png"
        cv2.imwrite(str(big), np.zeros((400, 400), dtype=np.uint8))
        with self.assertRaises(KisekiError) as caught:
            vision.match(self.base, big)
        self.assertEqual(caught.exception.details.get("code_hint"), "TEMPLATE_TOO_LARGE")

    def test_normalized_helpers(self) -> None:
        self.assertEqual(
            vision.normalized_rect([120, 60, 32, 24], 320, 240),
            [0.375, 0.25, 0.475, 0.35],
        )
        self.assertEqual(
            vision.normalized_center([120, 60, 32, 24], 320, 240),
            [0.425, 0.3],
        )

    def test_threshold_validation(self) -> None:
        self.assertEqual(vision.normalize_threshold(None), 0.9)
        self.assertEqual(vision.normalize_threshold(0.8), 0.8)
        for bad in (0, -0.1, 1.5, "high"):
            with self.assertRaises(KisekiError):
                vision.normalize_threshold(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
