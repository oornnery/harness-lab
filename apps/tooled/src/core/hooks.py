from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from .utils import logger

__all__ = ["ToolCall", "hook", "hooks_list", "run_post_hooks", "run_pre_hooks"]


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


type PreHook = Callable[[ToolCall], Any]
type PostHook = Callable[[ToolCall, str], Any]

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
        if phase == "pre":
            _PRE.append((fn, tool))
        elif phase == "post":
            _POST.append((fn, tool))
        else:
            raise ValueError(f"Unknown hook phase: {phase!r}. Use 'pre' or 'post'.")
        return fn

    return deco


async def run_pre_hooks(call: ToolCall, local: list[tuple[str, Any, str | None]] | None = None) -> None:
    hooks = [*_PRE]
    if local:
        hooks.extend((fn, scope) for phase, fn, scope in local if phase == "pre")
    for fn, scope in hooks:
        if scope is not None and scope != call.name:
            continue
        logger.debug("pre-hook %s tool=%s", getattr(fn, "__name__", repr(fn)), call.name)
        result = fn(call)
        if inspect.iscoroutine(result):
            await result


async def run_post_hooks(call: ToolCall, output: str, local: list[tuple[str, Any, str | None]] | None = None) -> str:
    hooks = [*_POST]
    if local:
        hooks.extend((fn, scope) for phase, fn, scope in local if phase == "post")
    for fn, scope in hooks:
        if scope is not None and scope != call.name:
            continue
        logger.debug("post-hook %s tool=%s", getattr(fn, "__name__", repr(fn)), call.name)
        result = fn(call, output)
        if inspect.iscoroutine(result):
            result = await result
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
