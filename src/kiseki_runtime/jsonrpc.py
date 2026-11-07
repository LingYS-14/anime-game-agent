"""Minimal JSON-RPC 2.0 helpers for the MCP stdio transport.

MCP's stdio transport is newline-delimited JSON-RPC 2.0: one JSON object per
line, UTF-8, no ``Content-Length`` framing.  Keeping this module tiny avoids
pulling the official SDK (and its dependency tree) into the runtime.
"""

from __future__ import annotations

import json
from typing import Any, IO

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """Raised while reading or validating an incoming message."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def read_message(stream: IO[str]) -> dict[str, Any] | None:
    """Read the next JSON-RPC message, or ``None`` on EOF.

    Blank lines are ignored (some clients keep a trailing newline).  Malformed
    JSON raises :class:`JsonRpcError` so the server can answer with an error
    object instead of crashing.
    """

    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JsonRpcError(PARSE_ERROR, "Parse error", {"detail": str(exc)}) from exc
        if not isinstance(message, dict):
            raise JsonRpcError(INVALID_REQUEST, "Invalid Request")
        return message
    return None


def write_message(stream: IO[str], message: dict[str, Any]) -> None:
    """Write one compact JSON-RPC message followed by a newline."""

    stream.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def failure(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
