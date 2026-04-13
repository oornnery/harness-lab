from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from pydantic_ai import Agent, RunContext
from pydantic_ai.agent import EndStrategy
from pydantic_ai.capabilities import (
    AbstractCapability,
    IncludeToolReturnSchemas,
    Thinking,
    WebFetch,
    WebSearch,
)
from pydantic_ai.mcp import load_mcp_servers

from ..model import HarnessSettings, ModelAdapter
from ..policy import HarnessDeps
from ..schema import HarnessOutput, HarnessOutputValidator, build_output_types
from ..sessions import SessionStore
from ..tools import ToolRuntime
from .delegation import build_delegation_tools
from .history import (
    adaptive_truncate_processor,
    pii_filter_processor,
    summarize_old_processor,
    truncate_processor,
)
from .hooks import build_harness_hooks
from .personas import PromptDocument, load_persona, render_dynamic


@dataclass
class AgentHandle:
    agent: Agent[HarnessDeps, HarnessOutput]
    deps: HarnessDeps
    runtime: ToolRuntime
    persona: PromptDocument
    history: list[Any]


class AgentBuilder:
    def __init__(
        self,
        settings: HarnessSettings,
        model_adapter: ModelAdapter,
        session_store: SessionStore,
    ) -> None:
        self.settings = settings
        self.model_adapter = model_adapter
        self.session_store = session_store

    def _build_agent(
        self,
        deps: HarnessDeps,
        runtime: ToolRuntime,
        persona: PromptDocument,
    ) -> Agent[HarnessDeps, HarnessOutput]:
        meta = persona.metadata
        base_settings = self.model_adapter.build_model_settings() or {}
        persona_settings = meta.get("model_settings") or {}
        merged_settings = {**base_settings, **persona_settings}

        capabilities: list[AbstractCapability[HarnessDeps]] = [
            build_harness_hooks(),
            WebSearch(builtin=False),
            WebFetch(builtin=False),
            IncludeToolReturnSchemas(),
        ]
        thinking_effort = meta.get("thinking")
        if thinking_effort and self.settings.show_thinking:
            capabilities.append(Thinking(effort=thinking_effort))

        toolsets: list[Any] = []
        if self.settings.mcp_config_path:
            toolsets.extend(load_mcp_servers(self.settings.mcp_config_path))

        agent = Agent[HarnessDeps, HarnessOutput](
            self.model_adapter.build_model(),
            deps_type=HarnessDeps,
            output_type=build_output_types(native=self.model_adapter.supports_native_output()),
            name=persona.name,
            description=persona.description,
            instructions=persona.content,
            model_settings=cast(Any, merged_settings or None),
            history_processors=self._history_processors(),
            tools=[*runtime.as_tools(), *build_delegation_tools(self, meta)],
            toolsets=toolsets or None,
            capabilities=capabilities,
            retries=int(meta.get("retries", 2)),
            output_retries=int(meta.get("output_retries", 1)),
            end_strategy=cast(EndStrategy, meta.get("end_strategy", "exhaustive")),
            tool_timeout=float(self.settings.tool_timeout_seconds),
            metadata=lambda ctx: {
                "session_id": ctx.deps.session_id,
                "workspace": str(ctx.deps.workspace.root),
                "persona": persona.name,
            },
        )

        persona_name = persona.name

        @agent.instructions
        async def _dynamic_instructions(ctx: RunContext[HarnessDeps]) -> str:
            return render_dynamic(ctx, persona_name)

        agent.output_validator(HarnessOutputValidator())
        return agent

    def _history_processors(self) -> list[Any]:
        processors: list[Any] = [pii_filter_processor()]
        if self.settings.summarize_model:
            processors.append(
                summarize_old_processor(
                    keep_last=self.settings.summarize_keep_last,
                    summarize_model=self.settings.summarize_model,
                )
            )
        processors.append(
            adaptive_truncate_processor(
                soft_token_limit=self.settings.adaptive_trim_threshold,
                floor_messages=self.settings.adaptive_trim_floor,
            )
        )
        processors.append(truncate_processor(self.settings.max_history_messages))
        return processors

    def setup(
        self,
        deps: HarnessDeps,
        history: list[Any],
        persona_name: str = "AGENTS",
    ) -> AgentHandle:
        persona = load_persona(persona_name)
        deps.persona_meta = dict(persona.metadata)
        runtime = ToolRuntime(deps)
        agent = self._build_agent(deps, runtime, persona)
        return AgentHandle(
            agent=agent, deps=deps, runtime=runtime, persona=persona, history=history
        )

    def rebuild(self, handle: AgentHandle, persona_name: str) -> AgentHandle:
        persona = load_persona(persona_name)
        handle.deps.persona_meta = dict(persona.metadata)
        agent = self._build_agent(handle.deps, handle.runtime, persona)
        return AgentHandle(
            agent=agent,
            deps=handle.deps,
            runtime=handle.runtime,
            persona=persona,
            history=handle.history,
        )
