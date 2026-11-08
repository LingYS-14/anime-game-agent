"""Offline tests for the PaddleOCR integration (M1.1).

The heavy PaddleOCR import is only exercised when the optional extra is
installed; the geometry/validation helpers are pure Python and always run.
"""

from __future__ import annotations

import unittest

from kiseki_runtime import ocr
from kiseki_runtime.errors import KisekiError


class ConfidenceTests(unittest.TestCase):
    def test_default_confidence(self) -> None:
        self.assertEqual(ocr.normalize_confidence(None), 0.5)

    def test_accepts_bounds(self) -> None:
        self.assertEqual(ocr.normalize_confidence(0.0), 0.0)
        self.assertEqual(ocr.normalize_confidence(1.0), 1.0)

    def test_rejects_out_of_range(self) -> None:
        with self.assertRaises(KisekiError) as caught:
            ocr.normalize_confidence(1.5)
        self.assertEqual(caught.exception.details["code_hint"], "INVALID_CONFIDENCE")

    def test_rejects_non_number(self) -> None:
        with self.assertRaises(KisekiError) as caught:
            ocr.normalize_confidence("high")
        self.assertEqual(caught.exception.details["code_hint"], "INVALID_CONFIDENCE")


class LineMappingTests(unittest.TestCase):
    def test_maps_region_pixels_to_client_and_normalized(self) -> None:
        raw = [{"text": "确定", "score": 0.9876, "rect": [10, 20, 100, 40]}]
        lines = ocr.normalize_lines(raw, (200, 300), 1000, 2000)
        self.assertEqual(
            lines,
            [
                {
                    "text": "确定",
                    "score": 0.9876,
                    "rect": [210, 320, 100, 40],
                    "normalized_rect": [0.21, 0.16, 0.31, 0.18],
                    "normalized_center": [0.26, 0.17],
                }
            ],
        )

    def test_polygon_rect_uses_bounding_box(self) -> None:
        rect = ocr._polygon_rect([[10, 20], [30, 20], [30, 50], [10, 50]])
        self.assertEqual(rect, [10, 20, 20, 30])


class AvailabilityTests(unittest.TestCase):
    def test_require_raises_typed_error_when_missing(self) -> None:
        if ocr.available():
            self.skipTest("PaddleOCR is installed in this interpreter")
        with self.assertRaises(KisekiError) as caught:
            ocr.require()
        self.assertEqual(caught.exception.details["code_hint"], "OCR_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
