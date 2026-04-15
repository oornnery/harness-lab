"""Shared output clipping helper used by every tool that returns text."""

from __future__ import annotations


def clip(text: str, limit: int) -> str:
    """Return `text` truncated to `limit` chars with a visible marker."""
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated {len(text) - limit} chars]"
