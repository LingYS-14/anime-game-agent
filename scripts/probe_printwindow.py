"""Probe: can PrintWindow(PW_RENDERFULLCONTENT) capture a fullscreen D3D game?

Usage: python scripts/probe_printwindow.py <hwnd> <out.png>
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kiseki_runtime.win32 import capture  # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def grab(hwnd: int, out: Path) -> dict:
    info = capture.window_info(hwnd)
    width, height = info["width"], info["height"]
    window_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bmp = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bmp)
    ok = user32.PrintWindow(hwnd, mem_dc, 0x00000002)  # PW_RENDERFULLCONTENT
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = width
    bi.bmiHeader.biHeight = -height  # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    copied = gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem_dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, window_dc)
    raw = buf.raw
    rgb = bytearray(width * height * 3)
    for i in range(0, len(raw), 4):
        b, g, r = raw[i], raw[i + 1], raw[i + 2]
        j = (i // 4) * 3
        rgb[j] = r
        rgb[j + 1] = g
        rgb[j + 2] = b
    data = capture.png_bytes(width, height, bytes(rgb))
    out.write_bytes(data)
    return {
        "hwnd": hwnd,
        "size": [width, height],
        "printwindow_ok": bool(ok),
        "getdibits": copied,
        "bytes": len(data),
        "out": str(out),
    }


if __name__ == "__main__":
    print(grab(int(sys.argv[1]), Path(sys.argv[2])))
