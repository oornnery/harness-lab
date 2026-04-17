from __future__ import annotations

import re
from pathlib import Path

from . import tool

_READ_LIMIT = 100 * 1024  # 100 KB


@tool(name="read_file", desc="Read a text file and return its contents.")
def read_file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: {path!r} does not exist"
    if not p.is_file():
        return f"Error: {path!r} is not a file"
    raw = p.read_bytes()
    text = raw[:_READ_LIMIT].decode("utf-8", errors="replace")
    if len(raw) > _READ_LIMIT:
        text += f"\n... [truncated at {_READ_LIMIT // 1024} KB]"
    return text


@tool(name="write_file", desc="Write content to a file, creating parent directories as needed.")
def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content.encode())} bytes to {path}"


@tool(name="list_dir", desc="List files in a directory matching an optional glob pattern.")
def list_dir(path: str, pattern: str = "*") -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: {path!r} does not exist"
    if not p.is_dir():
        return f"Error: {path!r} is not a directory"
    matches = sorted(str(f) for f in p.glob(pattern))
    if not matches:
        return f"No files matching {pattern!r} in {path}"
    return "\n".join(matches)


@tool(name="grep", desc="Search for a regex pattern in a file and return matching lines.")
def grep(pattern: str, path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        return f"Error: {path!r} does not exist"
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex {pattern!r}: {exc}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    matches = [f"{i + 1}: {line}" for i, line in enumerate(lines) if rx.search(line)]
    if not matches:
        return f"No matches for {pattern!r} in {path}"
    return "\n".join(matches)
