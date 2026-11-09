"""Tests for OCR-driven click targets (``click_text`` actions)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiseki_runtime.errors import KisekiError, PolicyError  # noqa: E402
from kiseki_runtime.tools import Runtime, _text_match  # noqa: E402

LINES = [
    {"text": "好友", "score": 0.97, "rect": [10, 10, 20, 20], "normalized_center": [0.1, 0.1]},
    {"text": "短信记录", "score": 0.62, "rect": [30, 30, 40, 20], "normalized_center": [0.2, 0.2]},
    {"text": "短信", "score": 0.55, "rect": [50, 50, 44, 22], "normalized_center": [0.35, 0.42]},
]


class TextMatchTests(unittest.TestCase):
    def test_exact_match_beats_higher_scored_partial(self) -> None:
        best = _text_match(LINES, "短信")
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best["text"], "短信")

    def test_partial_match_falls_back_to_highest_score(self) -> None:
        best = _text_match(LINES, "短信记")
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best["text"], "短信记录")

    def test_no_match_returns_none(self) -> None:
        self.assertIsNone(_text_match(LINES, "背包"))
        self.assertIsNone(_text_match(LINES, "   "))

    def test_lines_without_text_are_skipped(self) -> None:
        best = _text_match([{"score": 0.9}, {"text": None, "score": 0.8}], "短信")
        self.assertIsNone(best)


class RuntimeClickTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = Runtime(root=str(ROOT))
        self.window = {"hwnd": 4242, "client": [0, 0, 2560, 1600], "title": "demo"}

    def _patch(self, lines: list[dict]) -> None:
        self.patcher_window = mock.patch.object(self.runtime, "_focused_window", return_value=self.window)
        self.patcher_ocr = mock.patch.object(
            self.runtime,
            "game_ocr",
            return_value={"ok": True, "lines": lines, "preset": "accurate"},
        )
        self.patcher_window.start()
        self.patcher_ocr.start()
        self.addCleanup(self.patcher_window.stop)
        self.addCleanup(self.patcher_ocr.stop)

    def test_click_text_resolves_to_normalized_click(self) -> None:
        self._patch(LINES)
        result = self.runtime.game_act(
            "hsr",
            [{"type": "click_text", "text": "短信", "button": "left"}],
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["text_matches"][0]["matched_text"], "短信")
        planned = result["planned"][0]
        self.assertEqual(planned["kind"], "click")
        self.assertEqual(planned["target"], "normalized")
        self.assertEqual(planned["normalized"], [0.35, 0.42])

    def test_unknown_text_raises_with_visible_lines(self) -> None:
        self._patch([{"text": "好友", "score": 0.9, "rect": [0, 0, 10, 10], "normalized_center": [0.1, 0.1]}])
        with self.assertRaises(KisekiError) as caught:
            self.runtime.game_act("hsr", [{"type": "click_text", "text": "背包"}], dry_run=True)
        self.assertEqual(caught.exception.details.get("code_hint"), "TEXT_NOT_FOUND")
        self.assertEqual(caught.exception.details.get("lines"), ["好友"])

    def test_empty_text_is_rejected(self) -> None:
        self._patch(LINES)
        with self.assertRaises(PolicyError) as caught:
            self.runtime.game_act("hsr", [{"type": "click_text", "text": "  "}], dry_run=True)
        self.assertEqual(caught.exception.details.get("code_hint"), "INVALID_TEXT_TARGET")

    def test_plain_actions_pass_through_untouched(self) -> None:
        self._patch(LINES)
        result = self.runtime.game_act(
            "hsr",
            [{"type": "noop", "meta": {"note": "nothing"}}],
            dry_run=True,
        )
        self.assertEqual(result["text_matches"], [])
        self.assertEqual(result["planned"][0]["kind"], "noop")


if __name__ == "__main__":
    unittest.main()
