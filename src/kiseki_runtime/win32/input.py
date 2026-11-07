"""Keyboard and mouse injection for Windows via ``SendInput`` (ctypes only).

The runtime only injects *validated* actions: the tool layer resolves anchors
to screen pixels and checks the foreground window before calling in here.
``release_all`` is the panic path used by ``game_stop`` and the F12 watcher.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from typing import Any

from ..errors import InputError
from .capture import desktop_rect, enable_dpi_awareness

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

# Virtual-key codes for the names used by game profiles and skills.
_VK: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "lshift": 0xA0,
    "rshift": 0xA1,
    "lctrl": 0xA2,
    "rctrl": 0xA3,
    "lalt": 0xA4,
    "ralt": 0xA5,
}
for _index in range(1, 25):
    _VK[f"f{_index}"] = 0x6F + _index
for _char in "abcdefghijklmnopqrstuvwxyz":
    _VK[_char] = ord(_char.upper())
for _digit in "0123456789":
    _VK[_digit] = ord(_digit)

_MOUSE_BUTTONS: dict[str, tuple[int, int]] = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "mouseleft": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "mouseright": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    "mousemiddle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

_WHEEL: dict[str, int] = {"wheelup": 120, "wheeldown": -120, "scrollup": 120, "scrolldown": -120}

_held_keys: dict[int, str] = {}
_held_buttons: dict[str, int] = {}


def _send(*inputs: INPUT) -> None:
    if not inputs:
        return
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise InputError(
            f"SendInput inserted {sent} of {len(inputs)} events (error {ctypes.get_last_error()})"
        )


def _keyboard(vk: int, flags: int, scan: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return event


def _mouse(dx: int = 0, dy: int = 0, data: int = 0, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = INPUT_MOUSE
    event.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return event


def resolve_vk(name: str) -> int:
    """Resolve a key name (``"F12"``, ``"enter"``, ``"a"``) to a VK code."""

    key = str(name).strip().lower()
    if key in _VK:
        return _VK[key]
    raise InputError(f"unknown key name: {name!r}")


def key_pressed(vk: int) -> bool:
    """True while the physical key is held (used by the emergency stop)."""

    return bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)


def key_down(name: str) -> dict[str, Any]:
    vk = resolve_vk(name)
    _send(_keyboard(vk, 0))
    _held_keys[vk] = name
    return {"key": name, "vk": vk}


def key_up(name: str) -> dict[str, Any]:
    vk = resolve_vk(name)
    _send(_keyboard(vk, KEYEVENTF_KEYUP))
    _held_keys.pop(vk, None)
    return {"key": name, "vk": vk}


def key_tap(name: str, hold_ms: float = 40) -> dict[str, Any]:
    key_down(name)
    time.sleep(max(0, hold_ms) / 1000.0)
    key_up(name)
    return {"key": name, "hold_ms": hold_ms}


def press_combo(names: list[str], hold_ms: float = 40) -> dict[str, Any]:
    """Press keys together (e.g. ``["alt", "f4"]``), releasing in reverse order."""

    for name in names:
        key_down(name)
    try:
        time.sleep(max(0, hold_ms) / 1000.0)
    finally:
        for name in reversed(names):
            key_up(name)
    return {"keys": names, "hold_ms": hold_ms}


def send_text(text: str) -> dict[str, Any]:
    """Type arbitrary Unicode text using ``KEYEVENTF_UNICODE`` injection."""

    encoded = text.encode("utf-16-le")
    units = [int.from_bytes(encoded[i : i + 2], "little") for i in range(0, len(encoded), 2)]
    events: list[INPUT] = []
    for unit in units:
        events.append(_keyboard(0, KEYEVENTF_UNICODE, scan=unit))
        events.append(_keyboard(0, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, scan=unit))
    _send(*events)
    return {"chars": len(units)}


def mouse_move_to(x: int, y: int) -> dict[str, Any]:
    enable_dpi_awareness()
    desktop = desktop_rect()
    width = max(1, desktop["width"] - 1)
    height = max(1, desktop["height"] - 1)
    nx = int(round((int(x) - desktop["x"]) * 65535 / width))
    ny = int(round((int(y) - desktop["y"]) * 65535 / height))
    nx = max(0, min(65535, nx))
    ny = max(0, min(65535, ny))
    _send(
        _mouse(
            dx=nx,
            dy=ny,
            flags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        )
    )
    return {"x": int(x), "y": int(y)}


def mouse_move_by(dx: int, dy: int, duration_ms: float = 0) -> dict[str, Any]:
    steps = max(1, min(60, int(duration_ms / 16) or 1))
    step_x = int(dx) // steps
    step_y = int(dy) // steps
    for _ in range(steps):
        _send(_mouse(dx=step_x, dy=step_y, flags=MOUSEEVENTF_MOVE))
        if duration_ms:
            time.sleep(max(0, duration_ms) / 1000.0 / steps)
    return {"dx": int(dx), "dy": int(dy), "duration_ms": duration_ms}


def click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    hold_ms: float = 60,
) -> dict[str, Any]:
    if button not in _MOUSE_BUTTONS:
        raise InputError(f"unknown mouse button: {button!r}")
    down, up = _MOUSE_BUTTONS[button]
    mouse_move_to(x, y)
    for _ in range(max(1, min(3, int(clicks)))):
        _send(_mouse(flags=down))
        _held_buttons[button] = down
        time.sleep(max(0, hold_ms) / 1000.0)
        _send(_mouse(flags=up))
        _held_buttons.pop(button, None)
        time.sleep(0.02)
    return {"x": int(x), "y": int(y), "button": button, "clicks": clicks, "hold_ms": hold_ms}


def scroll(notches: int) -> dict[str, Any]:
    _send(_mouse(data=int(notches) * 120, flags=MOUSEEVENTF_WHEEL))
    return {"notches": int(notches)}


def release_all() -> dict[str, Any]:
    """Release every key and mouse button this process pressed."""

    released_buttons: list[str] = []
    for button, down in list(_held_buttons.items()):
        _, up = _MOUSE_BUTTONS.get(button, (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP))
        _send(_mouse(flags=up))
        released_buttons.append(button)
    _held_buttons.clear()

    released_keys: list[str] = []
    for vk, name in list(_held_keys.items()):
        _send(_keyboard(vk, KEYEVENTF_KEYUP))
        released_keys.append(name)
    _held_keys.clear()
    return {"keys": released_keys, "buttons": released_buttons}
