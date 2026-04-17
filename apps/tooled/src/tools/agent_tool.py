from __future__ import annotations

from typing import Any

from . import tool


@tool(name="delegate", desc="Run a sub-task with a fresh isolated agent and return its reply.")
async def delegate(instructions: str) -> str:
    # lazy imports: agent_tool -> agent would be circular at module level
    from ..agent import Agent, _agent_ctx

    parent: Agent[Any] = _agent_ctx.get()
    sub: Agent[Any] = Agent(config=parent.config)
    sub.confirm_fn = parent.confirm_fn
    try:
        resp = await sub.chat(instructions)
        return resp.content
    finally:
        await sub.aclose()
