"""CH9329 USB-HID bridge transport (M1.3, hardware pending).

The module converts KISEKI's abstract actions into CH9329 serial frames so the
runtime can drive a *real* USB keyboard/mouse instead of ``SendInput``.  This
matters for anti-cheat protected games (ACE filters synthetic SendInput while a
hardware HID device is indistinguishable from a real one).

Everything here is offline-testable: frame builders are pure functions and the
serial port is lazy, so the unit tests never need the physical module.  The
wire format follows the common CH9329 firmware:

    header  57 AB 00
    command 01 = GET_INFO, 02 = keyboard, 04 = relative mouse
    length  payload length
    payload ...
    checksum sum(all previous bytes) & 0xFF
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from .errors import KisekiError

HEADER = b"\x57\xab\x00"
CMD_GET_INFO = 0x01
CMD_KEYBOARD = 0x02
CMD_MOUSE_RELATIVE = 0x04

MOUSE_DATA_PREFIX = 0x01
MAX_AXIS = 127
REPORT_GAP_S = 0.010
RELEASE_FRAMES = 3

# USB HID usage ids (keyboard page 0x07) for the keys KISEKI profiles use.
KEYCODES: dict[str, int] = {
    "a": 0x04, "b": 0x05, "c": 0x06, "d": 0x07, "e": 0x08, "f": 0x09,
    "g": 0x0A, "h": 0x0B, "i": 0x0C, "j": 0x0D, "k": 0x0E, "l": 0x0F,
    "m": 0x10, "n": 0x11, "o": 0x12, "p": 0x13, "q": 0x14, "r": 0x15,
    "s": 0x16, "t": 0x17, "u": 0x18, "v": 0x19, "w": 0x1A, "x": 0x1B,
    "y": 0x1C, "z": 0x1D,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22, "6": 0x23,
    "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "tab": 0x2B, "space": 0x2C, " ": 0x2C,
    "minus": 0x2D, "-": 0x2D, "equals": 0x2E, "=": 0x2E,
    "left": 0x50, "right": 0x4F, "down": 0x51, "up": 0x52,
    "f1": 0x3A, "f2": 0x3B, "f3": 0x3C, "f4": 0x3D, "f5": 0x3E,
    "f6": 0x3F, "f7": 0x40, "f8": 0x41, "f9": 0x42, "f10": 0x43,
    "f11": 0x44, "f12": 0x45,
}

MODIFIER_BITS: dict[str, int] = {
    "ctrl": 0x01, "control": 0x01, "left_ctrl": 0x01,
    "shift": 0x02, "left_shift": 0x02,
    "alt": 0x04, "menu": 0x04, "left_alt": 0x04,
    "win": 0x08, "meta": 0x08, "super": 0x08, "left_gui": 0x08,
    "right_ctrl": 0x10, "right_shift": 0x20, "right_alt": 0x40, "right_gui": 0x80,
}

MOUSE_BUTTONS: dict[str, int] = {
    "left": 0x01, "right": 0x02, "middle": 0x04,
}


def checksum(body: bytes) -> int:
    return sum(body) & 0xFF


def frame(command: int, data: bytes = b"") -> bytes:
    """Build one CH9329 frame with the trailing checksum."""

    if not 0 <= int(command) <= 0xFF:
        raise KisekiError("command must fit in one byte", code_hint="INVALID_FRAME", command=command)
    payload = bytes(data)
    if len(payload) > 0xFF:
        raise KisekiError(
            "payload too long for one CH9329 frame",
            code_hint="INVALID_FRAME",
            length=len(payload),
        )
    body = HEADER + bytes([int(command) & 0xFF, len(payload)]) + payload
    return body + bytes([checksum(body)])


def keyboard_frame(modifier: int, keys: Iterable[int]) -> bytes:
    """USB keyboard report: modifier byte, reserved byte, 6 key slots."""

    slots = [int(key) & 0xFF for key in keys][:6]
    data = bytes([int(modifier) & 0xFF, 0x00, *slots, *([0x00] * (6 - len(slots)))])
    return frame(CMD_KEYBOARD, data)


def mouse_frame(buttons: int, dx: int, dy: int, wheel: int = 0) -> bytes:
    """Relative mouse report; each axis is clamped to -127..127 first."""

    def clamp(value: int) -> int:
        return max(-MAX_AXIS, min(MAX_AXIS, int(value))) & 0xFF

    data = bytes(
        [
            MOUSE_DATA_PREFIX,
            int(buttons) & 0x1F,
            clamp(dx),
            clamp(dy),
            clamp(wheel),
        ]
    )
    return frame(CMD_MOUSE_RELATIVE, data)


def axis_chunks(dx: int, dy: int, wheel: int = 0) -> list[tuple[int, int, int]]:
    """Split a movement into CH9329-sized chunks (each axis within ±127)."""

    remaining_x, remaining_y, remaining_wheel = int(dx), int(dy), int(wheel)
    chunks: list[tuple[int, int, int]] = []
    while remaining_x or remaining_y or remaining_wheel:
        step_x = max(-MAX_AXIS, min(MAX_AXIS, remaining_x))
        step_y = max(-MAX_AXIS, min(MAX_AXIS, remaining_y))
        step_wheel = max(-MAX_AXIS, min(MAX_AXIS, remaining_wheel))
        chunks.append((step_x, step_y, step_wheel))
        remaining_x -= step_x
        remaining_y -= step_y
        remaining_wheel -= step_wheel
    return chunks


def keycode(name: str) -> int:
    normalized = str(name).strip().lower()
    code = KEYCODES.get(normalized)
    if code is None:
        raise KisekiError(
            f"no CH9329 keycode for {name!r}",
            code_hint="INVALID_KEY",
            key=name,
            known=sorted(KEYCODES),
        )
    return code


def modifier_value(names: Iterable[str]) -> int:
    value = 0
    for name in names:
        bit = MODIFIER_BITS.get(str(name).strip().lower())
        if bit is None:
            raise KisekiError(
                f"no CH9329 modifier for {name!r}",
                code_hint="INVALID_KEY",
                modifier=name,
                known=sorted(MODIFIER_BITS),
            )
        value |= bit
    return value


def resolver(names: Iterable[str]) -> tuple[int, list[int]]:
    """Split a key list into (modifier byte, usage ids)."""

    modifier = 0
    keys: list[int] = []
    for name in names:
        normalized = str(name).strip().lower()
        bit = MODIFIER_BITS.get(normalized)
        if bit is not None:
            modifier |= bit
        else:
            keys.append(keycode(normalized))
    return modifier, keys


def available() -> bool:
    """True when pyserial is importable (the USB-TTL adapter driver side)."""

    try:
        import serial  # noqa: F401
    except Exception:  # pragma: no cover - depends on the host
        return False
    return True


class Ch9329:
    """Thin CH9329 transport; the port is opened lazily by ``open()``."""

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 115200,
        *,
        serial_factory: Any = None,
        report_gap_s: float = REPORT_GAP_S,
    ) -> None:
        self.port = str(port)
        self.baudrate = int(baudrate)
        self._serial_factory = serial_factory
        self._serial: Any = None
        self.report_gap_s = float(report_gap_s)

    def open(self) -> "Ch9329":
        if self._serial is not None:
            return self
        factory = self._serial_factory
        if factory is None:
            try:
                import serial  # type: ignore[import-not-found]
            except Exception as error:  # pragma: no cover - depends on the host
                raise KisekiError(
                    "pyserial is not installed; pip install -e .[hid]",
                    code_hint="HID_UNAVAILABLE",
                    detail=str(error),
                ) from None
            factory = serial.Serial
        self._serial = factory(self.port, self.baudrate, timeout=0.05)
        return self

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def write(self, payload: bytes) -> None:
        if self._serial is None:
            self.open()
        self._serial.write(payload)

    def info(self) -> str:
        """GET_INFO round-trip: bytes echoed by the module, as hex."""

        self.write(frame(CMD_GET_INFO))
        read = getattr(self._serial, "read", None)
        answer = read(32) if callable(read) else b""
        return bytes(answer or b"").hex()

    # -- high level helpers -------------------------------------------------
    def move(self, dx: int, dy: int, wheel: int = 0) -> int:
        """Relative move, chunked to ±127 per frame; returns frames sent."""

        sent = 0
        for step_x, step_y, step_wheel in axis_chunks(dx, dy, wheel):
            self.write(mouse_frame(0, step_x, step_y, step_wheel))
            sent += 1
            time.sleep(self.report_gap_s)
        return sent

    def click(self, button: str = "left", hold_s: float = 0.03) -> int:
        mask = MOUSE_BUTTONS.get(str(button).strip().lower())
        if mask is None:
            raise KisekiError(
                f"unknown mouse button {button!r}",
                code_hint="INVALID_KEY",
                button=button,
                known=sorted(MOUSE_BUTTONS),
            )
        self.write(mouse_frame(mask, 0, 0, 0))
        time.sleep(hold_s)
        self.write(mouse_frame(0, 0, 0, 0))
        return 2

    def press(self, keys: Iterable[str]) -> int:
        """Press (and leave held) a key combination."""

        modifier, usage = resolver(keys)
        self.write(keyboard_frame(modifier, usage))
        return 1

    def release(self) -> int:
        """Release every key — send a few times because CH9329 drops reports."""

        for _ in range(RELEASE_FRAMES):
            self.write(keyboard_frame(0, []))
            time.sleep(self.report_gap_s)
        return RELEASE_FRAMES

    def tap(self, keys: Iterable[str], hold_s: float = 0.02) -> int:
        modifier, usage = resolver(keys)
        self.write(keyboard_frame(modifier, usage))
        time.sleep(hold_s)
        self.write(keyboard_frame(0, []))
        return 2
