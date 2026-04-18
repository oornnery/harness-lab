from __future__ import annotations

import contextlib
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .tool import tool

__all__ = [
    "MemoryDecision",
    "MemoryEntry",
    "forget",
    "load_medium_memory",
    "memory_clear",
    "memory_list",
    "recall",
    "remember",
    "remember_impl",
]

_HOME = Path.cwd() / ".tooled"
_MED_FILE = _HOME / "memory.md"
_LONG_FILE = _HOME / "memory_long.jsonl"


# --- Pydantic models ---


class MemoryDecision(BaseModel):
    save: bool
    tier: Literal["medium", "long"] | None = None
    content: str | None = None
    tags: list[str] = Field(default_factory=list)


class MemoryEntry(BaseModel):
    id: str
    content: str
    tags: list[str] = Field(default_factory=list)
    created_at: str


# --- Low-level storage ---


def _ensure_dirs() -> None:
    _HOME.mkdir(parents=True, exist_ok=True)


def _write_medium(content: str) -> None:
    _ensure_dirs()
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    with _MED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n## {ts}\n\n{content.strip()}\n")


def _write_long(entry: MemoryEntry) -> None:
    _ensure_dirs()
    with _LONG_FILE.open("a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")


def _read_long_entries() -> list[MemoryEntry]:
    if not _LONG_FILE.exists():
        return []
    entries: list[MemoryEntry] = []
    for line in _LONG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        with contextlib.suppress(Exception):
            entries.append(MemoryEntry.model_validate_json(line))
    return entries


def load_medium_memory() -> str:
    """Return medium-term memory content, or empty string if none."""
    if not _MED_FILE.exists():
        return ""
    return _MED_FILE.read_text(encoding="utf-8").strip()


def _keyword_match(text: str, query: str) -> bool:
    tokens = query.lower().split()
    low = text.lower()
    return all(t in low for t in tokens)


# --- Tool implementations (registered below) ---


def remember_impl(text: str, tags: list[str], tier: str) -> str:
    if tier == "long":
        entry = MemoryEntry(
            id=secrets.token_hex(8),
            content=text,
            tags=tags,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _write_long(entry)
        return f"Saved to long-term memory (id={entry.id})"
    _write_medium(text)
    return "Saved to medium-term memory"


def _recall_impl(query: str, k: int, tier: str) -> str:
    results: list[str] = []

    if tier in ("medium", "all") and _MED_FILE.exists():
        text = _MED_FILE.read_text(encoding="utf-8")
        for block in text.split("## "):
            if not block.strip():
                continue
            if _keyword_match(block, query):
                results.append(block.strip())
                if len(results) >= k:
                    break

    if tier in ("long", "all"):
        for entry in reversed(_read_long_entries()):
            if _keyword_match(entry.content + " " + " ".join(entry.tags), query):
                results.append(f"[{entry.id}] {entry.content}")
                if len(results) >= k:
                    break

    if not results:
        return f"No memory found for {query!r}"
    return "\n\n---\n\n".join(results[:k])


# --- Registered tools ---


@tool(name="remember", desc="Save a fact or observation to memory.")
def remember(text: str, tags: list[str] | None = None, tier: str = "medium") -> str:
    return remember_impl(text, tags or [], tier)


@tool(name="recall", desc="Search memory for relevant facts matching a query.")
def recall(query: str, k: int = 5, tier: str = "all") -> str:
    return _recall_impl(query, k, tier)


# --- REPL-only helpers (not tools) ---


def forget(entry_id: str) -> bool:
    """Delete a long-term entry by id. Returns True if found and removed."""
    entries = _read_long_entries()
    new_entries = [e for e in entries if e.id != entry_id]
    if len(new_entries) == len(entries):
        return False
    _ensure_dirs()
    with _LONG_FILE.open("w", encoding="utf-8") as f:
        for e in new_entries:
            f.write(e.model_dump_json() + "\n")
    return True


def memory_list(tier: str = "all") -> str:
    lines: list[str] = []
    if tier in ("medium", "all") and _MED_FILE.exists():
        lines.append(f"=== medium ({_MED_FILE}) ===")
        lines.append(_MED_FILE.read_text(encoding="utf-8").strip())
    if tier in ("long", "all"):
        entries = _read_long_entries()
        if entries:
            lines.append(f"=== long ({len(entries)} entries) ===")
            for e in entries:
                lines.append(f"[{e.id}] ({e.created_at}) {e.content}")
    return "\n".join(lines) if lines else "Memory is empty."


def memory_clear(tier: str = "all") -> int:
    cleared = 0
    if tier in ("medium", "all") and _MED_FILE.exists():
        _MED_FILE.unlink()
        cleared += 1
    if tier in ("long", "all") and _LONG_FILE.exists():
        _LONG_FILE.unlink()
        cleared += 1
    return cleared
