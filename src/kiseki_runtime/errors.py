"""Typed errors shared by the KISEKI runtime tools.

Every error carries a stable machine-readable ``code`` so that the MCP tool
output can be audited instead of parsed from free text.
"""

from __future__ import annotations

from typing import Any


class KisekiError(Exception):
    """Base error with a stable code and optional structured details."""

    code = "KISEKI_ERROR"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False,
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class YamlError(KisekiError, ValueError):
    code = "YAML_ERROR"


class ProfileError(KisekiError):
    code = "PROFILE_ERROR"


class PolicyError(KisekiError):
    """A request was well-formed but rejected by the profile safety rules."""

    code = "POLICY_ERROR"


class WindowError(KisekiError):
    code = "WINDOW_ERROR"


class CaptureError(KisekiError):
    code = "CAPTURE_ERROR"


class InputError(KisekiError):
    code = "INPUT_ERROR"


class EmergencyStopError(KisekiError):
    code = "EMERGENCY_STOP"


class NotSupportedError(KisekiError):
    code = "NOT_IMPLEMENTED"
