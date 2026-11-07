"""Window discovery, geometry and PNG capture for Windows (ctypes only).

No third-party dependency is used: windows are enumerated with ``EnumWindows``,
client pixels are copied with GDI ``BitBlt`` and encoded as PNG with
``zlib`` + ``struct``.  That keeps ``kiseki_runtime`` installable on a bare
Python 3.13 and is enough for the M0 milestone (window focus, capture, click).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import struct
import zlib
from pathlib import Path
from typing import Any, Callable

from ..errors import CaptureError, WindowError

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

SRCCOPY = 0x00CC0020
BI_RGB = 0
DIB_RGB_COLORS = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


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


_ENUM_PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [_ENUM_PROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(POINT)]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
if hasattr(user32, "GetDpiForWindow"):
    user32.GetDpiForWindow.argtypes = [wt.HWND]
    user32.GetDpiForWindow.restype = wt.UINT

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [wt.HDC, ctypes.c_void_p]
gdi32.BitBlt.argtypes = [
    wt.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wt.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wt.DWORD,
]
gdi32.GetDIBits.argtypes = [
    wt.HDC,
    wt.HBITMAP,
    wt.UINT,
    wt.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO),
    wt.UINT,
]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [wt.HDC]

kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPWSTR,
    ctypes.POINTER(wt.DWORD),
]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

_dpi_aware = False


def enable_dpi_awareness() -> bool:
    """Make the process DPI aware once, so window rects are physical pixels."""

    global _dpi_aware
    if _dpi_aware:
        return True
    try:
        # PER_MONITOR_AWARE_V2 = -4; works on Windows 10 1703+.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _dpi_aware = True
            return True
    except AttributeError:
        pass
    try:
        if user32.SetProcessDPIAware():
            _dpi_aware = True
            return True
    except AttributeError:
        pass
    return False


def _window_text(hwnd: wt.HWND) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _class_name(hwnd: wt.HWND) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def process_name(pid: int) -> str | None:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value.rsplit("\\", 1)[-1] or None
    finally:
        kernel32.CloseHandle(handle)


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    """True when the handle still refers to a live window."""
    if not hwnd:
        return False
    return bool(user32.IsWindow(int(hwnd)))


def is_foreground(hwnd: int) -> bool:
    return foreground_window() == int(hwnd)


user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetWindowThreadProcessId.restype = wt.DWORD
kernel32.GetCurrentThreadId.restype = wt.DWORD

SW_RESTORE = 9


def bring_to_foreground(hwnd: int) -> bool:
    """Raise ``hwnd`` to the foreground, working around the focus lock.

    Windows blocks ``SetForegroundWindow`` from a process that does not own the
    current foreground window, so when the direct call fails we attach to the
    foreground thread's input queue for the duration of the raise.
    """

    enable_dpi_awareness()
    handle = wt.HWND(int(hwnd))
    if user32.IsIconic(handle):
        user32.ShowWindow(handle, SW_RESTORE)
    user32.SetForegroundWindow(handle)
    if is_foreground(int(hwnd)):
        return True

    current = int(kernel32.GetCurrentThreadId())
    threads: set[int] = set()
    foreground = foreground_window()
    if foreground:
        threads.add(int(user32.GetWindowThreadProcessId(wt.HWND(foreground), None)))
    threads.add(int(user32.GetWindowThreadProcessId(handle, None)))
    attached: list[int] = []
    for thread in threads:
        if thread and thread != current and user32.AttachThreadInput(current, thread, True):
            attached.append(thread)
    try:
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    finally:
        for thread in attached:
            user32.AttachThreadInput(current, thread, False)
    return is_foreground(int(hwnd))


def get_dpi_for_window(hwnd: int) -> int:
    if hasattr(user32, "GetDpiForWindow"):
        value = user32.GetDpiForWindow(wt.HWND(hwnd))
        if value:
            return int(value)
    return 96


def window_info(hwnd: int) -> dict[str, Any]:
    """Return geometry, process and visibility for one window."""

    handle = wt.HWND(int(hwnd))
    if not handle:
        raise WindowError("invalid hwnd", hwnd=hwnd)
    rect = RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        raise WindowError(
            f"GetWindowRect failed for hwnd {hwnd} (error {ctypes.get_last_error()})",
            hwnd=hwnd,
        )
    client = RECT()
    user32.GetClientRect(handle, ctypes.byref(client))
    origin = POINT(0, 0)
    user32.ClientToScreen(handle, ctypes.byref(origin))
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return {
        "hwnd": int(hwnd),
        "title": _window_text(handle),
        "class_name": _class_name(handle),
        "pid": int(pid.value),
        "process": process_name(int(pid.value)),
        "visible": bool(user32.IsWindowVisible(handle)),
        "minimized": bool(user32.IsIconic(handle)),
        "rect": [rect.left, rect.top, rect.right, rect.bottom],
        # client area as [left, top, width, height] in screen coordinates
        "client": [origin.x, origin.y, client.right, client.bottom],
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
        "dpi": get_dpi_for_window(int(hwnd)),
        "is_foreground": is_foreground(int(hwnd)),
    }


def enum_windows(visible_only: bool = True, resolve_process: bool = False) -> list[dict[str, Any]]:
    """Enumerate top-level windows, cheapest useful fields only."""

    results: list[dict[str, Any]] = []

    def callback(hwnd: wt.HWND, _lparam: int) -> bool:
        # WINFUNCTYPE with HWND (c_void_p) delivers a plain int on some builds.
        handle = int(hwnd) if isinstance(hwnd, int) else int(hwnd.value or 0)
        if visible_only and not user32.IsWindowVisible(handle):
            return True
        rect = RECT()
        if not user32.GetWindowRect(handle, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return True
        client = RECT()
        user32.GetClientRect(handle, ctypes.byref(client))
        origin = POINT(0, 0)
        user32.ClientToScreen(handle, ctypes.byref(origin))
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        info: dict[str, Any] = {
            "hwnd": handle,
            "title": _window_text(handle),
            "class_name": _class_name(hwnd),
            "pid": int(pid.value),
            "visible": True,
            "minimized": bool(user32.IsIconic(hwnd)),
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "client": [origin.x, origin.y, client.right, client.bottom],
            "width": width,
            "height": height,
            "dpi": get_dpi_for_window(handle),
            "is_foreground": is_foreground(handle),
        }
        if resolve_process:
            info["process"] = process_name(info["pid"])
        results.append(info)
        return True

    if not user32.EnumWindows(_ENUM_PROC(callback), 0):
        error = ctypes.get_last_error()
        if error:
            raise WindowError(f"EnumWindows failed (error {error})")
    return results


def find_window(
    process: str | None = None,
    title_regex: str | None = None,
    class_name: str | None = None,
) -> dict[str, Any] | None:
    """Find the first visible window matching the profile rules."""

    pattern = re.compile(title_regex, re.IGNORECASE) if title_regex else None
    want_process = process.lower() if process else None
    for info in enum_windows(visible_only=True, resolve_process=bool(want_process)):
        if want_process and (info.get("process") or "").lower() != want_process:
            continue
        if class_name and info["class_name"] != class_name:
            continue
        if pattern and not pattern.search(info["title"]):
            continue
        return info
    return None


def _bgra_to_rgb(data: bytes, width: int, height: int) -> bytes:
    out = bytearray(width * height * 3)
    source = memoryview(data)
    index = 0
    for offset in range(0, width * height * 4, 4):
        out[index] = source[offset + 2]
        out[index + 1] = source[offset + 1]
        out[index + 2] = source[offset]
        index += 3
    return bytes(out)


def _crop_rgb(
    rgb: bytes, width: int, height: int, region: list[float] | tuple[float, ...]
) -> tuple[bytes, int, int]:
    if len(region) != 4:
        raise CaptureError("region must be [x0, y0, x1, y1] normalized to 0..1")
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(value))) for value in region)
    if x1 <= x0 or y1 <= y0:
        raise CaptureError("region is empty after clamping", region=list(region))
    left = int(round(x0 * width))
    top = int(round(y0 * height))
    right = max(left + 1, int(round(x1 * width)))
    bottom = max(top + 1, int(round(y1 * height)))
    right = min(width, right)
    bottom = min(height, bottom)
    rows = [rgb[(top + row) * width * 3 + left * 3 : (top + row) * width * 3 + right * 3] for row in range(bottom - top)]
    return b"".join(rows), right - left, bottom - top


def _scale_rgb(
    rgb: bytes, width: int, height: int, scale: float
) -> tuple[bytes, int, int]:
    """Downscale RGB pixels with nearest-neighbour sampling (no new dependency)."""

    if scale == 1.0:
        return rgb, width, height
    target_width = max(1, round(width * scale))
    target_height = max(1, round(height * scale))
    out = bytearray(target_width * target_height * 3)
    for target_y in range(target_height):
        source_y = min(height - 1, target_y * height // target_height)
        source_row = source_y * width * 3
        index = target_y * target_width * 3
        for target_x in range(target_width):
            source_x = min(width - 1, target_x * width // target_width)
            offset = source_row + source_x * 3
            out[index] = rgb[offset]
            out[index + 1] = rgb[offset + 1]
            out[index + 2] = rgb[offset + 2]
            index += 3
    return bytes(out), target_width, target_height


def _validated_scale(scale: float) -> float:
    try:
        value = float(scale)
    except (TypeError, ValueError):
        raise CaptureError(
            "scale must be a number between 0.25 and 1.0",
            code_hint="INVALID_SCALE",
            scale=str(scale),
        ) from None
    if not 0.25 <= value <= 1.0:
        raise CaptureError(
            "scale must be between 0.25 and 1.0",
            code_hint="INVALID_SCALE",
            scale=value,
        )
    return value


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """Encode raw RGB pixels as a PNG (color type 2, 8 bit, no interlace)."""

    expected = width * height * 3
    if len(rgb) != expected:
        raise CaptureError(f"rgb buffer has {len(rgb)} bytes, expected {expected}")

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgb[row * width * 3 : (row + 1) * width * 3] for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _blit_client(hdc_source: wt.HDC, width: int, height: int) -> bytes:
    memory_dc = gdi32.CreateCompatibleDC(hdc_source)
    if not memory_dc:
        raise CaptureError(f"CreateCompatibleDC failed (error {ctypes.get_last_error()})")
    bitmap = gdi32.CreateCompatibleBitmap(hdc_source, width, height)
    if not bitmap:
        gdi32.DeleteDC(memory_dc)
        raise CaptureError(f"CreateCompatibleBitmap failed (error {ctypes.get_last_error()})")
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not gdi32.BitBlt(memory_dc, 0, 0, width, height, hdc_source, 0, 0, SRCCOPY):
            raise CaptureError(f"BitBlt failed (error {ctypes.get_last_error()})")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS
        )
        if copied == 0:
            raise CaptureError(f"GetDIBits failed (error {ctypes.get_last_error()})")
        return _bgra_to_rgb(buffer.raw, width, height)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)


def _screen_rgb() -> tuple[int, int, bytes]:
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    if width <= 0 or height <= 0:
        raise CaptureError("could not read the primary screen size")
    hdc = user32.GetDC(None)
    if not hdc:
        raise CaptureError(f"GetDC(desktop) failed (error {ctypes.get_last_error()})")
    try:
        return width, height, _blit_client(hdc, width, height)
    finally:
        user32.ReleaseDC(None, hdc)


def _printwindow_rgb(hwnd: int, width: int, height: int) -> bytes:
    """Ask the window to render itself into a bitmap (works for many D3D windows)."""

    if not hasattr(user32, "PrintWindow"):
        raise CaptureError("PrintWindow is not available on this Windows build")
    user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
    user32.PrintWindow.restype = wt.BOOL
    handle = wt.HWND(int(hwnd))
    window_dc = user32.GetWindowDC(handle)
    if not window_dc:
        raise CaptureError(f"GetWindowDC failed (error {ctypes.get_last_error()})")
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    if not memory_dc or not bitmap:
        user32.ReleaseDC(handle, window_dc)
        raise CaptureError(f"could not allocate the PrintWindow bitmap (error {ctypes.get_last_error()})")
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        rendered = user32.PrintWindow(handle, memory_dc, 0x00000002)  # PW_RENDERFULLCONTENT
        if not rendered:
            raise CaptureError(f"PrintWindow failed (error {ctypes.get_last_error()})", hwnd=int(hwnd))
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(
            memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS
        )
        if copied == 0:
            raise CaptureError(f"GetDIBits failed (error {ctypes.get_last_error()})")
        return _bgra_to_rgb(buffer.raw, width, height)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(handle, window_dc)


def capture_window_png(
    hwnd: int,
    path: str | Path,
    region: list[float] | tuple[float, ...] | None = None,
    prefer_screen: bool | None = None,
    scale: float = 1.0,
) -> dict[str, Any]:
    """Capture the client area of ``hwnd`` (optionally a normalized region).

    Fullscreen D3D games (flip model) do not update the window DC, so a plain
    BitBlt of the window returns a stale frame.  When the window is in the
    foreground we therefore copy the *screen* and crop it to the client area;
    for background windows we ask the window to render itself via PrintWindow.

    ``scale`` (0.25..1.0) shrinks the captured image with nearest-neighbour
    sampling; the returned ``width``/``height`` are the scaled pixel sizes.
    """

    scale = _validated_scale(scale)
    enable_dpi_awareness()
    info = window_info(int(hwnd))
    left, top, width, height = info["client"]
    if width <= 0 or height <= 0:
        raise CaptureError("window has an empty client area", hwnd=hwnd)
    foreground = is_foreground(int(hwnd))
    use_screen = foreground if prefer_screen is None else bool(prefer_screen)
    if use_screen:
        screen_w, screen_h, screen = _screen_rgb()
        if left < 0 or top < 0 or left + width > screen_w or top + height > screen_h:
            raise CaptureError(
                "window client area is outside the primary screen",
                hwnd=int(hwnd),
                client=[left, top, width, height],
                screen=[screen_w, screen_h],
            )
        rows = [
            screen[(top + row) * screen_w * 3 + left * 3 : (top + row) * screen_w * 3 + (left + width) * 3]
            for row in range(height)
        ]
        rgb = b"".join(rows)
        backend = "screen"
    else:
        try:
            rgb = _printwindow_rgb(int(hwnd), width, height)
            backend = "printwindow"
        except CaptureError:
            handle = wt.HWND(int(hwnd))
            hdc = user32.GetDC(handle)
            if not hdc:
                raise CaptureError(f"GetDC failed (error {ctypes.get_last_error()})", hwnd=hwnd)
            try:
                rgb = _blit_client(hdc, width, height)
            finally:
                user32.ReleaseDC(handle, hdc)
            backend = "window-dc"
    if region:
        rgb, width, height = _crop_rgb(rgb, width, height, region)
    rgb, width, height = _scale_rgb(rgb, width, height, scale)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes(width, height, rgb))
    return {
        "path": str(target),
        "width": width,
        "height": height,
        "scale": scale,
        "hwnd": int(hwnd),
        "client": [left, top, width, height],
        "region": list(region) if region else None,
        "backend": backend,
        "foreground": foreground,
    }


def capture_screen_png(
    path: str | Path,
    region: list[float] | tuple[float, ...] | None = None,
) -> dict[str, Any]:
    """Capture the primary desktop (useful for self-check before a game runs)."""

    enable_dpi_awareness()
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    if width <= 0 or height <= 0:
        raise CaptureError("could not read the primary screen size")
    hdc = user32.GetDC(None)
    if not hdc:
        raise CaptureError(f"GetDC(desktop) failed (error {ctypes.get_last_error()})")
    try:
        rgb = _blit_client(hdc, width, height)
    finally:
        user32.ReleaseDC(None, hdc)
    if region:
        rgb, width, height = _crop_rgb(rgb, width, height, region)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png_bytes(width, height, rgb))
    return {
        "path": str(target),
        "width": width,
        "height": height,
        "region": list(region) if region else None,
    }


def desktop_rect() -> dict[str, int]:
    """Virtual desktop bounds, used when mapping normalized click targets."""

    enable_dpi_awareness()
    return {
        "x": int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        "y": int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        "width": int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        "height": int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    }
