from __future__ import annotations

from contextvars import ContextVar
from typing import Any

__all__ = ["RunContext", "_run_ctx"]


class RunContext[D]:
    """Typed context available inside tool dispatch via `ctx: RunContext[MyDeps]`."""

    __slots__ = ("agent", "deps", "tool_call", "turn")

    def __init__(self, agent: Any, deps: D, tool_call: Any, turn: int) -> None:
        self.agent = agent
        self.deps = deps
        self.tool_call = tool_call
        self.turn = turn


# Set to current RunContext before each tool dispatch, reset after.
_run_ctx: ContextVar[RunContext[Any]] = ContextVar("run_ctx")
