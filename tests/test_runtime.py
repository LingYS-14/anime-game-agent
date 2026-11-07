"""Offline tests for kiseki_runtime (no game window, no input injection)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kiseki_runtime import actions, profiles, yamlmini  # noqa: E402
from kiseki_runtime.errors import KisekiError, PolicyError  # noqa: E402
from kiseki_runtime.mcp_server import Server, TOOL_SCHEMAS  # noqa: E402
from kiseki_runtime.tools import Runtime  # noqa: E402

PROFILE_TEXT = """
version: 1
id: demo
name: Demo Game
window:
  process: Demo.exe
  title_regex: Demo
input:
  focus_check: true
ui:
  anchors:
    - id: play
      region: [0.40, 0.80, 0.60, 0.90]
      threshold: 0.9
    - id: mail
      region: [0.90, 0.05, 0.98, 0.15]
safety:
  emergency_stop_key: F12
  dry_run: true
  forbid_keys: [alt+f4]
skills:
  - id: tap_mail
    actions:
      - type: click
        target: {anchor: mail}
"""


class YamlMiniTests(unittest.TestCase):
    def test_parses_maps_lists_scalars_and_comments(self) -> None:
        text = """
# top comment
name: demo
count: 3
ratio: 0.5
enabled: true
nothing: null
items: [a, "b c", 2]
nested:
  key: value
  list:
    - one
    - two
"""
        data = yamlmini.loads(text)
        self.assertEqual(data["name"], "demo")
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["ratio"], 0.5)
        self.assertTrue(data["enabled"])
        self.assertIsNone(data["nothing"])
        self.assertEqual(data["items"], ["a", "b c", 2])
        self.assertEqual(data["nested"]["list"], ["one", "two"])

    def test_parses_block_scalars(self) -> None:
        data = yamlmini.loads("notes: >-\n  line one\n  line two\nliteral: |\n  a\n  b\n")
        self.assertEqual(data["notes"], "line one line two")
        self.assertEqual(data["literal"], "a\nb\n")

    def test_rejects_broken_indentation(self) -> None:
        with self.assertRaises(KisekiError):
            yamlmini.loads("a: 1\n   b: 2\n")


class ProfileTests(unittest.TestCase):
    def test_loads_profile_and_ignores_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configs = Path(tmp) / "configs"
            configs.mkdir()
            (configs / "demo.yaml").write_text(PROFILE_TEXT, encoding="utf-8")
            (configs / "game_profile.example.yaml").write_text("id: example\nname: x\n", encoding="utf-8")
            loaded, errors = profiles.load_profiles(configs)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(loaded), ["demo"])
        summary = profiles.summary(loaded["demo"])
        self.assertEqual(summary["anchors"], ["mail", "play"])
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["emergency_stop_key"], "F12")

    def test_broken_profile_is_reported_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configs = Path(tmp) / "configs"
            configs.mkdir()
            (configs / "ok.yaml").write_text(PROFILE_TEXT, encoding="utf-8")
            (configs / "bad.yaml").write_text("name: no id here\n", encoding="utf-8")
            loaded, errors = profiles.load_profiles(configs)
        self.assertIn("demo", loaded)  # profile id, not file stem
        self.assertEqual(len(errors), 1)
        self.assertIn("bad.yaml", errors[0]["path"])


class ActionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = yamlmini.loads(PROFILE_TEXT)
        self.rect = (100, 50, 1000, 800)

    def test_anchor_resolves_to_client_pixels(self) -> None:
        planned = actions.plan_actions(self.profile, [{"type": "click", "target": {"anchor": "play"}}], self.rect)
        step = planned[0]
        self.assertEqual(step["kind"], "click")
        self.assertEqual(step["target"], "anchor:play")
        self.assertEqual(step["normalized"], [0.5, 0.85])
        self.assertEqual(step["screen"], [100 + 500, 50 + 680])

    def test_rejects_absolute_pixels(self) -> None:
        with self.assertRaises(PolicyError) as ctx:
            actions.plan_actions(
                self.profile, [{"type": "click", "target": {"x": 1280, "y": 720}}], self.rect
            )
        self.assertEqual(ctx.exception.details.get("code_hint"), "INVALID_TARGET")

    def test_rejects_unknown_anchor(self) -> None:
        with self.assertRaises(PolicyError) as ctx:
            actions.plan_actions(self.profile, [{"type": "click", "target": {"anchor": "nope"}}], self.rect)
        self.assertIn("nope", str(ctx.exception))

    def test_rejects_confirm_actions(self) -> None:
        with self.assertRaises(PolicyError):
            actions.plan_actions(
                self.profile,
                [{"type": "click", "target": {"anchor": "play"}, "meta": {"confirm": True}}],
                self.rect,
            )

    def test_rejects_forbidden_key(self) -> None:
        with self.assertRaises(PolicyError):
            actions.plan_actions(self.profile, [{"type": "key", "keys": ["alt", "f4"]}], self.rect)

    def test_wait_supports_seconds_only(self) -> None:
        planned = actions.plan_actions(
            self.profile, [{"type": "wait", "condition": {"kind": "seconds", "seconds": 1.5}}], self.rect
        )
        self.assertEqual(planned[0]["seconds"], 1.5)
        with self.assertRaises(KisekiError):
            actions.plan_actions(
                self.profile, [{"type": "wait", "condition": {"kind": "screen", "id": "x"}}], self.rect
            )

    def test_unknown_action_type_is_rejected(self) -> None:
        with self.assertRaises(KisekiError):
            actions.plan_actions(self.profile, [{"type": "script", "code": "import os"}], self.rect)


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "configs").mkdir()
        (root / "configs" / "demo.yaml").write_text(PROFILE_TEXT, encoding="utf-8")
        self.server = Server(Runtime(root=root))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def call(self, method: str, params: dict | None = None, request_id: int = 1) -> dict:
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self.server.handle(message)
        assert response is not None
        return response

    def test_initialize_and_tool_list(self) -> None:
        init = self.call("initialize")
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(init["result"]["serverInfo"]["name"], "kiseki-runtime")
        listed = self.call("tools/list")
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(names, [schema["name"] for schema in TOOL_SCHEMAS])
        self.assertEqual(len(names), 9)

    def test_status_tool_works(self) -> None:
        response = self.call("tools/call", {"name": "game_status", "arguments": {}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["profiles"], ["demo"])

    def test_profiles_tool_skips_examples(self) -> None:
        response = self.call("tools/call", {"name": "game_profiles", "arguments": {}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual([item["id"] for item in payload["profiles"]], ["demo"])
        self.assertFalse(payload["errors"])

    def test_unknown_tool_is_an_error(self) -> None:
        response = self.call("tools/call", {"name": "game_cheat", "arguments": {}})
        self.assertEqual(response["error"]["code"], -32602)

    def test_unimplemented_map_returns_tool_error(self) -> None:
        response = self.call("tools/call", {"name": "game_map", "arguments": {"game_id": "demo"}})
        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["error"]["code"], "NOT_IMPLEMENTED")

    def test_unknown_game_id_reports_known_profiles(self) -> None:
        response = self.call("tools/call", {"name": "game_focus", "arguments": {"game_id": "nope"}})
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload["ok"] is False)
        self.assertEqual(payload["error"]["code"], "PROFILE_ERROR")

    def test_notification_gets_no_response(self) -> None:
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
