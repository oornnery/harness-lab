"""Command base types and extension state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rich.console import Console
from src.policy import HarnessDeps
from src.session import UnifiedStore

if TYPE_CHECKING:
    from src.agent import AgentBuilder, AgentHandle

    from ..ui.renderer import StreamRenderer

CommandHandler = Callable[["ExtensionState", str], Awaitable[None]]

MAX_PENDING_ATTACHMENTS = 10


@dataclass
class CommandSpec:
    name: str
    help_text: str
    handler: CommandHandler


@dataclass
class HarnessExtension:
    name: str
    description: str
    commands: list[CommandSpec] = field(default_factory=list)


@dataclass
class ExtensionState:
    console: Console
    deps: HarnessDeps
    session_store: UnifiedStore
    known_tools: list[str]
    workspace_summary: str
    handle: AgentHandle | None = None
    builder: AgentBuilder | None = None
    renderer: StreamRenderer | None = None
    pending_attachments: list[Any] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return self.deps.session_id


def stringify(value: Any) -> str:
    return str(value)
