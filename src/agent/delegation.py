from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry, RunContext, Tool

from ..policy import HarnessDeps
from ..schema import FinalAnswer
from ..tools import ToolRuntime

if TYPE_CHECKING:
    from .builder import AgentBuilder

_MAX_DEPTH = 2


class DelegationTools:
    """Produces `delegate_to_<persona>` tools for agent delegation.

    A builder factory is required instead of a live handle because each
    call builds a disposable sub-agent with a different persona.
    """

    def __init__(self, builder: AgentBuilder, targets: list[str]) -> None:
        self.builder = builder
        self.targets = targets

    def as_tools(self) -> list[Tool[HarnessDeps]]:
        return [self._make_tool(name) for name in self.targets]

    def _make_tool(self, persona_name: str) -> Tool[HarnessDeps]:
        async def _delegate(ctx: RunContext[HarnessDeps], task: str) -> str:
            if ctx.deps.delegation_depth >= _MAX_DEPTH:
                raise ModelRetry(
                    f"delegation depth limit {_MAX_DEPTH} reached; finish this step directly."
                )
            from .personas import load_persona

            persona = load_persona(persona_name)
            sub_runtime = ToolRuntime(ctx.deps)
            sub_agent = self.builder._build_agent(  # type: ignore[attr-defined]
                ctx.deps, sub_runtime, persona
            )

            ctx.deps.delegation_depth += 1
            try:
                result = await sub_agent.run(task, deps=ctx.deps)
            finally:
                ctx.deps.delegation_depth -= 1

            output = result.output
            if isinstance(output, FinalAnswer):
                return output.summary
            return f"[deferred: {type(output).__name__}]"

        _delegate.__name__ = f"delegate_to_{persona_name}"
        _delegate.__doc__ = (
            f"Delegate a sub-task to the `{persona_name}` persona. "
            f"Returns the sub-agent final summary. "
            f"Use for work that should be done by that specialist."
        )
        return Tool(
            _delegate,
            name=f"delegate_to_{persona_name}",
            metadata={"category": "delegation", "target": persona_name},
        )


def build_delegation_tools(
    builder: AgentBuilder, persona_meta: dict[str, Any]
) -> list[Tool[HarnessDeps]]:
    targets = persona_meta.get("delegates") or []
    if not targets:
        return []
    return DelegationTools(builder, list(targets)).as_tools()
