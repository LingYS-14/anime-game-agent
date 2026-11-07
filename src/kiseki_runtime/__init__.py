"""KISEKI runtime: a local tool surface that lets an agent drive a game window.

The package is intentionally dependency-free (Python 3.11+, ctypes only) and is
exposed to DSH as an MCP server via ``python -m kiseki_runtime.mcp_server``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
