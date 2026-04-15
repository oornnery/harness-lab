from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelRetry, RunContext, Tool
from src.policy import HarnessDeps, RuntimePolicy, WorkingMemory
from src.schema import FinalAnswer
from src.tools import ToolRuntime

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

    @staticmethod
    def _build_sub_deps(parent: HarnessDeps, task: str) -> HarnessDeps:
        """Forge a constrained child deps: read-only, never-approve, fresh state."""
        sub_settings = replace(parent.settings, read_only=True, approval_mode="never")
        sub_policy = RuntimePolicy(sub_settings, parent.workspace.root)
        return replace(
            parent,
            settings=sub_settings,
            policy=sub_policy,
            working_memory=WorkingMemory(task=task),
            delegation_depth=parent.delegation_depth + 1,
            retrieved_memories="",
        )

    def _make_tool(self, persona_name: str) -> Tool[HarnessDeps]:
        async def _delegate(ctx: RunContext[HarnessDeps], task: str) -> dict[str, Any]:
            if ctx.deps.delegation_depth >= _MAX_DEPTH:
                raise ModelRetry(
                    f"delegation depth limit {_MAX_DEPTH} reached; finish this step directly."
                )
            from .personas import load_persona

            persona = load_persona(persona_name)
            sub_deps = self._build_sub_deps(ctx.deps, task)
            sub_runtime = ToolRuntime(sub_deps)
            sub_agent = self.builder._build_agent(  # type: ignore[attr-defined]
                sub_deps, sub_runtime, persona
            )

            result = await sub_agent.run(task, deps=sub_deps)
            output = result.output
            if isinstance(output, FinalAnswer):
                return {
                    "persona": persona_name,
                    "status": "ok",
                    "answer": output.model_dump(mode="json"),
                }
            return {
                "persona": persona_name,
                "status": "deferred",
                "reason": type(output).__name__,
            }

        _delegate.__name__ = f"delegate_to_{persona_name}"
        _delegate.__doc__ = (
            f"Delegate a sub-task to the `{persona_name}` persona. "
            f"Sub-agent runs read-only with approval_mode=never and a fresh "
            f"working memory. Returns a dict with keys: persona, status "
            f"(ok|deferred), answer (full FinalAnswer fields), reason."
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
