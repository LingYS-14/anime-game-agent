"""Offline tests for the CH9329 HID transport (no hardware needed)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kiseki_runtime import hid_serial  # noqa: E402
from kiseki_runtime.errors import KisekiError  # noqa: E402


class FakeSerial:
    def __init__(self, port: str, baudrate: int, timeout: float | None = None) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.written: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> int:
        self.written.append(bytes(payload))
        return len(payload)

    def read(self, size: int) -> bytes:
        return bytes.fromhex("57ab0001")

    def close(self) -> None:
        self.closed = True


class FrameTests(unittest.TestCase):
    def test_get_info_frame_matches_known_bytes(self) -> None:
        self.assertEqual(hid_serial.frame(hid_serial.CMD_GET_INFO), bytes.fromhex("57AB00010003"))

    def test_checksum_is_low_byte_of_sum(self) -> None:
        body = bytes([0x57, 0xAB, 0x00, 0x04, 0x05, 0x01])
        self.assertEqual(hid_serial.checksum(body), sum(body) & 0xFF)

    def test_keyboard_frame_layout(self) -> None:
        payload = hid_serial.keyboard_frame(0x02, [0x04, 0x05])
        self.assertEqual(payload[:5], bytes([0x57, 0xAB, 0x00, 0x02, 0x08]))
        self.assertEqual(payload[5], 0x02)  # modifier
        self.assertEqual(payload[6], 0x00)  # reserved
        self.assertEqual(payload[7:9], bytes([0x04, 0x05]))
        self.assertEqual(payload[9:13], bytes(4))
        self.assertEqual(payload[-1], sum(payload[:-1]) & 0xFF)

    def test_keyboard_frame_keeps_six_slots(self) -> None:
        payload = hid_serial.keyboard_frame(0, [0x04] * 9)
        self.assertEqual(len(payload), 14)  # 5 header + 8 data + checksum
        self.assertEqual(payload[7:13], bytes([0x04] * 6))

    def test_mouse_frame_clamps_axes(self) -> None:
        payload = hid_serial.mouse_frame(0x01, 300, -300, 5)
        self.assertEqual(payload[:5], bytes([0x57, 0xAB, 0x00, 0x04, 0x05]))
        self.assertEqual(payload[5], 0x01)  # report prefix
        self.assertEqual(payload[6], 0x01)  # left button
        self.assertEqual(payload[7], 0x7F)  # +127
        self.assertEqual(payload[8], 0x81)  # -127 as unsigned byte
        self.assertEqual(payload[9], 0x05)
        self.assertEqual(payload[-1], sum(payload[:-1]) & 0xFF)

    def test_axis_chunks_split_long_moves(self) -> None:
        self.assertEqual(hid_serial.axis_chunks(0, 0), [])
        self.assertEqual(hid_serial.axis_chunks(200, -300), [(127, -127, 0), (73, -127, 0), (0, -46, 0)])

    def test_keycodes_and_modifiers(self) -> None:
        self.assertEqual(hid_serial.keycode("A"), 0x04)
        self.assertEqual(hid_serial.keycode("esc"), 0x29)
        self.assertEqual(hid_serial.modifier_value(["ctrl", "shift"]), 0x03)
        self.assertEqual(hid_serial.resolver(["ctrl", "a", "shift"]), (0x03, [0x04]))
        with self.assertRaises(KisekiError) as caught:
            hid_serial.keycode("does-not-exist")
        self.assertEqual(caught.exception.details.get("code_hint"), "INVALID_KEY")
        with self.assertRaises(KisekiError) as caught:
            hid_serial.modifier_value(["hyper"])
        self.assertEqual(caught.exception.details.get("code_hint"), "INVALID_KEY")

    def test_invalid_frame_arguments(self) -> None:
        with self.assertRaises(KisekiError):
            hid_serial.frame(0x1FF)
        with self.assertRaises(KisekiError):
            hid_serial.frame(0x02, b"x" * 300)


class TransportTests(unittest.TestCase):
    def make(self) -> tuple[hid_serial.Ch9329, FakeSerial]:
        transport = hid_serial.Ch9329(
            "COM9", serial_factory=lambda port, baudrate, timeout=None: FakeSerial(port, baudrate, timeout)
        )
        transport.report_gap_s = 0.0
        transport.open()
        return transport, transport._serial  # type: ignore[return-value]

    def test_move_chunks_and_sleeps(self) -> None:
        transport, fake = self.make()
        sent = transport.move(300, -200)
        self.assertEqual(sent, 3)  # x: 127,127,46; y: -127,-73 ride the first two frames
        self.assertEqual(len(fake.written), 3)
        self.assertTrue(all(frame[0:3] == b"\x57\xab\x00" for frame in fake.written))

    def test_click_press_release(self) -> None:
        transport, fake = self.make()
        self.assertEqual(transport.click("left"), 2)
        self.assertEqual(transport.press(["ctrl", "a"]), 1)
        self.assertEqual(transport.release(), hid_serial.RELEASE_FRAMES)
        self.assertEqual(len(fake.written), 2 + 1 + hid_serial.RELEASE_FRAMES)
        self.assertEqual(fake.written[2][5], 0x01)  # ctrl modifier
        self.assertEqual(fake.written[2][7], 0x04)  # 'a'
        for release_frame in fake.written[3:]:
            self.assertEqual(release_frame[5:13], bytes(8))

    def test_tap_two_frames(self) -> None:
        transport, fake = self.make()
        self.assertEqual(transport.tap(["esc"]), 2)
        self.assertEqual(len(fake.written), 2)
        self.assertEqual(fake.written[0][7], 0x29)
        self.assertEqual(fake.written[1][5:13], bytes(8))

    def test_close(self) -> None:
        transport, fake = self.make()
        transport.close()
        self.assertTrue(fake.closed)
        self.assertIsNone(transport._serial)

    def test_info_reads_reply(self) -> None:
        transport, _ = self.make()
        transport.open()
        self.assertEqual(transport.info(), "57ab0001")

    def test_unknown_button(self) -> None:
        transport, _ = self.make()
        with self.assertRaises(KisekiError) as caught:
            transport.click("pedal")
        self.assertEqual(caught.exception.details.get("code_hint"), "INVALID_KEY")


class AvailabilityTests(unittest.TestCase):
    def test_available_is_boolean(self) -> None:
        self.assertIsInstance(hid_serial.available(), bool)


if __name__ == "__main__":
    unittest.main()
