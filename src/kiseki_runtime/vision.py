"""Template matching for the KISEKI runtime (M1.0).

OpenCV is an *optional* dependency: the runtime still installs and runs without
it (everything that only needs coordinates keeps working).  Call
:func:`available` before using, or catch the ``VISION_UNAVAILABLE`` error the
tool surface raises.

Why grayscale + ``TM_CCOEFF_NORMED``: it is fast on full-screen 2560x1600
frames, tolerant to small brightness shifts, and needs no model files.  It does
not handle scale or rotation changes; game UI is fixed-size, so that is fine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from .errors import KisekiError

try:  # pragma: no cover - exercised only when opencv is installed
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


def available() -> bool:
    """True when OpenCV can be imported in this interpreter."""

    return cv2 is not None


def require() -> None:
    """Raise a clear, actionable error when OpenCV is missing."""

    if not available():
        raise KisekiError(
            "template matching needs OpenCV; install it with "
            "`python -m pip install opencv-python-headless` (or `pip install -e .[vision]`)",
            code_hint="VISION_UNAVAILABLE",
            dependency="opencv-python-headless",
        )


def _region_pixels(
    region: Sequence[float], width: int, height: int
) -> tuple[int, int, int, int]:
    try:
        x0, y0, x1, y1 = (float(value) for value in region)
    except (TypeError, ValueError):
        raise KisekiError(
            "region must be four numbers [x0, y0, x1, y1] normalized to 0..1",
            code_hint="INVALID_REGION",
            region=list(region),
        ) from None
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        raise KisekiError(
            "region must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1",
            code_hint="INVALID_REGION",
            region=[x0, y0, x1, y1],
        )
    left = max(0, min(width - 1, int(round(x0 * width))))
    top = max(0, min(height - 1, int(round(y0 * height))))
    right = max(left + 1, min(width, int(round(x1 * width))))
    bottom = max(top + 1, min(height, int(round(y1 * height))))
    return left, top, right, bottom


def match(
    screenshot: str | Path,
    template: str | Path,
    threshold: float = 0.9,
    region: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Find ``template`` inside ``screenshot``.

    Returns ``{"found": bool, "score": float, "rect": [x, y, w, h] | None}``
    where ``rect`` uses screenshot pixels (region offset already applied).
    """

    require()
    # Local aliases keep type checkers quiet about the optional imports.
    cv = cv2
    npmod = np

    haystack = cv.imread(str(screenshot), cv.IMREAD_GRAYSCALE)
    if haystack is None:
        raise KisekiError(
            "could not read the screenshot",
            code_hint="CAPTURE_UNREADABLE",
            path=str(screenshot),
        )
    return _match_gray(haystack, template, threshold=threshold, region=region)


def match_rgb(
    rgb: bytes,
    width: int,
    height: int,
    template: str | Path,
    threshold: float = 0.9,
    region: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Like :func:`match`, but on an in-memory RGB buffer (no PNG round-trip).

    ``game_locate`` uses this path: capturing to memory and matching directly is
    roughly 10x faster than writing/reading a full-screen PNG.
    """

    require()
    cv = cv2
    npmod = np

    expected = width * height * 3
    if len(rgb) != expected:
        raise KisekiError(
            "raw frame buffer does not match its declared size",
            code_hint="CAPTURE_UNREADABLE",
            expected=expected,
            actual=len(rgb),
            size=[width, height],
        )
    frame = npmod.frombuffer(rgb, dtype=npmod.uint8).reshape((height, width, 3))
    gray = cv.cvtColor(frame, cv.COLOR_RGB2GRAY)
    return _match_gray(gray, template, threshold=threshold, region=region)


def _match_gray(
    haystack: Any,
    template: str | Path,
    threshold: float = 0.9,
    region: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Shared matching core; ``haystack`` is a grayscale ndarray."""

    cv = cv2

    needle = cv.imread(str(template), cv.IMREAD_GRAYSCALE)
    if needle is None:
        raise KisekiError(
            "could not read the template image",
            code_hint="TEMPLATE_UNREADABLE",
            path=str(template),
        )

    height, width = haystack.shape[:2]
    offset_x = offset_y = 0
    if region is not None:
        left, top, right, bottom = _region_pixels(region, width, height)
        haystack = haystack[top:bottom, left:right]
        offset_x, offset_y = left, top

    if needle.shape[0] > haystack.shape[0] or needle.shape[1] > haystack.shape[1]:
        raise KisekiError(
            "template is larger than the search area",
            code_hint="TEMPLATE_TOO_LARGE",
            template=[int(needle.shape[1]), int(needle.shape[0])],
            search=[int(haystack.shape[1]), int(haystack.shape[0])],
        )

    result = cv.matchTemplate(haystack, needle, cv.TM_CCOEFF_NORMED)
    _min_value, max_value, _min_loc, max_loc = cv.minMaxLoc(result)
    score = float(max_value)
    if score < float(threshold):
        return {"found": False, "score": score, "rect": None}

    x = int(max_loc[0]) + offset_x
    y = int(max_loc[1]) + offset_y
    return {
        "found": True,
        "score": score,
        "rect": [x, y, int(needle.shape[1]), int(needle.shape[0])],
    }


def normalized_rect(
    rect: Sequence[int], width: int, height: int
) -> list[float]:
    """Convert a pixel rect to ``[x0, y0, x1, y1]`` normalized to 0..1."""

    x, y, w, h = (int(value) for value in rect)
    return [
        round(x / width, 6),
        round(y / height, 6),
        round((x + w) / width, 6),
        round((y + h) / height, 6),
    ]


def normalized_center(rect: Sequence[int], width: int, height: int) -> list[float]:
    """Normalized center point of a pixel rect (what ``game_act`` wants)."""

    x, y, w, h = (int(value) for value in rect)
    return [round((x + w / 2) / width, 6), round((y + h / 2) / height, 6)]


def normalize_threshold(threshold: Any) -> float:
    """Validate a match threshold in (0.0, 1.0]."""

    if threshold is None:
        return 0.9
    try:
        value = float(threshold)
    except (TypeError, ValueError):
        raise KisekiError(
            "threshold must be a number between 0 and 1",
            code_hint="INVALID_THRESHOLD",
            threshold=str(threshold),
        ) from None
    if not 0.0 < value <= 1.0:
        raise KisekiError(
            "threshold must be in (0, 1]",
            code_hint="INVALID_THRESHOLD",
            threshold=value,
        )
    return value


def template_size(path: str | Path) -> tuple[int, int] | None:
    """(width, height) of a template image, or None when it cannot be read."""

    if not available():
        return None
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return int(image.shape[1]), int(image.shape[0])


__all__ = [
    "available",
    "require",
    "match",
    "match_rgb",
    "normalized_center",
    "normalized_rect",
    "normalize_threshold",
    "template_size",
]
