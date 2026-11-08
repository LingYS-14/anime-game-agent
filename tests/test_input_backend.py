"""Offline tests for input backends (no real HID hardware needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kiseki_runtime import hid_serial, input_backend  # noqa: E402
from kiseki_runtime.errors import InputError, KisekiError  # noqa: E402


class FakeTransport:
    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.frames.append(bytes(payload))
        return len(payload)

    def close(self) -> None:
        self.closed = True


class Cursor:
    def __init__(self, *positions: tuple[int, int]) -> None:
        self.positions = list(positions)
        self.index = 0

    def __call__(self) -> tuple[int, int]:
        position = self.positions[min(self.index, len(self.positions) - 1)]
        self.index += 1
        return position


def backend_with_cursor(*positions: tuple[int, int], tolerance_px: int = 2):
    transport = FakeTransport()
    backend = input_backend.HidSerialBackend(
        transport=transport,
        cursor_getter=Cursor(*positions),
        tolerance_px=tolerance_px,
        report_gap_s=0.0,
    )
    return backend, transport


class FactoryTests(unittest.TestCase):
    def test_default_is_sendinput(self) -> None:
        self.assertIsInstance(input_backend.from_profile({}), input_backend.SendInputBackend)
        self.assertIsInstance(
            input_backend.from_profile({"input": {"backend": "sendinput"}}),
            input_backend.SendInputBackend,
        )

    def test_hid_serial_reads_port(self) -> None:
        created: dict[str, object] = {}

        class FakeCh9329:
            def __init__(self, port: str, *, baudrate: int = 115200, report_gap_s: float = 0.01) -> None:
                created["port"] = port
                created["baudrate"] = baudrate

            def open(self) -> None:
                created["opened"] = True

        with mock.patch.object(hid_serial, "Ch9329", FakeCh9329):
            backend = input_backend.from_profile(
                {"input": {"backend": "hid-serial", "serial": {"port": "COM7", "baudrate": 9600}}}
            )
        self.assertIsInstance(backend, input_backend.HidSerialBackend)
        self.assertEqual(created["port"], "COM7")
        self.assertEqual(created["baudrate"], 9600)
        self.assertTrue(created["opened"])

    def test_unknown_backend(self) -> None:
        with self.assertRaises(KisekiError) as caught:
            input_backend.from_profile({"input": {"backend": "magic"}})
        self.assertEqual(caught.exception.details.get("code_hint"), "UNKNOWN_INPUT_BACKEND")


class HidKeyboardTests(unittest.TestCase):
    def test_press_combo_holds_and_releases(self) -> None:
        backend, transport = backend_with_cursor()
        backend.press_combo(["ctrl", "a"], hold_ms=0)
        self.assertEqual(len(transport.frames), 4)
        self.assertEqual(transport.frames[0][5], 0x01)  # ctrl modifier
        self.assertEqual(transport.frames[0][7:13], bytes(6))  # no usage yet
        self.assertEqual(transport.frames[1][5], 0x01)
        self.assertEqual(transport.frames[1][7], 0x04)  # 'a'
        self.assertEqual(transport.frames[2][5], 0x01)  # ctrl still held
        self.assertEqual(transport.frames[3][5:13], bytes(8))  # all released

    def test_double_key_down_is_idempotent(self) -> None:
        backend, transport = backend_with_cursor()
        backend.key_down("shift")
        backend.key_down("shift")
        self.assertEqual(len(transport.frames), 1)

    def test_unknown_key_fails_before_hardware(self) -> None:
        backend, transport = backend_with_cursor()
        with self.assertRaises(KisekiError):
            backend.key_down("hyper-key")
        self.assertEqual(transport.frames, [])


class HidMouseTests(unittest.TestCase):
    def test_move_to_uses_cursor_feedback(self) -> None:
        backend, transport = backend_with_cursor((0, 0), (100, 80))
        backend.mouse_move_to(100, 80)
        self.assertEqual(len(transport.frames), 1)
        payload = transport.frames[0]
        self.assertEqual(payload[0:3], b"\x57\xab\x00")
        self.assertEqual(payload[7], 100)
        self.assertEqual(payload[8], 80)

    def test_move_to_gives_up(self) -> None:
        backend, _ = backend_with_cursor((0, 0), tolerance_px=1)
        with self.assertRaises(InputError) as caught:
            backend.mouse_move_to(50, 50)
        self.assertEqual(caught.exception.details.get("code_hint"), "HID_MOVE_TIMEOUT")

    def test_click_presses_and_releases_button(self) -> None:
        backend, transport = backend_with_cursor((40, 30))
        backend.click(40, 30, button="left", hold_ms=0)
        self.assertEqual(len(transport.frames), 2)
        self.assertEqual(transport.frames[0][6], 0x01)  # left down
        self.assertEqual(transport.frames[1][6], 0x00)  # released

    def test_click_unknown_button(self) -> None:
        backend, _ = backend_with_cursor((0, 0))
        with self.assertRaises(KisekiError) as caught:
            backend.click(0, 0, button="pedal")
        self.assertEqual(caught.exception.details.get("code_hint"), "INVALID_KEY")

    def test_release_all_clears_keys_and_buttons(self) -> None:
        backend, transport = backend_with_cursor()
        backend.key_down("ctrl")
        backend.release_all()
        self.assertEqual(len(transport.frames), 3)  # key_down + keyboard release + mouse release
        self.assertEqual(transport.frames[-2][5:13], bytes(8))
        self.assertEqual(transport.frames[-1][6], 0x00)

    def test_close_delegates(self) -> None:
        backend, transport = backend_with_cursor()
        backend.close()
        self.assertTrue(transport.closed)


class ExecutorRoutingTests(unittest.TestCase):
    def test_execute_actions_uses_the_backend(self) -> None:
        from kiseki_runtime import actions

        class Recorder:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def click(self, x, y, **kwargs):
                self.calls.append(("click", x, y, kwargs.get("button", "left")))

            def key_down(self, name):
                self.calls.append(("key_down", name))

            def key_up(self, name):
                self.calls.append(("key_up", name))

            def press_combo(self, keys, **kwargs):
                self.calls.append(("press_combo", tuple(keys)))

            def mouse_move_by(self, dx, dy, **kwargs):
                self.calls.append(("move_by", dx, dy))

            def mouse_move_to(self, x, y):
                self.calls.append(("move_to", x, y))

            def release_all(self):
                self.calls.append(("release",))

        recorder = Recorder()
        planned = [
            {"kind": "click", "screen": [10, 20], "button": "left", "clicks": 1, "hold_ms": 60},
            {"kind": "key", "keys": ["esc"], "action": "tap", "hold_ms": 40},
            {"kind": "move", "dx": 5, "dy": -3, "relative": True, "duration_ms": 0},
            {"kind": "noop"},
        ]
        actions.execute_actions(planned, hwnd=1, backend=recorder, focus_check=False)
        self.assertEqual(
            recorder.calls,
            [
                ("click", 10, 20, "left"),
                ("press_combo", ("esc",)),
                ("move_by", 5, -3),
            ],
        )


if __name__ == "__main__":
    unittest.main()
