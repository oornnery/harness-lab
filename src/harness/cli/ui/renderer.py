"""Minimal Rich-based CLI renderer - coordinator.

Design rules:
- No heavy boxes. Use `Rule` + `Padding` + dim prefixes.
- Colored chips for persona/mode/budget.
- ASCII tree for tool calls.
- Panels reserved for approval prompts (hard stops) and the final result.
- Partial `FinalAnswer` validation via Pydantic to stream the summary
  markdown live.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import (
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.messages import ThinkingPart, ThinkingPartDelta
from rich.box import SIMPLE
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from src.agent import PromptDocument
from src.model import HarnessSettings, ModelAdapter
from src.schema import FinalAnswer, HarnessOutput

from .panels import ApprovalPanel, BootPanel, HelpPanel, ResultPanel
from .progress import RunProgress
from .stats import StatsRenderer
from .tools import ToolCallRenderer

_FINAL_ANSWER_ADAPTER: TypeAdapter[FinalAnswer] = TypeAdapter(FinalAnswer)


def _partial_final_answer(buffer: str) -> FinalAnswer | None:
    if not buffer.strip():
        return None
    try:
        return _FINAL_ANSWER_ADAPTER.validate_json(
            buffer, experimental_allow_partial="trailing-strings"
        )
    except ValidationError:
        return None


class StreamRenderer:
    """Minimal renderer - coordinates all UI components.

    This class delegates to specialized components for each concern:
    - BootPanel: boot sequence
    - ResultPanel: final results
    - ApprovalPanel: approval prompts
    - HelpPanel: help system
    - RunProgress: progress tracking
    - ToolCallRenderer: tool call visualization
    - StatsRenderer: statistics display
    """

    def __init__(self, console: Console, settings: HarnessSettings) -> None:
        self.console = console
        self.settings = settings

        # UI Components
        self.boot = BootPanel(console, settings)
        self.result = ResultPanel(console)
        self.approval = ApprovalPanel(console)
        self.help = HelpPanel(console)
        self.progress = RunProgress(console)
        self.tools = ToolCallRenderer(console)
        self.stats = StatsRenderer(console)

        # Internal state
        self._live: Live | None = None
        self._stream_buffer = ""
        self._thinking_buffer = ""
        self._active_tools: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API - delegated to components
    # ------------------------------------------------------------------

    def boot_panel(self, model_adapter: ModelAdapter) -> None:
        """Render boot panel."""
        self.boot.render(model_adapter)

    def final_result(self, output: HarnessOutput) -> None:
        """Render final result panel."""
        self.result.render(output)

    def render_approval_request(self, approval: Any) -> None:
        """Render approval request panel."""
        self.approval.render(approval)

    async def approval_prompt(self) -> bool:
        """Prompt user for approval."""
        from rich.prompt import Confirm

        result = await asyncio.to_thread(
            Confirm.ask,
            "[bold yellow]approve this action?[/]",
            console=self.console,
            default=False,
        )
        return bool(result)

    def help_personas(self, personas: list[PromptDocument], current: str) -> None:
        """Render personas help."""
        self.help.render_personas(personas, current)

    def start_status(self, label: str) -> None:
        """Start progress status."""
        self.progress.start(label)

    def stop_status(self) -> None:
        """Stop progress status."""
        self.progress.stop()

    def restart_status(self, label: str) -> None:
        """Restart progress status with new label."""
        self.progress.update(label)

    def turn_stats(self, result: Any, elapsed: float, model_name: str) -> None:
        """Render turn statistics."""
        self.stats.render_turn_stats(result, elapsed, model_name)

    def persona_switch(self, persona: PromptDocument) -> None:
        """Render persona switch notification."""
        self.stats.render_persona_switch(persona)

    def mode_state(self, settings: HarnessSettings) -> None:
        """Render current mode state."""
        self.stats.render_mode_state(settings)

    def prompt_prefix(self, persona: str) -> str:
        """Generate prompt prefix with persona and mode chips."""

        mode_label = "ro" if self.settings.read_only else "rw"
        mode_color = "yellow" if self.settings.read_only else "green"
        persona_fg, persona_bg = {
            "AGENTS": ("black", "cyan"),
            "coder": ("black", "green"),
            "planner": ("white", "magenta"),
            "reviewer": ("black", "yellow"),
        }.get(persona, ("black", "white"))

        appr = self.settings.approval_mode
        appr_color = "red" if appr == "always" else "yellow" if appr == "tool" else "green"

        parts = [
            f"[bold {persona_fg} on {persona_bg}] {persona} [/]",
            f"[{mode_color}]mode={mode_label}[/]",
            f"[{appr_color}]approval={appr}[/]",
        ]
        return " ".join(parts) + " [bold cyan]\u276f[/] "

    # ------------------------------------------------------------------
    # Stream event handling
    # ------------------------------------------------------------------

    def cleanup_turn(self) -> None:
        """Clean up after a turn."""
        self.progress.stop()
        self._stop_live_panel()
        self._flush_thinking()
        self.tools.reset_counter()
        self._active_tools.clear()

    def on_stream_event(self, event: Any) -> None:
        """Handle stream events from agent."""
        if isinstance(event, PartStartEvent):
            if isinstance(event.part, TextPart):
                self._feed_live_panel(event.part.content)
            elif isinstance(event.part, ThinkingPart):
                self._feed_thinking(event.part.content)
        elif isinstance(event, PartDeltaEvent):
            if isinstance(event.delta, TextPartDelta):
                self._feed_live_panel(event.delta.content_delta)
            elif isinstance(event.delta, ThinkingPartDelta):
                self._feed_thinking(event.delta.content_delta or "")
        elif isinstance(event, FunctionToolCallEvent):
            self._emit_tool_call(event)
        elif isinstance(event, FunctionToolResultEvent):
            self._emit_tool_result(event)
        elif isinstance(event, FinalResultEvent):
            self._stop_live_panel()

    # ------------------------------------------------------------------
    # Private methods - streaming and tools
    # ------------------------------------------------------------------

    def _feed_thinking(self, chunk: str) -> None:
        """Feed thinking content to buffer."""
        self._thinking_buffer += chunk

    def _flush_thinking(self) -> None:
        """Flush thinking buffer to console."""
        if not self._thinking_buffer.strip():
            self._thinking_buffer = ""
            return
        body = self._thinking_buffer[:500]
        suffix = "…" if len(self._thinking_buffer) > 500 else ""
        self.console.print(
            Panel(
                Markdown(body + suffix),
                title="thinking",
                title_align="left",
                border_style="magenta",
                box=SIMPLE,
                padding=(0, 1),
            )
        )
        self._thinking_buffer = ""

    def _start_live_panel(self) -> None:
        """Start live streaming panel."""
        if self._live is not None:
            return
        self._stream_buffer = ""
        self._live = Live(
            Text(""),
            console=self.console,
            transient=True,
            refresh_per_second=12,
        )
        self._live.start()

    def _feed_live_panel(self, chunk: str) -> None:
        """Feed content to live panel."""
        if self._live is None:
            self._flush_thinking()
            self._start_live_panel()
        assert self._live is not None
        self._stream_buffer += chunk

        partial = _partial_final_answer(self._stream_buffer)
        summary = partial.summary if partial else ""
        reasoning = partial.reasoning_summary if partial else ""

        sections: list[RenderableType] = []
        sections.append(Rule("streaming", style="cyan"))
        if summary:
            sections.append(Padding(Markdown(summary), (0, 2)))
        else:
            sections.append(Padding(Text("waiting for content…", style="dim"), (0, 2)))
        if reasoning:
            sections.append(Rule("reasoning", style="grey50"))
            sections.append(Padding(Markdown(reasoning, style="grey50"), (0, 2)))
        self._live.update(Group(*sections))

    def _stop_live_panel(self) -> None:
        """Stop live streaming panel."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _emit_tool_call(self, event: FunctionToolCallEvent) -> None:
        """Emit tool call visualization."""
        prefix = self._active_tools.get(event.part.tool_call_id, "")
        self.tools.render_tool_call(
            event.part.tool_name, event.part.args, event.part.tool_call_id, prefix
        )
        self._active_tools[event.part.tool_call_id] = f"call #{self.tools._counter}"

    def _emit_tool_result(self, event: FunctionToolResultEvent) -> None:
        """Emit tool result visualization."""
        self.tools.render_tool_result(event.result, event.tool_call_id)
        self._active_tools.pop(event.tool_call_id, None)
