"""Game Profile loading.

A Game Profile is a hand-written YAML file under ``configs/``.  One profile
per game; the runtime itself must stay game-agnostic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import ProfileError
from .yamlmini import load_file

EXAMPLE_MARKERS = ("example", "sample", "template")


def load_profile_file(path: str | Path) -> dict[str, Any]:
    """Load and validate one profile, returning a plain dictionary."""

    source = Path(path)
    try:
        data = load_file(str(source))
    except Exception as exc:  # noqa: BLE001 - surface the file name
        raise ProfileError(f"could not parse profile {source}: {exc}", path=str(source)) from exc
    if not isinstance(data, dict):
        raise ProfileError(f"profile {source} must be a mapping", path=str(source))
    profile_id = str(data.get("id") or "").strip()
    if not profile_id:
        raise ProfileError(f"profile {source} has no 'id'", path=str(source))
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ProfileError(f"profile {source} has no 'name'", path=str(source))
    window = data.get("window")
    if window is not None and not isinstance(window, dict):
        raise ProfileError(f"profile {source}: 'window' must be a mapping", path=str(source))
    data["__source__"] = str(source)
    return data


def load_profiles(directory: str | Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Load every non-example ``*.yaml`` in ``directory``.

    Returns ``(profiles_by_id, errors)``; a broken file is reported instead of
    aborting the whole runtime, so one bad profile cannot brick the MCP server.
    """

    root = Path(directory)
    if not root.is_dir():
        raise ProfileError(f"profiles directory not found: {root}")
    profiles: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        if any(marker in path.stem.lower() for marker in EXAMPLE_MARKERS):
            continue
        try:
            profile = load_profile_file(path)
        except ProfileError as exc:
            errors.append({"path": str(path), "error": exc.message})
            continue
        profiles[profile["id"]] = profile
    return profiles, errors


def window_rule(profile: dict[str, Any]) -> dict[str, Any]:
    window = profile.get("window")
    return window if isinstance(window, dict) else {}


def safety(profile: dict[str, Any]) -> dict[str, Any]:
    value = profile.get("safety")
    return value if isinstance(value, dict) else {}


def anchors(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ui = profile.get("ui")
    raw = ui.get("anchors") if isinstance(ui, dict) else None
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("id"):
                result[str(entry["id"])] = entry
    return result


def emergency_stop_key(profile: dict[str, Any]) -> str:
    return str(safety(profile).get("emergency_stop_key") or "F12")


def dry_run_default(profile: dict[str, Any]) -> bool:
    """Profiles are conservative by default: dry-run until explicitly disabled."""

    return bool(safety(profile).get("dry_run", True))


def summary(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": profile.get("id"),
        "name": profile.get("name"),
        "version": profile.get("version"),
        "game_version": profile.get("game_version"),
        "region": profile.get("region"),
        "source": profile.get("__source__"),
        "window": window_rule(profile),
        "anchors": sorted(anchors(profile)),
        "dry_run": dry_run_default(profile),
        "emergency_stop_key": emergency_stop_key(profile),
        "tasks": [task.get("id") for task in profile.get("tasks") or [] if isinstance(task, dict)],
    }
