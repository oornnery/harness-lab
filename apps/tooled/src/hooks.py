from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from .utils import logger

__all__ = ["ToolCall", "hook", "hooks_list", "run_post_hooks", "run_pre_hooks"]


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


type PreHook = Callable[[ToolCall], None]
type PostHook = Callable[[ToolCall, str], str | None]

_PRE: list[tuple[PreHook, str | None]] = []
_POST: list[tuple[PostHook, str | None]] = []


def hook(
    phase: str,
    *,
    tool: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a pre or post hook.

    @hook("pre") -- runs before tool dispatch; may raise ToolDenied
    @hook("post") -- runs after dispatch; may return modified output
    @hook("pre", tool="shell") -- scoped to a single tool
    """
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        if phase == "pre":
            _PRE.append((wrapper, tool))  # type: ignore[arg-type]
        elif phase == "post":
            _POST.append((wrapper, tool))  # type: ignore[arg-type]
        else:
            raise ValueError(f"Unknown hook phase: {phase!r}. Use 'pre' or 'post'.")
        return wrapper

    return deco


def run_pre_hooks(call: ToolCall) -> None:
    for fn, scope in _PRE:
        if scope is not None and scope != call.name:
            continue
        logger.debug("pre-hook %s tool=%s", getattr(fn, "__name__", repr(fn)), call.name)
        fn(call)


def run_post_hooks(call: ToolCall, output: str) -> str:
    for fn, scope in _POST:
        if scope is not None and scope != call.name:
            continue
        logger.debug("post-hook %s tool=%s", getattr(fn, "__name__", repr(fn)), call.name)
        result = fn(call, output)
        if result is not None:
            output = result
    return output


def hooks_list() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for fn, scope in _PRE:
        rows.append({"phase": "pre", "name": getattr(fn, "__name__", repr(fn)), "tool": scope or "*"})
    for fn, scope in _POST:
        rows.append({"phase": "post", "name": getattr(fn, "__name__", repr(fn)), "tool": scope or "*"})
    return rows
