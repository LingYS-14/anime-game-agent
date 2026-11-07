"""Contract tests for the Game Profiles shipped in configs/.

They do not touch the real machine: the goal is to catch broken YAML, duplicate
anchor ids and obviously wrong regions before a profile reaches the runtime.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiseki_runtime.profiles import load_profiles  # noqa: E402

CONFIGS = ROOT / "configs"


class ShippedProfilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profiles, cls.errors = load_profiles(CONFIGS)

    def test_every_shipped_profile_loads(self) -> None:
        self.assertEqual(self.errors, [])
        self.assertIn("hsr", self.profiles)
        self.assertIn("endfield-map", self.profiles)

    def test_anchor_ids_are_unique_and_regions_are_normalized(self) -> None:
        for game_id, profile in self.profiles.items():
            anchors = profile.get("ui", {}).get("anchors", [])
            ids = [anchor.get("id") for anchor in anchors]
            self.assertEqual(len(ids), len(set(ids)), f"{game_id}: duplicate anchor id")
            for anchor in anchors:
                region = anchor.get("region")
                self.assertIsInstance(region, list, f"{game_id}: {anchor.get('id')} region")
                self.assertEqual(len(region), 4, f"{game_id}: {anchor.get('id')} region size")
                x0, y0, x1, y1 = region
                for value in region:
                    self.assertGreaterEqual(value, 0.0, f"{game_id}: {anchor.get('id')} below 0")
                    self.assertLessEqual(value, 1.0, f"{game_id}: {anchor.get('id')} above 1")
                self.assertLess(x0, x1, f"{game_id}: {anchor.get('id')} x order")
                self.assertLess(y0, y1, f"{game_id}: {anchor.get('id')} y order")

    def test_skills_reference_known_actions(self) -> None:
        kinds = {"key", "click", "move", "wait", "noop"}
        for game_id, profile in self.profiles.items():
            for skill in profile.get("skills", []):
                for action in skill.get("actions", []):
                    self.assertIn(action.get("type"), kinds, f"{game_id}: skill {skill.get('id')}")

    def test_safety_defaults_keep_dry_run_on(self) -> None:
        for game_id, profile in self.profiles.items():
            safety = profile.get("safety", {})
            self.assertTrue(safety.get("dry_run", True), f"{game_id}: dry_run must default to true")
            self.assertEqual(safety.get("emergency_stop_key", "F12"), "F12", f"{game_id}: F12 stop")


if __name__ == "__main__":
    unittest.main()
