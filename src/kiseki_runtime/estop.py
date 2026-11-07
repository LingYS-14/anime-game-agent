"""Emergency stop: the F12 panic key.

The watcher polls ``GetAsyncKeyState`` on its own thread.  When the key is
pressed it sets a latch, releases every key/button the runtime is holding and
keeps the latch until an explicit ``game_stop`` / ``reset`` call, so a burst of
queued actions cannot resume after the user has taken control back.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .errors import EmergencyStopError
from .win32 import input as winput


class EmergencyStop:
    def __init__(
        self,
        key: str = "F12",
        poll_hz: float = 20.0,
        on_trigger: Callable[[str], None] | None = None,
    ) -> None:
        self.key = key
        self.vk = winput.resolve_vk(key)
        self.poll_hz = max(1.0, float(poll_hz))
        self.on_trigger = on_trigger
        self._triggered = False
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._halt.clear()
            self._thread = threading.Thread(target=self._loop, name="kiseki-estop", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._halt.set()

    def _loop(self) -> None:
        while not self._halt.is_set():
            if winput.key_pressed(self.vk):
                self._triggered = True
                try:
                    winput.release_all()
                except Exception:  # noqa: BLE001 - never let the panic path die
                    pass
                if self.on_trigger is not None:
                    try:
                        self.on_trigger(self.key)
                    except Exception:  # noqa: BLE001
                        pass
                return
            time.sleep(1.0 / self.poll_hz)

    # -- state -------------------------------------------------------------
    @property
    def triggered(self) -> bool:
        return self._triggered

    def check(self) -> None:
        if self._triggered:
            raise EmergencyStopError(
                f"emergency stop ({self.key}) is active; call game_stop to release and reset",
                key=self.key,
            )

    def reset(self) -> None:
        self._triggered = False
        self.start()

    def status(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "triggered": self._triggered,
            "watching": bool(self._thread and self._thread.is_alive()),
        }
