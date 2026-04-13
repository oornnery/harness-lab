from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai import ModelMessage, RunContext

HistoryProcessor = Callable[[list[ModelMessage]], Awaitable[list[ModelMessage]]]
AdaptiveHistoryProcessor = Callable[
    [RunContext[Any], list[ModelMessage]], Awaitable[list[ModelMessage]]
]

_PII_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9]{20,})"),
    re.compile(r"(ghp_[A-Za-z0-9]{20,})"),
    re.compile(r"(xox[baprs]-[A-Za-z0-9-]{10,})"),
    re.compile(r"(AKIA[0-9A-Z]{16})"),
    re.compile(r"(-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----)"),
]

_PII_PLACEHOLDER = "[REDACTED]"


def _redact(text: str) -> str:
    for pattern in _PII_PATTERNS:
        text = pattern.sub(_PII_PLACEHOLDER, text)
    return text


def pii_filter_processor() -> HistoryProcessor:
    """Redact common secret patterns from message text parts.

    Operates on a best-effort basis: only `content` string fields are
    scanned. Structured tool call arguments are left alone.
    """

    async def _process(messages: list[ModelMessage]) -> list[ModelMessage]:
        for message in messages:
            parts = getattr(message, "parts", None)
            if not parts:
                continue
            for part in parts:
                content = getattr(part, "content", None)
                if isinstance(content, str):
                    redacted = _redact(content)
                    if redacted != content:
                        part.content = redacted  # type: ignore[attr-defined]
        return messages

    return _process


def truncate_processor(max_messages: int) -> HistoryProcessor:
    """Keep only the last `max_messages` entries."""

    async def _process(messages: list[ModelMessage]) -> list[ModelMessage]:
        if len(messages) <= max_messages:
            return messages
        return messages[-max_messages:]

    return _process


def adaptive_truncate_processor(
    soft_token_limit: int,
    floor_messages: int = 6,
) -> AdaptiveHistoryProcessor:
    """Trim history adaptively based on real token usage.

    If `ctx.usage.total_tokens` is above `soft_token_limit`, aggressively
    trim to `floor_messages`. Otherwise pass the history through
    unchanged. Requires the `RunContext`-aware signature so pydantic-ai
    injects usage stats.
    """

    async def _process(ctx: RunContext[Any], messages: list[ModelMessage]) -> list[ModelMessage]:
        usage = getattr(ctx, "usage", None)
        total = getattr(usage, "total_tokens", 0) or 0
        if total < soft_token_limit or len(messages) <= floor_messages:
            return messages
        return messages[-floor_messages:]

    return _process


def _messages_to_text(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    for msg in messages:
        parts = getattr(msg, "parts", None) or []
        for part in parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and content.strip():
                kind = type(part).__name__
                chunks.append(f"[{kind}] {content.strip()}")
    return "\n".join(chunks)


def summarize_old_processor(keep_last: int, summarize_model: str) -> HistoryProcessor:
    """Replace older messages with a synthetic summary message.

    A lightweight secondary `Agent` built lazily on first call compresses
    the prefix of the history. The tail (`keep_last` entries) is kept
    verbatim.
    """
    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    summarizer: Agent[None, str] | None = None

    async def _process(messages: list[ModelMessage]) -> list[ModelMessage]:
        nonlocal summarizer
        if len(messages) <= keep_last:
            return messages
        prefix = messages[:-keep_last]
        tail = messages[-keep_last:]
        text = _messages_to_text(prefix)
        if not text:
            return tail

        if summarizer is None:
            summarizer = Agent(
                summarize_model,
                instructions=(
                    "You compress prior agent conversation history. "
                    "Produce a compact factual summary (<=300 words) "
                    "preserving decisions, file paths, tool calls, and "
                    "open questions. Drop filler."
                ),
            )

        try:
            result = await summarizer.run(text)
            summary = str(result.output)
        except Exception as exc:
            summary = f"[summary unavailable: {type(exc).__name__}]"

        synthetic = ModelRequest(
            parts=[UserPromptPart(content=f"[prior history summary]\n{summary}")]
        )
        return [synthetic, *tail]

    return _process
