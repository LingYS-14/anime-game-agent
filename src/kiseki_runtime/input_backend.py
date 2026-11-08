"""Input backends: route planned actions to SendInput or a CH9329 USB-HID bridge.

The runtime always reasons in screen coordinates.  ``SendInputBackend`` hands
them to ``SendInput``; ``HidSerialBackend`` drives a real HID device over a
serial port, moving the cursor with relative reports and closing the loop by
reading the current cursor position.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import hid_serial
from .errors import InputError, KisekiError
from .win32 import capture, input as winput

DEFAULT_TOLERANCE_PX = 2
MAX_MOVE_ITERATIONS = 40
MOUSE_BUTTONS = ("left", "right", "middle")


class InputBackend:
    """Minimal interface the executor needs."""

    name = "abstract"

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1, hold_ms: float = 60) -> None:
        raise NotImplementedError

    def key_down(self, name: str) -> None:
        raise NotImplementedError

    def key_up(self, name: str) -> None:
        raise NotImplementedError

    def press_combo(self, keys: list[str], *, hold_ms: float = 40) -> None:
        raise NotImplementedError

    def mouse_move_by(self, dx: int, dy: int, *, duration_ms: float = 0) -> None:
        raise NotImplementedError

    def mouse_move_to(self, x: int, y: int) -> None:
        raise NotImplementedError

    def release_all(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SendInputBackend(InputBackend):
    name = "sendinput"

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1, hold_ms: float = 60) -> None:
        winput.click(x, y, button=button, clicks=clicks, hold_ms=hold_ms)

    def key_down(self, name: str) -> None:
        winput.key_down(name)

    def key_up(self, name: str) -> None:
        winput.key_up(name)

    def press_combo(self, keys: list[str], *, hold_ms: float = 40) -> None:
        winput.press_combo(keys, hold_ms=hold_ms)

    def mouse_move_by(self, dx: int, dy: int, *, duration_ms: float = 0) -> None:
        winput.mouse_move_by(dx, dy, duration_ms=duration_ms)

    def mouse_move_to(self, x: int, y: int) -> None:
        winput.mouse_move_to(x, y)

    def release_all(self) -> None:
        winput.release_all()


class HidSerialBackend(InputBackend):
    """Drive a CH9329 bridge; relative reports plus a cursor feedback loop."""

    name = "hid-serial"

    def __init__(
        self,
        port: str = "COM3",
        *,
        baudrate: int = 115200,
        tolerance_px: int = DEFAULT_TOLERANCE_PX,
        transport: Any | None = None,
        cursor_getter: Callable[[], tuple[int, int]] | None = None,
        report_gap_s: float = hid_serial.REPORT_GAP_S,
    ) -> None:
        self.tolerance_px = max(1, int(tolerance_px))
        self.cursor_getter = cursor_getter or capture.cursor_position
        if transport is None:
            transport = hid_serial.Ch9329(port, baudrate=baudrate, report_gap_s=report_gap_s)
            transport.open()
        self.transport = transport
        self._pressed: list[str] = []
        self._held_buttons: set[str] = set()

    # -- keyboard -----------------------------------------------------------
    def key_down(self, name: str) -> None:
        key = name.strip()
        if key.lower() in {item.lower() for item in self._pressed}:
            return
        winput.resolve_vk(key)  # fail fast on unknown keys before touching hardware
        self._pressed.append(key)
        self._send_keys()

    def key_up(self, name: str) -> None:
        key = name.strip()
        remaining = [item for item in self._pressed if item.lower() != key.lower()]
        if len(remaining) == len(self._pressed):
            return
        self._pressed = remaining
        self._send_keys()

    def press_combo(self, keys: list[str], *, hold_ms: float = 40) -> None:
        for name in keys:
            self.key_down(name)
        try:
            if hold_ms > 0:
                time.sleep(hold_ms / 1000.0)
        finally:
            for name in reversed(keys):
                self.key_up(name)

    def _send_keys(self) -> None:
        modifier, usages = hid_serial.resolver(self._pressed)
        self.transport.write(hid_serial.keyboard_frame(modifier, usages))

    # -- mouse --------------------------------------------------------------
    def mouse_move_by(self, dx: int, dy: int, *, duration_ms: float = 0) -> None:
        frames = hid_serial.axis_chunks(int(dx), int(dy))
        if not frames:
            return
        if duration_ms and duration_ms > 0:
            # Spread the move over the requested duration with 10 ms reports.
            delay = max(hid_serial.REPORT_GAP_S, duration_ms / 1000.0 / len(frames))
        else:
            delay = hid_serial.REPORT_GAP_S
        for chunk_dx, chunk_dy, chunk_wheel in frames:
            self.transport.write(hid_serial.mouse_frame(0x00, chunk_dx, chunk_dy, chunk_wheel))
            if delay > 0:
                time.sleep(delay)

    def mouse_move_to(self, x: int, y: int) -> None:
        """Move the pointer to ``(x, y)`` with relative reports and a cursor loop."""

        for _ in range(MAX_MOVE_ITERATIONS):
            cx, cy = self.cursor_getter()
            dx, dy = int(x) - int(cx), int(y) - int(cy)
            if abs(dx) <= self.tolerance_px and abs(dy) <= self.tolerance_px:
                return
            self.mouse_move_by(dx, dy)
            time.sleep(hid_serial.REPORT_GAP_S)
        raise InputError(
            "HID pointer did not reach the requested position",
            code_hint="HID_MOVE_TIMEOUT",
            target=[x, y],
            tolerance_px=self.tolerance_px,
        )

    def click(self, x: int, y: int, *, button: str = "left", clicks: int = 1, hold_ms: float = 60) -> None:
        name = button.lower()
        if name not in MOUSE_BUTTONS:
            raise KisekiError(f"unknown mouse button: {button!r}", code_hint="INVALID_KEY", button=button)
        self.mouse_move_to(x, y)
        for _ in range(max(1, int(clicks))):
            self._send_click_frame(name, down=True)
            if hold_ms > 0:
                time.sleep(hold_ms / 1000.0)
            self._send_click_frame(name, down=False)
            self._held_buttons.discard(name)

    def _send_click_frame(self, name: str, *, down: bool) -> None:
        if down:
            self._held_buttons.add(name)
        else:
            self._held_buttons.discard(name)
        mask = 0
        for button_name, bit in (("left", 0x01), ("right", 0x02), ("middle", 0x04)):
            if button_name in self._held_buttons:
                mask |= bit
        self.transport.write(hid_serial.mouse_frame(mask, 0, 0))

    def release_all(self) -> None:
        self._pressed = []
        self._held_buttons.clear()
        self.transport.write(hid_serial.keyboard_frame(0, []))
        self.transport.write(hid_serial.mouse_frame(0, 0, 0))

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()


def from_profile(profile: dict[str, Any]) -> InputBackend:
    """Build the backend named by ``input.backend`` (default ``sendinput``)."""

    section = profile.get("input") if isinstance(profile.get("input"), dict) else {}
    name = str(section.get("backend") or "sendinput").strip().lower()
    if name in ("sendinput", "send-input", "win32"):
        return SendInputBackend()
    if name in ("hid-serial", "hid", "ch9329", "serial"):
        serial = section.get("serial") if isinstance(section.get("serial"), dict) else {}
        port = str(serial.get("port") or "COM3")
        baudrate = int(serial.get("baudrate") or 115200)
        return HidSerialBackend(port, baudrate=baudrate)
    raise KisekiError(
        f"unsupported input backend: {name!r}",
        code_hint="UNKNOWN_INPUT_BACKEND",
        backend=name,
        supported=["sendinput", "hid-serial"],
    )
