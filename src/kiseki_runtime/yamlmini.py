"""A very small YAML subset parser (zero third-party dependencies).

KISEKI game profiles are written by hand, so they only use a modest slice of
YAML: nested mappings, sequences, inline ``[...]`` / ``{...}`` literals,
quoted strings, numbers, booleans, null and ``|``/``>`` block scalars (folded
or literal, with optional ``+``/``-`` chomping).  Supporting exactly that slice
keeps ``kiseki_runtime`` runnable on a bare Python install (no PyYAML).

Deliberately unsupported: anchors/aliases, multi-document streams, tags, merge
keys and tabs for indentation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import YamlError

_BLOCK_HEAD = re.compile(r"^(?P<header>.*?)(?P<style>[|>])(?P<chomp>[+-]?)$")


def _strip_comment(line: str) -> str:
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split on ``sep`` while ignoring separators inside quotes or brackets."""

    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_key_value(text: str) -> tuple[str, str]:
    quote = ""
    depth = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            return text[:i].strip(), text[i + 1 :].strip()
    raise YamlError(f"expected 'key: value', got: {text!r}")


def _unquote(text: str) -> str:
    body = text[1:-1]
    if text[0] == '"':
        return (
            body.replace("\\\\", "\\")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\t", "\t")
        )
    return body.replace("''", "'")


def parse_scalar(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part) for part in _split_top(inner)]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        if not inner:
            return {}
        result: dict[str, Any] = {}
        for part in _split_top(inner):
            key, value = _split_key_value(part)
            result[str(parse_scalar(key))] = parse_scalar(value)
        return result
    if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
        return _unquote(text)
    low = text.lower()
    if low in ("null", "~"):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        if any(ch in text for ch in ".eE") and text.lower() not in ("nan", "inf"):
            return float(text)
        return int(text, 10)
    except ValueError:
        return text


def _looks_like_mapping(text: str) -> bool:
    if not text or text[0] in "\"'[{":
        return False
    try:
        _split_key_value(text)
    except YamlError:
        return False
    return True


def _parse_map(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while index < len(lines) and lines[index][0] == indent:
        content = lines[index][1]
        if content.startswith("- "):
            break
        key, value = _split_key_value(content)
        index += 1
        if value:
            out[str(parse_scalar(key))] = parse_scalar(value)
        elif index < len(lines) and lines[index][0] > indent:
            child, index = _parse_block(lines, index, lines[index][0])
            out[str(parse_scalar(key))] = child
        else:
            out[str(parse_scalar(key))] = None
    return out, index


def _parse_seq(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
        rest = lines[index][1][2:].strip()
        index += 1
        if rest and _looks_like_mapping(rest):
            item: dict[str, Any] = {}
            key, value = _split_key_value(rest)
            if value:
                item[str(parse_scalar(key))] = parse_scalar(value)
            elif index < len(lines) and lines[index][0] > indent:
                child, index = _parse_block(lines, index, lines[index][0])
                item[str(parse_scalar(key))] = child
            else:
                item[str(parse_scalar(key))] = None
            if index < len(lines) and lines[index][0] > indent:
                extra, index = _parse_block(lines, index, lines[index][0])
                if not isinstance(extra, dict):
                    raise YamlError("expected a mapping to continue a sequence item")
                item.update(extra)
            out.append(item)
        else:
            out.append(parse_scalar(rest) if rest else None)
    return out, index


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if lines[index][0] != indent:
        raise YamlError(f"unexpected indentation at {lines[index]!r}")
    if lines[index][1].startswith("- "):
        return _parse_seq(lines, index, indent)
    return _parse_map(lines, index, indent)


def _block_header(content: str) -> tuple[str, str, str] | None:
    """Split ``notes: >-`` into ``("notes:", ">", "-")``; None when not a header."""

    match = _BLOCK_HEAD.match(content)
    if match is None:
        return None
    header = match.group("header").strip()
    if not (header == "" or header == "-" or header.endswith(":")):
        return None
    return header, match.group("style"), match.group("chomp")


def _collect_block(raw_lines: list[str], start: int, parent_indent: int) -> tuple[list[str], int]:
    collected: list[str] = []
    block_indent: int | None = None
    index = start
    while index < len(raw_lines):
        raw = raw_lines[index]
        if not raw.strip():
            collected.append("")
            index += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= parent_indent:
            break
        if block_indent is None:
            block_indent = indent
        if indent < block_indent:
            break
        collected.append(raw[block_indent:] if block_indent else raw.lstrip())
        index += 1
    return collected, index


def _render_block(collected: list[str], style: str, chomp: str) -> str:
    if style == "|":
        text = "\n".join(collected)
    else:  # folded
        parts: list[str] = []
        for line in collected:
            if not line:
                parts.append("\n")
            elif parts and parts[-1] not in ("\n",):
                parts.append(" " + line)
            else:
                parts.append(line)
        text = "".join(parts)
    text = text.rstrip("\n")
    if chomp == "-":
        return text
    if chomp == "+":
        return text + "\n"
    return text + "\n"


def loads(text: str) -> Any:
    """Parse a YAML document in the supported subset."""

    raw_lines = text.splitlines()
    lines: list[tuple[int, str]] = []
    index = 0
    while index < len(raw_lines):
        raw = raw_lines[index]
        line = _strip_comment(raw)
        if not line.strip():
            index += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent]:
            raise YamlError("tabs are not supported for indentation")
        content = line.strip()
        header = _block_header(content)
        if header is not None:
            name, style, chomp = header
            collected, next_index = _collect_block(raw_lines, index + 1, indent)
            value = _render_block(collected, style, chomp)
            lines.append((indent, f"{name} {json.dumps(value, ensure_ascii=False)}"))
            index = next_index
            continue
        lines.append((indent, content))
        index += 1
    if not lines:
        return None
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise YamlError(f"unexpected trailing content: {lines[index][1]!r}")
    return value


def load_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return loads(handle.read())
