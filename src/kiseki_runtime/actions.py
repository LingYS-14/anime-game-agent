"""Action planning: turn high-level KISEKI actions into concrete steps.

Planning is pure (no injection): it validates the request against the Game
Profile, resolves anchors / normalized coordinates into screen pixels and
returns a trace the caller can either execute or return as a dry-run preview.
Execution lives in :func:`execute_actions` and performs the actual injection.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .errors import NotSupportedError, PolicyError
from . import profiles as profile_module
from . import input_backend
from .win32 import capture, input as winput

MAX_ACTIONS = 64
DEFAULT_HOLD_MS = 60


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PolicyError(f"{field} must be a number", field=field, value=value)
    return float(value)


def anchor_center(profile: dict[str, Any], anchor_id: str) -> tuple[float, float]:
    entry = profile_module.anchors(profile).get(anchor_id)
    if entry is None:
        raise PolicyError(
            f"anchor {anchor_id!r} is not declared by this Game Profile",
            code_hint="UNKNOWN_ANCHOR",
            known=sorted(profile_module.anchors(profile)),
        )
    region = entry.get("region")
    if not isinstance(region, list) or len(region) != 4:
        raise PolicyError(f"anchor {anchor_id!r} has no valid region", anchor=anchor_id)
    x0, y0, x1, y1 = (_require_number(value, f"{anchor_id}.region") for value in region)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def resolve_target(profile: dict[str, Any], target: Any) -> tuple[float, float, str]:
    """Return ``(nx, ny, description)`` for a click target in ``[0, 1]`` space."""

    if not isinstance(target, dict):
        raise PolicyError("click target must be an object", target=target)
    if "anchor" in target:
        anchor_id = str(target["anchor"])
        nx, ny = anchor_center(profile, anchor_id)
        return nx, ny, f"anchor:{anchor_id}"
    if "x" in target and "y" in target:
        nx = _require_number(target["x"], "target.x")
        ny = _require_number(target["y"], "target.y")
        if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
            raise PolicyError(
                "normalized coordinates must be within 0..1; absolute pixels are rejected",
                code_hint="INVALID_TARGET",
                target=target,
            )
        return nx, ny, "normalized"
    raise PolicyError("click target needs either 'anchor' or 'x'/'y'", target=target)


def _client_rect(client_rect: tuple[int, int, int, int] | list[int]) -> tuple[int, int, int, int]:
    if len(client_rect) != 4:
        raise PolicyError("client_rect must be [left, top, width, height]", client_rect=client_rect)
    left, top, width, height = (int(value) for value in client_rect)
    if width <= 0 or height <= 0:
        raise PolicyError("window client area is empty", client_rect=list(client_rect))
    return left, top, width, height


def _plan_click(
    profile: dict[str, Any], action: dict[str, Any], rect: tuple[int, int, int, int]
) -> dict[str, Any]:
    left, top, width, height = rect
    nx, ny, source = resolve_target(profile, action.get("target"))
    x = int(round(left + nx * width))
    y = int(round(top + ny * height))
    return {
        "kind": "click",
        "target": source,
        "normalized": [round(nx, 6), round(ny, 6)],
        "screen": [x, y],
        "button": str(action.get("button") or "left"),
        "clicks": max(1, min(3, int(action.get("clicks", 1)))),
        "hold_ms": float(action.get("hold_ms", DEFAULT_HOLD_MS)),
    }


def _plan_key(profile: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    keys = action.get("keys")
    if not isinstance(keys, list) or not keys:
        raise PolicyError("key action needs a non-empty 'keys' list")
    names = [str(name).strip() for name in keys]
    forbidden = {
        str(item).replace(" ", "").lower()
        for item in profile_module.safety(profile).get("forbid_keys") or []
    }
    combo = "+".join(name.lower() for name in names)
    if combo in forbidden or any(name.lower() in forbidden for name in names):
        raise PolicyError(f"key combination {names} is forbidden by the profile", keys=names)
    for name in names:
        winput.resolve_vk(name)  # fail fast with a clear error
    mode = str(action.get("action") or "tap")
    if mode not in ("tap", "hold", "down", "up", "release"):
        raise PolicyError(f"unsupported key action: {mode!r}", action=mode)
    return {
        "kind": "key",
        "keys": names,
        "action": mode,
        "hold_ms": float(action.get("hold_ms", 40)),
    }


def _plan_move(action: dict[str, Any]) -> dict[str, Any]:
    dx = _require_number(action.get("dx", 0), "dx")
    dy = _require_number(action.get("dy", 0), "dy")
    return {
        "kind": "move",
        "dx": int(round(dx)),
        "dy": int(round(dy)),
        "relative": bool(action.get("relative", True)),
        "duration_ms": float(action.get("duration_ms", 0)),
    }


def _plan_wait(action: dict[str, Any]) -> dict[str, Any]:
    condition = action.get("condition")
    if not isinstance(condition, dict):
        raise PolicyError("wait action needs a 'condition' object")
    kind = str(condition.get("kind") or "")
    if kind == "seconds":
        seconds = _require_number(condition.get("seconds", 0), "condition.seconds")
    elif kind == "stable":
        seconds = _require_number(condition.get("stable_ms", 500), "condition.stable_ms") / 1000.0
    else:
        raise NotSupportedError(
            f"wait condition {kind!r} needs perception (M1); only 'seconds' and 'stable' work in M0",
            condition=condition,
        )
    return {"kind": "wait", "seconds": max(0.0, seconds), "condition": kind}


def plan_actions(
    profile: dict[str, Any],
    actions: Any,
    client_rect: tuple[int, int, int, int] | list[int],
) -> list[dict[str, Any]]:
    """Validate and normalize a list of actions; raises :class:`PolicyError`."""

    if not isinstance(actions, list) or not actions:
        raise PolicyError("'actions' must be a non-empty array")
    if len(actions) > MAX_ACTIONS:
        raise PolicyError(f"at most {MAX_ACTIONS} actions per call, got {len(actions)}")
    rect = _client_rect(client_rect)
    planned: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise PolicyError(f"action[{index}] must be an object")
        meta = action.get("meta")
        if isinstance(meta, dict) and meta.get("confirm"):
            raise PolicyError(
                "action explicitly requires human confirmation; runtime refuses it",
                code_hint="CONFIRM_REQUIRED",
                index=index,
            )
        kind = str(action.get("type") or "")
        if kind == "click":
            planned.append(_plan_click(profile, action, rect))
        elif kind == "key":
            planned.append(_plan_key(profile, action))
        elif kind == "move":
            planned.append(_plan_move(action))
        elif kind == "wait":
            planned.append(_plan_wait(action))
        elif kind in ("noop",):
            planned.append({"kind": "noop"})
        else:
            raise NotSupportedError(
                f"action type {kind!r} is not executable in M0",
                action_type=kind,
                supported=["click", "key", "move", "wait", "noop"],
            )
    return planned


def execute_actions(
    planned: list[dict[str, Any]],
    *,
    hwnd: int,
    backend: input_backend.InputBackend | None = None,
    focus_check: bool = True,
    cancel_check: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Execute planned steps with foreground enforcement and cancellation."""

    injector = backend or input_backend.SendInputBackend()
    trace: list[dict[str, Any]] = []
    for step in planned:
        if cancel_check is not None:
            cancel_check()
        if focus_check and step["kind"] not in ("noop", "wait"):
            if not capture.is_foreground(hwnd):
                raise PolicyError(
                    "target window is not in the foreground; refusing to inject input",
                    code_hint="WINDOW_NOT_FOREGROUND",
                    hwnd=hwnd,
                )
        kind = step["kind"]
        if kind == "click":
            injector.click(
                step["screen"][0],
                step["screen"][1],
                button=step["button"],
                clicks=step["clicks"],
                hold_ms=step["hold_ms"],
            )
        elif kind == "key":
            mode = step["action"]
            if mode in ("down",):
                for name in step["keys"]:
                    injector.key_down(name)
            elif mode in ("up", "release"):
                for name in step["keys"]:
                    injector.key_up(name)
            elif mode == "tap":
                injector.press_combo(step["keys"], hold_ms=step["hold_ms"])
            else:  # hold: press, keep for hold_ms, release
                for name in step["keys"]:
                    injector.key_down(name)
                try:
                    time.sleep(max(0.0, step["hold_ms"]) / 1000.0)
                finally:
                    for name in reversed(step["keys"]):
                        injector.key_up(name)
        elif kind == "move":
            if step["relative"]:
                injector.mouse_move_by(step["dx"], step["dy"], duration_ms=step["duration_ms"])
            else:
                injector.mouse_move_to(step["dx"], step["dy"])
        elif kind == "wait":
            time.sleep(step["seconds"])
        trace.append({"ok": True, **step})
    return trace
