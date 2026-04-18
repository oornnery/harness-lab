from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core._context import RunContext
from ..core.tool import tool

if TYPE_CHECKING:
    from ..core.agents import Agent


@tool(name="delegate", desc="Run a sub-task with a fresh isolated agent and return its reply.")
async def delegate(ctx: RunContext, instructions: str) -> str:
    from ..core.agents import Agent

    parent: Agent[Any] = ctx.agent
    sub_cfg = parent.config_for_role("delegate")
    sub: Agent[Any] = Agent(config=sub_cfg)
    sub.confirm_fn = parent.confirm_fn
    sub.runtime = parent.runtime
    sub.disabled_tools |= parent.scoped_disabled_tools("delegate")
    try:
        resp = await sub.chat(instructions)
        return resp.content
    finally:
        await sub.aclose()
