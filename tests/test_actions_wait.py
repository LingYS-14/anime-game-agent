"""Tests for perception-based wait conditions (``condition.kind: check``)."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kiseki_runtime import actions  # noqa: E402
from kiseki_runtime.errors import KisekiError, NotSupportedError, PolicyError  # noqa: E402

PROFILE = {
    "id": "demo",
    "checks": {"in_game": {"anchor": "hud_chat"}, "phone_open": {"anchor": "phone_close"}},
    "ui": {"anchors": []},
}
RECT = (0, 0, 100, 100)


def plan(name: str = "in_game", **extra):
    condition = {"kind": "check", "name": name, **extra}
    return actions.plan_actions(PROFILE, [{"type": "wait", "condition": condition}], RECT)


class CheckPlanTests(unittest.TestCase):
    def test_plan_carries_timeout_and_interval(self) -> None:
        planned = plan(timeout=5, interval=0.1)[0]
        self.assertEqual(planned["condition"], "check")
        self.assertEqual(planned["check"], "in_game")
        self.assertEqual(planned["timeout_s"], 5.0)
        self.assertEqual(planned["interval_s"], 0.1)

    def test_defaults(self) -> None:
        planned = plan()[0]
        self.assertEqual(planned["timeout_s"], 10.0)
        self.assertEqual(planned["interval_s"], 0.3)

    def test_unknown_check_is_rejected_at_plan_time(self) -> None:
        with self.assertRaises(PolicyError) as caught:
            plan("not_declared")
        self.assertEqual(caught.exception.details.get("code_hint"), "UNKNOWN_CHECK")

    def test_unsupported_condition_still_errors(self) -> None:
        with self.assertRaises(NotSupportedError):
            actions.plan_actions(PROFILE, [{"type": "wait", "condition": {"kind": "screen"}}], RECT)


class CheckExecuteTests(unittest.TestCase):
    def test_polls_until_passed(self) -> None:
        calls: list[str] = []

        def check(name: str) -> dict:
            calls.append(name)
            return {"passed": len(calls) >= 3, "detail": {"name": name}}

        planned = plan(timeout=2, interval=0.01)
        trace = actions.execute_actions(planned, hwnd=1, focus_check=False, check_fn=check)
        self.assertEqual(len(calls), 3)
        self.assertEqual(trace[0]["attempts"], 3)
        self.assertGreaterEqual(trace[0]["elapsed_s"], 0.0)

    def test_accepts_ok_flag_from_guard_results(self) -> None:
        """Regression: Runtime guards return ``ok``; waits must not require ``passed``."""

        def check(name: str) -> dict:
            return {"name": name, "kind": "ocr", "ok": True}

        planned = plan(timeout=1, interval=0.01)
        trace = actions.execute_actions(planned, hwnd=1, focus_check=False, check_fn=check)
        self.assertEqual(trace[0]["check"], "in_game")
        self.assertEqual(trace[0]["attempts"], 1)

    def test_timeout_raises_and_reports_last_state(self) -> None:
        def check(name: str) -> dict:
            return {"passed": False, "reason": "still hidden"}

        planned = plan(timeout=0.1, interval=0.02)
        with self.assertRaises(KisekiError) as caught:
            actions.execute_actions(planned, hwnd=1, focus_check=False, check_fn=check)
        self.assertEqual(caught.exception.details.get("code_hint"), "CHECK_TIMEOUT")
        self.assertEqual(caught.exception.details.get("check"), "in_game")
        self.assertGreaterEqual(caught.exception.details.get("attempts"), 1)

    def test_missing_check_fn(self) -> None:
        planned = plan(timeout=0.1)
        with self.assertRaises(NotSupportedError):
            actions.execute_actions(planned, hwnd=1, focus_check=False)

    def test_seconds_wait_is_unchanged(self) -> None:
        planned = actions.plan_actions(
            PROFILE, [{"type": "wait", "condition": {"kind": "seconds", "seconds": 0.02}}], RECT
        )
        started = time.perf_counter()
        trace = actions.execute_actions(planned, hwnd=1, focus_check=False)
        self.assertGreaterEqual(time.perf_counter() - started, 0.015)
        self.assertEqual(trace[0]["condition"], "seconds")


if __name__ == "__main__":
    unittest.main()
