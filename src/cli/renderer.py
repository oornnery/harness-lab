"""Minimal Rich-based CLI renderer.

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
import json
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import (
    DeferredToolRequests,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)
from rich.box import MINIMAL, SIMPLE
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn
from rich.prompt import Confirm
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ..agent import PromptDocument
from ..model import HarnessSettings, ModelAdapter
from ..schema import FinalAnswer, HarnessOutput

_FINAL_ANSWER_ADAPTER: TypeAdapter[FinalAnswer] = TypeAdapter(FinalAnswer)

# Persona -> (fg, bg) for the chip badge.
_PERSONA_COLORS: dict[str, tuple[str, str]] = {
    "AGENTS": ("black", "cyan"),
    "coder": ("black", "green"),
    "planner": ("white", "magenta"),
    "reviewer": ("black", "yellow"),
}
_FALLBACK_PERSONA = ("black", "white")

# Tree glyphs
_TREE_BRANCH = "├─"
_TREE_LAST = "└─"
_TREE_CONT = "│ "


def _partial_final_answer(buffer: str) -> FinalAnswer | None:
    if not buffer.strip():
        return None
    try:
        return _FINAL_ANSWER_ADAPTER.validate_json(
            buffer, experimental_allow_partial="trailing-strings"
        )
    except ValidationError:
        return None


def _persona_chip(name: str) -> Text:
    fg, bg = _PERSONA_COLORS.get(name, _FALLBACK_PERSONA)
    return Text(f" {name} ", style=f"bold {fg} on {bg}")


def _mode_chip(label: str, value: str, positive: bool = True) -> Text:
    bg = "green" if positive else "red"
    return Text(f" {label}={value} ", style=f"black on {bg}")


def _budget_chip(settings: HarnessSettings) -> Text:
    limits = getattr(settings, "usage_limits", None)
    if limits is None:
        return Text(" budget=none ", style="black on grey50")
    return Text(" budget=limited ", style="black on yellow")


class StreamRenderer:
    """Minimal renderer. See module docstring for design rules."""

    def __init__(self, console: Console, settings: HarnessSettings) -> None:
        self.console = console
        self.settings = settings
        self._progress: Progress | None = None
        self._progress_task: TaskID | None = None
        self._progress_active = False
        self._live: Live | None = None
        self._stream_buffer = ""
        self._thinking_buffer = ""
        self._active_tools: dict[str, str] = {}
        self._tool_counter = 0

    # ------------------------------------------------------------------
    # Header / boot / status
    # ------------------------------------------------------------------

    def boot_panel(self, model_adapter: ModelAdapter) -> None:
        mode = "read-only" if self.settings.read_only else "read-write"
        header = Text.assemble(
            ("harness", "bold white on blue"),
            ("  ", ""),
            (f"{model_adapter.model_name}", "cyan"),
            ("  ·  ", "dim"),
            (f"{model_adapter.settings.resolved_workspace()}", "dim"),
            ("  ·  ", "dim"),
            (f"mode={mode}", "yellow"),
            ("  ·  ", "dim"),
            (f"approval={self.settings.approval_mode}", "magenta"),
        )
        self.console.print(header)
        self.console.print(Rule(style="grey30"))

    def start_status(self, label: str) -> None:
        self._progress = Progress(
            SpinnerColumn(style="cyan"),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("[dim]({task.elapsed:.1f}s)[/]"),
            console=self.console,
            transient=True,
        )
        self._progress_task = self._progress.add_task(label, total=None)
        self._progress.start()
        self._progress_active = True

    def stop_status(self) -> None:
        if self._progress is not None and self._progress_active:
            self._progress.stop()
            self._progress_active = False

    def restart_status(self, label: str) -> None:
        if self._progress is None:
            return
        if self._progress_task is not None:
            self._progress.update(self._progress_task, description=label)
        if not self._progress_active:
            self._progress.start()
            self._progress_active = True

    def cleanup_turn(self) -> None:
        self.stop_status()
        self._stop_live_panel()
        self._flush_thinking()
        self._progress = None
        self._progress_task = None
        self._active_tools.clear()
        self._tool_counter = 0

    # ------------------------------------------------------------------
    # Thinking
    # ------------------------------------------------------------------

    def _feed_thinking(self, chunk: str) -> None:
        self._thinking_buffer += chunk

    def _flush_thinking(self) -> None:
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

    # ------------------------------------------------------------------
    # Streaming live panel
    # ------------------------------------------------------------------

    def _start_live_panel(self) -> None:
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
            sections.append(
                Padding(Markdown(reasoning, style="grey50"), (0, 2))
            )
        self._live.update(Group(*sections))

    def _stop_live_panel(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._stream_buffer = ""

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def on_stream_event(self, event: Any) -> None:
        if isinstance(event, PartStartEvent):
            if isinstance(event.part, TextPart):
                self.stop_status()
                if event.part.content:
                    self._feed_live_panel(event.part.content)
                return
            if isinstance(event.part, ToolCallPart) and event.part.tool_name == "final_result":
                self.stop_status()
                args = event.part.args
                if args:
                    self._feed_live_panel(args if isinstance(args, str) else repr(args))
                return

        if isinstance(event, PartDeltaEvent):
            if isinstance(event.delta, TextPartDelta):
                self.stop_status()
                self._feed_live_panel(event.delta.content_delta)
                return
            if isinstance(event.delta, ToolCallPartDelta) and event.delta.args_delta is not None:
                self.stop_status()
                chunk = event.delta.args_delta
                self._feed_live_panel(chunk if isinstance(chunk, str) else repr(chunk))
                return
            if self.settings.show_thinking and isinstance(event.delta, ThinkingPartDelta):
                self.stop_status()
                self._feed_thinking(event.delta.content_delta)
                return

        if isinstance(event, FunctionToolCallEvent):
            self._emit_tool_call(event)
            return

        if isinstance(event, FunctionToolResultEvent):
            self._emit_tool_result(event)
            return

        if isinstance(event, FinalResultEvent):
            return

    # ------------------------------------------------------------------
    # Tool tree
    # ------------------------------------------------------------------

    def _emit_tool_call(self, event: FunctionToolCallEvent) -> None:
        self.stop_status()
        self._stop_live_panel()
        self._tool_counter += 1
        self._active_tools[event.part.tool_call_id] = event.part.tool_name
        args_summary = self._format_tool_args(event.part.args)
        line = Text.assemble(
            (f"{_TREE_BRANCH} ", "cyan"),
            (event.part.tool_name, "bold yellow"),
            (f" {args_summary}", "dim"),
        )
        self.console.print(line)

    def _emit_tool_result(self, event: FunctionToolResultEvent) -> None:
        self.stop_status()
        self._stop_live_panel()
        snippet = repr(event.result.content)
        if len(snippet) > 120:
            snippet = snippet[:117] + "…"
        line = Text.assemble(
            (f"{_TREE_CONT}{_TREE_LAST} ", "cyan"),
            ("result ", "green"),
            (snippet, "dim"),
        )
        self.console.print(line)
        self._active_tools.pop(event.tool_call_id, None)
        self.restart_status("[bold cyan]thinking…[/]")

    def _format_tool_args(self, args: Any) -> str:
        if args is None:
            return ""
        if isinstance(args, dict):
            items = [f"{k}={self._truncate_value(v)}" for k, v in args.items()]
            return " ".join(items)
        if isinstance(args, str):
            return self._truncate_value(args)
        return self._truncate_value(repr(args))

    @staticmethod
    def _truncate_value(value: Any, limit: int = 40) -> str:
        text = repr(value) if not isinstance(value, str) else value
        if len(text) > limit:
            return text[: limit - 1] + "…"
        return text

    # ------------------------------------------------------------------
    # Final output
    # ------------------------------------------------------------------

    def final_result(self, output: HarnessOutput) -> None:
        if isinstance(output, DeferredToolRequests):
            self.console.print(
                Text("unexpected deferred output at render phase.", style="red")
            )
            return

        self.console.print()
        self.console.print(Rule("result", style="cyan"))
        self.console.print(Padding(Markdown(output.summary), (0, 2)))

        if output.reasoning_summary:
            self.console.print()  # blank line before next section
            self.console.print(Rule("reasoning", style="grey50"))
            self.console.print(
                Padding(Markdown(output.reasoning_summary, style="grey50"), (0, 2))
            )

        if output.files_considered:
            self.console.print()  # blank line before next section
            self.console.print(Rule("files", style="grey50"))
            self.console.print(
                Padding(Text(" · ".join(output.files_considered), style="grey50"), (0, 2))
            )

        if output.actions:
            self.console.print()  # blank line before next section
            self.console.print(Rule("actions", style="grey50"))
            for action in output.actions:
                self.console.print(
                    Text.assemble(
                        (f"  {_TREE_BRANCH} ", "cyan"),
                        (action.kind, "bold yellow"),
                        ("  ", ""),
                        (action.summary, ""),
                    )
                )

        if output.next_steps:
            self.console.print()  # blank line before next section
            self.console.print(Rule("next steps", style="grey50"))
            for step in output.next_steps:
                self.console.print(
                    Text.assemble((f"  {_TREE_BRANCH} ", "cyan"), (step, ""))
                )
        self.console.print(Rule(style="grey30"))

    # ------------------------------------------------------------------
    # Stats / persona / mode
    # ------------------------------------------------------------------

    def turn_stats(self, result: Any, elapsed: float, model_name: str) -> None:
        usage = result.usage()
        parts = [
            ("model", model_name, "cyan"),
            ("elapsed", f"{elapsed:.2f}s", "yellow"),
            ("in", str(usage.input_tokens), "green"),
            ("out", str(usage.output_tokens), "green"),
            ("total", str(usage.total_tokens), "bold green"),
        ]
        if getattr(usage, "requests", None):
            parts.append(("reqs", str(usage.requests), "magenta"))
        chunks: list[tuple[str, str]] = []
        for i, (key, val, color) in enumerate(parts):
            if i > 0:
                chunks.append(("  ·  ", "dim"))
            chunks.append((f"{key}=", "dim"))
            chunks.append((val, color))
        self.console.print(Text.assemble(*chunks))

    def persona_switch(self, persona: PromptDocument) -> None:
        self.console.print()
        self.console.print(
            Text.assemble(
                ("  persona  ", "dim"),
                _persona_chip(persona.name),
                ("  ", ""),
                (persona.description or "", "italic dim"),
            )
        )

    def mode_state(self, settings: HarnessSettings) -> None:
        ro_chip = _mode_chip(
            "read_only", str(settings.read_only).lower(), positive=not settings.read_only
        )
        appr_chip = _mode_chip(
            "approval", settings.approval_mode, positive=settings.approval_mode != "never"
        )
        budget_chip = _budget_chip(settings)
        self.console.print(
            Text.assemble(
                ("  mode  ", "dim"),
                ro_chip,
                ("  ", ""),
                appr_chip,
                ("  ", ""),
                budget_chip,
            )
        )

    def help_personas(self, personas: list[PromptDocument], current: str) -> None:
        self.console.print()
        self.console.print(Rule("personas", style="cyan"))
        for p in personas:
            marker = " ◉ " if p.name == current else "   "
            chip = _persona_chip(p.name)
            desc = Text(p.description or "", style="italic dim")
            mode = str(p.metadata.get("default_mode", ""))
            self.console.print(
                Text.assemble(
                    (marker, "bold cyan"),
                    chip,
                    ("  ", ""),
                    (mode, "yellow"),
                    ("  ", ""),
                    desc,
                )
            )
        self.console.print(Rule(style="grey30"))

    # ------------------------------------------------------------------
    # Approval prompt (kept as a panel: hard stop)
    # ------------------------------------------------------------------

    def render_approval_request(self, approval: Any) -> None:
        raw_args = approval.args
        if isinstance(raw_args, str):
            try:
                args_data = json.loads(raw_args)
            except json.JSONDecodeError:
                args_data = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            args_data = raw_args
        else:
            args_data = {"_raw": repr(raw_args)}

        path = args_data.get("path") if isinstance(args_data, dict) else None

        header = Table.grid(padding=(0, 1))
        header.add_column(style="bold cyan", justify="right")
        header.add_column()
        header.add_row("tool", Text(approval.tool_name, style="bold yellow"))
        if path:
            header.add_row("path", str(path))
        header.add_row("call id", approval.tool_call_id)

        try:
            args_text = json.dumps(args_data, indent=2, ensure_ascii=False, default=str)
            args_view: RenderableType = Syntax(
                args_text, "json", theme="ansi_dark", word_wrap=True
            )
        except Exception:
            args_view = Text(repr(raw_args))

        self.console.print(
            Panel(
                Group(header, Text(""), args_view),
                title="[bold red]⚠ approval required[/]",
                title_align="left",
                border_style="red",
                box=MINIMAL,
                padding=(0, 1),
            )
        )

    async def approval_prompt(self) -> bool:
        result = await asyncio.to_thread(
            Confirm.ask,
            "[bold yellow]approve this action?[/]",
            console=self.console,
            default=False,
        )
        return bool(result)

    # ------------------------------------------------------------------
    # Prompt line helper (used by app.py to compose a colored prefix)
    # ------------------------------------------------------------------

    def prompt_prefix(self, persona: str) -> str:
        mode_label = "ro" if self.settings.read_only else "rw"
        mode_color = "yellow" if self.settings.read_only else "green"
        persona_fg, persona_bg = _PERSONA_COLORS.get(persona, _FALLBACK_PERSONA)
        appr = self.settings.approval_mode
        return (
            f"[bold {persona_fg} on {persona_bg}] {persona} [/]"
            f"[bold {mode_color}] {mode_label} [/]"
            f"[dim]{appr}[/] "
            f"[bold cyan]\u276f[/] "  # heavy right angle arrow
        )
