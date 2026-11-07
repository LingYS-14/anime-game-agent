"""Keep the machine awake while the runtime is driving a game.

``SetThreadExecutionState`` is per-thread, so calls must happen on the same
thread that keeps doing the capture/input work (the MCP server is
single-threaded).  ``ES_DISPLAY_REQUIRED`` is what matters here: with the
display off, GDI captures of a game window can come back black or stale.
"""

from __future__ import annotations

from typing import Any

from ..errors import CaptureError

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

_STATE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED


def _set(flags: int) -> Any:
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    ctypes.windll.kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    previous = ctypes.windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags))
    if not previous:
        raise CaptureError("SetThreadExecutionState failed", code_hint="KEEP_AWAKE_FAILED")
    return previous


def keep_awake() -> bool:
    """Ask Windows not to idle-sleep or blank the display for this thread."""

    _set(_STATE)
    return True


def release_awake() -> bool:
    """Undo :func:`keep_awake` so normal power management resumes."""

    _set(ES_CONTINUOUS)
    return True
