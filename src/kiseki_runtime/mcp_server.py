"""KISEKI runtime MCP server (stdio).

Run with ``python -m kiseki_runtime.mcp_server``.  The server speaks
newline-delimited JSON-RPC 2.0 and also accepts the ``Content-Length`` framing
used by some MCP clients.  Nothing but protocol messages is written to stdout;
diagnostics go to stderr.

``--self-check`` runs the same checks without starting the server, which is what
``docs/04-DSH集成与运行时方案.md`` asks for as the week-1 acceptance step.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, Iterable

from . import __version__
from .errors import KisekiError
from .tools import TOOL_NAMES, VERSION, Runtime

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "kiseki-runtime"

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "game_profiles",
        "description": "List the Game Profiles installed in configs/ (id, window rule, anchors, dry-run default).",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "game_focus",
        "description": "Find the game window by its profile rule and bring it to the foreground. Required before capture/act.",
        "inputSchema": {
            "type": "object",
            "properties": {"game_id": {"type": "string", "description": "Game Profile id"}},
            "required": ["game_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_capture",
        "description": "Capture the focused game window (optionally a normalized region) as a PNG under captures/.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "region": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "[x0, y0, x1, y1] normalized to 0..1",
                },
                "label": {"type": "string", "description": "short label used in the file name"},
                "scale": {
                    "type": "number",
                    "minimum": 0.25,
                    "maximum": 1.0,
                    "description": "optional downscale factor; 1.0 keeps the native pixel size",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "game_state",
        "description": "Return the focused window geometry, foreground flag, dry-run default and emergency-stop status.",
        "inputSchema": {
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "game_act",
        "description": (
            "Execute validated actions (click/key/move/wait/noop). Targets must be profile anchors or "
            "normalized 0..1 coordinates; absolute pixels and scripts are rejected. Foreground is "
            "checked before every input event."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {"type": "object"},
                },
                "dry_run": {"type": "boolean", "description": "override the profile default"},
            },
            "required": ["game_id", "actions"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_skill",
        "description": "Run a deterministic skill declared by the Game Profile (a fixed action list, no perception).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game_id": {"type": "string"},
                "skill": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["game_id", "skill"],
            "additionalProperties": False,
        },
    },
    {
        "name": "game_status",
        "description": "Runtime status: loaded profiles, focused games, capture/action counters, emergency stop.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "game_map",
        "description": "Reserved for M2 map understanding; currently returns NOT_IMPLEMENTED.",
        "inputSchema": {
            "type": "object",
            "properties": {"game_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "game_stop",
        "description": "Release every held key/button, reset the emergency stop and forget focused windows.",
        "inputSchema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "additionalProperties": False,
        },
    },
]

TOOL_INDEX = {schema["name"]: schema for schema in TOOL_SCHEMAS}


def _log(message: str) -> None:
    print(f"[kiseki-runtime] {message}", file=sys.stderr, flush=True)


class Server:
    def __init__(self, runtime: Runtime | None = None) -> None:
        self.runtime = runtime or Runtime()

    # -- protocol ----------------------------------------------------------
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if request_id is None:  # notification: no response
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": VERSION,
                        "title": "KISEKI game runtime",
                    },
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": TOOL_SCHEMAS},
            }
        if method == "tools/call":
            return self._call_tool(request_id, message.get("params") or {})
        if method == "shutdown":
            return {"jsonrpc": "2.0", "id": request_id, "result": None}
        return self._error(request_id, -32601, f"method not found: {method!r}")

    def _call_tool(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOL_INDEX:
            return self._error(request_id, -32602, f"unknown tool: {name!r}", {"known": TOOL_NAMES})
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "arguments must be an object")
        try:
            result = self._dispatch(str(name), arguments)
        except KisekiError as error:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"ok": False, "error": error.to_dict()},
                                ensure_ascii=False,
                            ),
                        }
                    ],
                },
            }
        except Exception as error:  # noqa: BLE001 - report instead of dying
            _log("".join(traceback.format_exception(error)))
            return self._error(
                request_id,
                -32603,
                f"{type(error).__name__}: {error}",
            )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": False,
                "content": [
                    {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                ],
            },
        }

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        runtime = self.runtime
        if name == "game_profiles":
            return runtime.game_profiles()
        if name == "game_focus":
            return runtime.game_focus(str(arguments["game_id"]))
        if name == "game_capture":
            return runtime.game_capture(
                game_id=arguments.get("game_id"),
                region=arguments.get("region"),
                label=arguments.get("label"),
                scale=arguments.get("scale", 1.0),
            )
        if name == "game_state":
            return runtime.game_state(arguments.get("game_id"))
        if name == "game_act":
            return runtime.game_act(
                str(arguments["game_id"]),
                arguments["actions"],
                dry_run=arguments.get("dry_run"),
            )
        if name == "game_skill":
            return runtime.game_skill(
                str(arguments["game_id"]), str(arguments["skill"]), dry_run=arguments.get("dry_run")
            )
        if name == "game_status":
            return runtime.game_status()
        if name == "game_map":
            return runtime.game_map(arguments.get("game_id"))
        if name == "game_stop":
            return runtime.game_stop(arguments.get("reason"))
        raise KisekiError(f"unhandled tool {name!r}", code_hint="INTERNAL")

    @staticmethod
    def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}


# -- stdio transport ------------------------------------------------------
def iter_messages(stream: Any) -> Iterable[bytes]:
    """Yield raw JSON payloads, accepting both MCP framing styles."""

    while True:
        line = stream.readline()
        if not line:
            return
        if not line.strip():
            continue
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                header = stream.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
            body = stream.read(length)
            if body:
                yield body
        else:
            yield line


def write_message(stream: Any, payload: bytes, framed: bool) -> None:
    if framed:
        stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
    else:
        stream.write(payload + b"\n")
    stream.flush()


def serve(
    runtime: Runtime | None = None,
    stdin: Any = None,
    stdout: Any = None,
) -> int:
    from io import BytesIO

    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    server = Server(runtime)
    framed = False
    buffer = b""
    while True:
        line = source.readline()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            framed = True
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                header = source.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
            body = source.read(length)
        else:
            body = line
        if not body.strip():
            continue
        try:
            message = json.loads(body)
        except json.JSONDecodeError:
            write_message(
                sink,
                json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
                ).encode("utf-8"),
                framed,
            )
            continue
        try:
            response = server.handle(message)
        except Exception as error:  # noqa: BLE001
            _log("".join(traceback.format_exception(error)))
            response = Server._error(message.get("id"), -32603, f"{type(error).__name__}: {error}")
        if response is None:
            continue
        payload = json.dumps(response, ensure_ascii=False, default=str).encode("utf-8")
        write_message(sink, payload, framed)
        if message.get("method") == "shutdown":
            break
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KISEKI runtime MCP server")
    parser.add_argument("--root", help="repository root (defaults to KISEKI_ROOT or the package root)")
    parser.add_argument("--profiles", help="Game Profiles directory (defaults to <root>/configs)")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="run diagnostics, print JSON and exit (no MCP loop, no input injection)",
    )
    parser.add_argument("--version", action="version", version=f"kiseki-runtime {VERSION}")
    args = parser.parse_args(argv)
    runtime = Runtime(root=args.root, profiles_dir=args.profiles)
    if args.self_check:
        report = runtime.self_check()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0 if report.get("ok") else 1
    _log(f"serving {SERVER_NAME} {VERSION} (profiles: {', '.join(sorted(runtime.profiles)) or 'none'})")
    return serve(runtime)


if __name__ == "__main__":
    raise SystemExit(main())
