from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic_ai import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelRetry,
    PartStartEvent,
    RunContext,
    TextPart,
    ToolDefinition,
)
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering

from .context import IGNORED_DIRS, WorkspaceContext
from .model import HarnessSettings
from .sessions import SessionStore

REPEAT_GUARD_MAX = 256


@dataclass
class RuntimePolicy:
    settings: HarnessSettings
    workspace_root: Path
    protected_names: tuple[str, ...] = (".env", ".gitignore", "pyproject.toml")
    dangerous_shell_fragments: tuple[str, ...] = (
        "rm -rf /",
        "shutdown",
        "reboot",
        "mkfs",
        ":(){ :|:& };:",
    )
    recent_calls: OrderedDict[str, None] = field(default_factory=OrderedDict)
    mutations: list[dict[str, Any]] = field(default_factory=list)

    def resolve_path(self, raw_path: str) -> Path:
        path = (self.workspace_root / raw_path).resolve()
        if self.workspace_root != path and self.workspace_root not in path.parents:
            raise ModelRetry(f"Path escapes the workspace sandbox: {raw_path}")
        return path

    def skip_path(self, path: Path) -> bool:
        return bool(set(path.parts) & IGNORED_DIRS)

    def requires_protected_approval(self, path: Path) -> bool:
        return path.name in self.protected_names

    def requires_write_approval(self, path: Path) -> bool:
        if self.settings.approval_mode == "manual":
            return True
        return path.name in self.protected_names

    def check_write_allowed(self, path: Path) -> None:
        if self.settings.read_only:
            raise ModelRetry("The workspace is in read-only mode; write operations are disabled.")
        self.resolve_path(str(path.relative_to(self.workspace_root)))

    def check_shell_allowed(self, command: str) -> None:
        lowered = command.lower()
        if self.settings.read_only and any(
            token in lowered for token in ("rm ", "mv ", "sed -i", ">")
        ):
            raise ModelRetry("The workspace is read-only; mutating shell commands are disabled.")
        for fragment in self.dangerous_shell_fragments:
            if fragment in lowered:
                raise ModelRetry(f"Refusing dangerous shell fragment: {fragment}")

    def _fingerprint(self, tool_name: str, payload: dict[str, Any]) -> str:
        data = json.dumps({"tool": tool_name, "payload": payload}, sort_keys=True, default=str)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def guard_repeat(self, tool_name: str, payload: dict[str, Any]) -> None:
        fingerprint = self._fingerprint(tool_name, payload)
        if fingerprint in self.recent_calls:
            raise ModelRetry(
                f"The same {tool_name!r} call was already attempted. "
                "Try a different tool or new arguments."
            )
        self.recent_calls[fingerprint] = None
        while len(self.recent_calls) > REPEAT_GUARD_MAX:
            self.recent_calls.popitem(last=False)

    def record_mutation(self, kind: str, payload: dict[str, Any]) -> None:
        self.mutations.append({"kind": kind, "payload": payload})


@dataclass
class HarnessDeps:
    settings: HarnessSettings
    workspace: WorkspaceContext
    session_store: SessionStore
    session_id: str
    policy: RuntimePolicy


@dataclass
class ToolVisibilityCapability(AbstractCapability[HarnessDeps]):
    """Hide mutating tools when the harness is in read-only mode."""

    async def prepare_tools(
        self,
        ctx: RunContext[HarnessDeps],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        if not ctx.deps.settings.read_only:
            return tool_defs
        hidden = {"write_file", "replace_in_file", "run_shell"}
        return [tool for tool in tool_defs if tool.name not in hidden]


@dataclass
class AuditCapability(AbstractCapability[HarnessDeps]):
    """Audit tool activity and stream events into the session log."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def wrap_run_event_stream(
        self,
        ctx: RunContext[HarnessDeps],
        *,
        stream: AsyncIterable[AgentStreamEvent],
    ) -> AsyncIterable[AgentStreamEvent]:
        async for event in stream:
            if isinstance(event, FunctionToolCallEvent):
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "tool-call",
                        "tool": event.part.tool_name,
                        "args": event.part.args,
                        "tool_call_id": event.part.tool_call_id,
                    },
                )
            elif isinstance(event, FunctionToolResultEvent):
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "tool-result",
                        "tool_call_id": event.tool_call_id,
                        "result": repr(event.result.content)[:500],
                    },
                )
            elif isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                await ctx.deps.session_store.append_event(
                    ctx.deps.session_id,
                    {
                        "kind": "text-start",
                        "content": event.part.content[:200],
                    },
                )
            yield event


def build_capabilities() -> list[AbstractCapability[HarnessDeps]]:
    return [AuditCapability(), ToolVisibilityCapability()]
