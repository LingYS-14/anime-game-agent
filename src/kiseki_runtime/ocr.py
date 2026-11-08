"""OCR support backed by PaddleOCR (optional dependency).

PaddleOCR is imported lazily: the runtime, the MCP server and every other tool
keep working when it is not installed, and ``game_ocr`` reports
``OCR_UNAVAILABLE`` with an install hint instead of crashing at import time.

Why PaddleOCR: the game UIs this runtime targets use stylised CJK fonts on busy
backgrounds, which is exactly the case where PaddleOCR's det+rec pipeline beats
the built-in Windows OCR.  The trade-off is a heavy dependency, so it lives in
an optional extra (``pip install -e .[ocr]``) and ships its own models.
"""

from __future__ import annotations

import threading
from typing import Any

from .errors import KisekiError

MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

PRESETS_DEFAULT = "accurate"


def available() -> bool:
    """Return True when ``paddleocr`` and ``paddle`` can be imported."""

    try:
        import paddle  # noqa: F401
        import paddleocr  # noqa: F401
    except Exception:
        return False
    return True


def require() -> None:
    """Raise a typed error when PaddleOCR is missing."""

    if available():
        return
    raise KisekiError(
        "PaddleOCR is not installed; install the runtime with the OCR extra",
        code_hint="OCR_UNAVAILABLE",
        hint="pip install -e .[ocr]  (or: pip install paddlepaddle paddleocr)",
    )


def normalize_confidence(confidence: float | None) -> float:
    """Validate the caller's minimum confidence (0..1)."""

    if confidence is None:
        return 0.5
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        raise KisekiError(
            "min_confidence must be a number",
            code_hint="INVALID_CONFIDENCE",
            min_confidence=str(confidence),
        ) from None
    if value < MIN_CONFIDENCE or value > MAX_CONFIDENCE:
        raise KisekiError(
            "min_confidence must be between 0.0 and 1.0",
            code_hint="INVALID_CONFIDENCE",
            min_confidence=value,
            minimum=MIN_CONFIDENCE,
            maximum=MAX_CONFIDENCE,
        )
    return value


# PaddleOCR 3.7 ships PP-OCRv6_medium by default (accurate, ~7 s/frame on this
# CPU) and can fall back to the PP-OCRv5 mobile det+rec pair (~1.5 s/frame).
# "accurate" is the default because the game fonts are stylised; callers that
# poll a HUD repeatedly can opt into "fast".
PRESETS: dict[str, dict[str, str]] = {
    "accurate": {},
    "fast": {
        "text_detection_model_name": "PP-OCRv5_mobile_det",
        "text_recognition_model_name": "PP-OCRv5_mobile_rec",
    },
}

_engines: dict[tuple[str, str], Any] = {}
_engine_lock = threading.Lock()


def normalize_preset(preset: str | None) -> str:
    """Validate the OCR preset name."""

    name = "accurate" if preset is None else str(preset).strip().lower()
    if name not in PRESETS:
        raise KisekiError(
            f"unknown OCR preset {preset!r}",
            code_hint="INVALID_PRESET",
            preset=str(preset),
            supported=sorted(PRESETS),
        )
    return name


def _engine_for(language: str, preset: str):
    """Return a cached PaddleOCR engine for ``(language, preset)``."""

    key = (language, preset)
    with _engine_lock:
        if key in _engines:
            return _engines[key]
        from paddleocr import PaddleOCR  # heavy import, kept out of module load

        engine = PaddleOCR(
            lang=language,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # Paddle 3.3's PIR + oneDNN path raises NotImplementedError on some
            # ops (ConvertPirAttribute2RuntimeAttribute); the plain CPU kernels
            # are slower but stable.
            enable_mkldnn=False,
            **PRESETS[preset],
        )
        _engines[key] = engine
        return engine


def _result_attr(result: Any, name: str) -> Any:
    """Read one field from a PaddleOCR result object across versions."""

    value = getattr(result, name, None)
    if value is not None:
        return value
    if isinstance(result, dict):
        return result.get(name)
    payload = getattr(result, "json", None)
    if isinstance(payload, dict):
        inner = payload.get("res", payload)
        if isinstance(inner, dict):
            return inner.get(name)
    return None


def _polygon_rect(polygon: Any) -> list[int]:
    """Turn a 4-point polygon into an integer ``[x, y, w, h]`` rectangle."""

    points = [tuple(float(value) for value in point[:2]) for point in polygon]
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return [
        int(round(min(xs))),
        int(round(min(ys))),
        int(round(max(xs) - min(xs))),
        int(round(max(ys) - min(ys))),
    ]


def normalize_lines(
    raw: list[dict[str, Any]],
    offset: tuple[int, int],
    client_width: int,
    client_height: int,
) -> list[dict[str, Any]]:
    """Map OCR boxes from a captured region back to normalized client coords.

    ``raw`` entries carry ``text``/``score``/``rect`` in captured-region pixels;
    ``offset`` is where that region starts inside the client area.  The returned
    boxes are client pixels and 0..1 normalized fractions of the client area so
    callers can feed ``normalized_center`` straight into ``game_act``.
    """

    lines: list[dict[str, Any]] = []
    for entry in raw:
        rect = [int(value) for value in entry["rect"]]
        client_rect = [
            rect[0] + offset[0],
            rect[1] + offset[1],
            rect[2],
            rect[3],
        ]
        normalized = [
            client_rect[0] / client_width,
            client_rect[1] / client_height,
            (client_rect[0] + client_rect[2]) / client_width,
            (client_rect[1] + client_rect[3]) / client_height,
        ]
        center = [
            (normalized[0] + normalized[2]) / 2.0,
            (normalized[1] + normalized[3]) / 2.0,
        ]
        lines.append(
            {
                "text": entry["text"],
                "score": round(float(entry["score"]), 4),
                "rect": client_rect,
                "normalized_rect": [round(value, 6) for value in normalized],
                "normalized_center": [round(value, 6) for value in center],
            }
        )
    return lines


def recognize(
    image: Any,
    width: int,
    height: int,
    offset: tuple[int, int],
    client_width: int,
    client_height: int,
    min_confidence: float | None = None,
    language: str = "ch",
    preset: str | None = None,
) -> list[dict[str, Any]]:
    """Run PaddleOCR on an RGB buffer and return normalized text lines."""

    require()
    threshold = normalize_confidence(min_confidence)
    chosen = normalize_preset(preset)
    import numpy as np

    frame = np.frombuffer(image, dtype=np.uint8).reshape((height, width, 3))
    # PaddleOCR follows the OpenCV convention and wants BGR pixels.
    bgr = np.ascontiguousarray(frame[:, :, ::-1])
    engine = _engine_for(language, chosen)
    try:
        results = engine.predict(bgr)
    except Exception as error:  # pragma: no cover - depends on native runtime
        raise KisekiError(
            f"PaddleOCR failed: {error}",
            code_hint="OCR_FAILED",
        ) from error
    raw: list[dict[str, Any]] = []
    for result in results:
        texts = _result_attr(result, "rec_texts") or []
        scores = _result_attr(result, "rec_scores") or []
        polygons = _result_attr(result, "rec_polys")
        if polygons is None:
            polygons = _result_attr(result, "dt_polys") or []
        for text, score, polygon in zip(texts, scores, polygons):
            if not str(text).strip() or float(score) < threshold:
                continue
            raw.append(
                {
                    "text": str(text),
                    "score": float(score),
                    "rect": _polygon_rect(polygon),
                }
            )
    return normalize_lines(raw, offset, client_width, client_height)
