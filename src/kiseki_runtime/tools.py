"""M0 tool surface of the KISEKI runtime.

The MCP server in :mod:`kiseki_runtime.mcp_server` is a thin transport over
this module.  Every tool is deliberately narrow: it either reads state or
executes a validated, deterministic action plan.  There is no "run this
script", no absolute-pixel click and no way to bypass the foreground check.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import actions as action_module
from . import estop as estop_module
from . import profiles as profile_module
from .errors import (
    EmergencyStopError,
    KisekiError,
    NotSupportedError,
    PolicyError,
    ProfileError,
    WindowError,
)
from .win32 import capture, input as winput
from .win32 import power

VERSION = "0.1.0"

TOOL_NAMES = [
    "game_profiles",
    "game_focus",
    "game_capture",
    "game_state",
    "game_act",
    "game_skill",
    "game_status",
    "game_map",
    "game_stop",
]


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


class Runtime:
    """One runtime process = one tool server; state is intentionally in-memory."""

    def __init__(
        self,
        root: str | Path | None = None,
        profiles_dir: str | Path | None = None,
    ) -> None:
        default_root = Path(__file__).resolve().parents[2]
        self.root = Path(
            root or os.environ.get("KISEKI_ROOT") or os.environ.get("KISEKI_HOME") or default_root
        )
        self.profiles_dir = Path(
            profiles_dir or os.environ.get("KISEKI_PROFILES") or self.root / "configs"
        )
        self.captures_dir = self.root / "captures"
        self.profiles, self.profile_errors = profile_module.load_profiles(self.profiles_dir)
        self._focused: dict[str, int] = {}
        self._estops: dict[str, estop_module.EmergencyStop] = {}
        self._stopped = False
        self._started_at = time.time()
        self._captures = 0
        self._actions = 0

    # -- helpers -----------------------------------------------------------
    def profile(self, game_id: str) -> dict[str, Any]:
        entry = self.profiles.get(str(game_id))
        if entry is None:
            raise ProfileError(
                f"unknown game profile {game_id!r}",
                code_hint="UNKNOWN_PROFILE",
                known=sorted(self.profiles),
                errors=self.profile_errors,
            )
        return entry

    def _estop(self, profile: dict[str, Any]) -> estop_module.EmergencyStop:
        game_id = str(profile["id"])
        watcher = self._estops.get(game_id)
        if watcher is None:
            watcher = estop_module.EmergencyStop(
                key=profile_module.emergency_stop_key(profile),
                on_trigger=self._on_estop,
            )
            watcher.start()
            self._estops[game_id] = watcher
        return watcher

    def _on_estop(self, key: str) -> None:
        self._stopped = True

    def _focused_window(self, profile: dict[str, Any]) -> dict[str, Any]:
        game_id = str(profile["id"])
        hwnd = self._focused.get(game_id)
        if hwnd is None:
            raise WindowError(
                f"game {game_id!r} is not focused yet; call game_focus first",
                code_hint="WINDOW_NOT_FOCUSED",
                game_id=game_id,
            )
        try:
            return capture.window_info(hwnd)
        except KisekiError:
            self._focused.pop(game_id, None)
            raise

    def _cancel_check(self, profile: dict[str, Any]) -> None:
        if self._stopped:
            raise EmergencyStopError("runtime is stopped; call game_stop to reset", key=None)
        self._estop(profile).check()

    # -- tools -------------------------------------------------------------
    def game_profiles(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profiles_dir": str(self.profiles_dir),
            "profiles": [profile_module.summary(profile) for profile in self.profiles.values()],
            "errors": self.profile_errors,
            "usage": "pass the profile id as game_id to the other tools",
        }

    def game_focus(self, game_id: str) -> dict[str, Any]:
        profile = self.profile(game_id)
        rule = profile_module.window_rule(profile)
        window = capture.find_window(
            process=rule.get("process"),
            title_regex=rule.get("title_regex"),
            class_name=rule.get("class_name"),
        )
        if window is None:
            raise WindowError(
                f"no visible window matches the {game_id!r} window rule",
                code_hint="WINDOW_NOT_FOUND",
                rule=rule,
                hint="start the game first, or fix window.process/title_regex in the profile",
            )
        raised = capture.bring_to_foreground(int(window["hwnd"]))
        self._focused[str(profile["id"])] = int(window["hwnd"])
        self._estop(profile)
        power.keep_awake()
        return {
            "ok": True,
            "game_id": profile["id"],
            "window": window,
            "foreground": raised,
            "client": window["client"],
            "notice": None
            if raised
            else "could not raise the window; input will fail the foreground check",
        }

    def game_capture(
        self,
        game_id: str | None = None,
        region: list[float] | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        profile = self.profile(game_id) if game_id else self._only_focused_profile()
        window = self._focused_window(profile)
        power.keep_awake()
        safe_label = "".join(ch for ch in (label or "capture") if ch.isalnum() or ch in "-_")[:32]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{profile['id']}-{safe_label}-{uuid.uuid4().hex[:6]}.png"
        path = self.captures_dir / name
        meta = capture.capture_window_png(int(window["hwnd"]), path, region=region)
        self._captures += 1
        return {
            "ok": True,
            "game_id": profile["id"],
            "path": str(path),
            "relative_path": str(path.relative_to(self.root)).replace("\\", "/"),
            "width": meta["width"],
            "height": meta["height"],
            "region": region,
            "foreground": capture.is_foreground(int(window["hwnd"])),
            "notice": "window is not in the foreground; the frame may be stale or covered"
            if not capture.is_foreground(int(window["hwnd"]))
            else None,
        }

    def game_state(self, game_id: str | None = None) -> dict[str, Any]:
        profile = self.profile(game_id) if game_id else self._only_focused_profile()
        window = self._focused_window(profile)
        return {
            "ok": True,
            "game_id": profile["id"],
            "window": window,
            "foreground": capture.is_foreground(int(window["hwnd"])),
            "dry_run_default": profile_module.dry_run_default(profile),
            "emergency_stop": self._estop(profile).status(),
            "estopped": self._stopped,
            "captures": self._captures,
        }

    def game_act(
        self,
        game_id: str,
        actions: list[dict[str, Any]],
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        profile = self.profile(game_id)
        window = self._focused_window(profile)
        self._cancel_check(profile)
        planned = action_module.plan_actions(profile, actions, window["client"])
        if dry_run is None:
            dry_run = profile_module.dry_run_default(profile)
        if dry_run:
            return {
                "ok": True,
                "game_id": profile["id"],
                "dry_run": True,
                "planned": planned,
                "hint": "set dry_run=false (and safety.dry_run: false in the profile) to inject input",
            }
        power.keep_awake()
        trace = action_module.execute_actions(
            planned,
            hwnd=int(window["hwnd"]),
            focus_check=bool(profile.get("input", {}).get("focus_check", True)),
            cancel_check=lambda: self._cancel_check(profile),
        )
        self._actions += len(trace)
        return {"ok": True, "game_id": profile["id"], "dry_run": False, "executed": trace}

    def game_skill(
        self,
        game_id: str,
        skill: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        profile = self.profile(game_id)
        entry = None
        for candidate in profile.get("skills") or []:
            if isinstance(candidate, dict) and candidate.get("id") == skill:
                entry = candidate
                break
        if entry is None:
            raise PolicyError(
                f"skill {skill!r} is not declared by profile {game_id!r}",
                code_hint="UNKNOWN_SKILL",
                known=[item.get("id") for item in profile.get("skills") or [] if isinstance(item, dict)],
            )
        declared = entry.get("actions")
        if not isinstance(declared, list) or not declared:
            raise NotSupportedError(
                f"skill {skill!r} has no deterministic action list; skills that need "
                "perception are implemented in M2",
                skill=skill,
            )
        result = self.game_act(game_id, declared, dry_run=dry_run)
        result["skill"] = skill
        return result

    def game_status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": VERSION,
            "root": str(self.root),
            "profiles_dir": str(self.profiles_dir),
            "profiles": sorted(self.profiles),
            "profile_errors": self.profile_errors,
            "focused": {game: hwnd for game, hwnd in self._focused.items()},
            "estopped": self._stopped,
            "emergency_stop": {game: watcher.status() for game, watcher in self._estops.items()},
            "captures": self._captures,
            "actions": self._actions,
            "uptime_s": round(time.time() - self._started_at, 1),
            "platform": os.name,
            "warnings": []
            if os.name == "nt"
            else ["the runtime only supports Windows; read-only tools still work"],
        }

    def game_map(self, game_id: str | None = None) -> dict[str, Any]:
        raise NotSupportedError(
            "map understanding (game_map) is planned for M2; nothing is implemented yet",
            stage="M2",
        )

    def game_stop(self, reason: str | None = None) -> dict[str, Any]:
        released = winput.release_all()
        power.release_awake()
        for watcher in self._estops.values():
            watcher.reset()
        self._stopped = False
        self._focused.clear()
        return {
            "ok": True,
            "released": released,
            "reason": reason,
            "hint": "all held keys/buttons were released and every emergency stop was reset",
        }

    # -- helpers -----------------------------------------------------------
    def _only_focused_profile(self) -> dict[str, Any]:
        if not self._focused:
            raise WindowError(
                "no game is focused; call game_focus with a game_id first",
                code_hint="WINDOW_NOT_FOCUSED",
                known=sorted(self.profiles),
            )
        if len(self._focused) > 1:
            raise PolicyError(
                "several games are focused; pass game_id explicitly",
                focused=sorted(self._focused),
            )
        game_id = next(iter(self._focused))
        return self.profile(game_id)

    # -- diagnostics -------------------------------------------------------
    def self_check(self, capture_screen: bool = True) -> dict[str, Any]:
        report: dict[str, Any] = {
            "version": VERSION,
            "root": str(self.root),
            "profiles_dir": str(self.profiles_dir),
            "profiles_dir_exists": self.profiles_dir.is_dir(),
            "profiles": sorted(self.profiles),
            "profile_errors": self.profile_errors,
            "screens": {},
        }
        if os.name != "nt":
            report["ok"] = False
            report["error"] = "kiseki_runtime requires Windows"
            return report
        try:
            report["screens"]["desktop"] = capture.desktop_rect()
            report["screens"]["foreground"] = capture.window_info(capture.foreground_window())
            report["screens"]["windows"] = len(capture.enum_windows(visible_only=True))
        except KisekiError as error:
            report["screens"]["error"] = error.to_dict()
        if capture_screen:
            try:
                self.captures_dir.mkdir(parents=True, exist_ok=True)
                path = self.captures_dir / "selfcheck.png"
                meta = capture.capture_screen_png(path)
                report["screens"]["capture"] = {**meta, "bytes": path.stat().st_size}
                self._captures += 1
            except KisekiError as error:
                report["screens"]["capture_error"] = error.to_dict()
        report["ok"] = not self.profile_errors and "error" not in report["screens"]
        return report
