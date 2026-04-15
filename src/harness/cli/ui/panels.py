"""Rich panel components for CLI UI."""

from __future__ import annotations

import json
from typing import Any

from rich.box import MINIMAL
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from src.agent import PromptDocument
from src.model import HarnessSettings, ModelAdapter
from src.schema import HarnessOutput

# Tree glyphs
_TREE_BRANCH = "├─"
_TREE_LAST = "└─"
_TREE_CONT = "│ "

# Persona -> (fg, bg) for the chip badge.
_PERSONA_COLORS: dict[str, tuple[str, str]] = {
    "AGENTS": ("black", "cyan"),
    "coder": ("black", "green"),
    "planner": ("white", "magenta"),
    "reviewer": ("black", "yellow"),
}
_FALLBACK_PERSONA = ("black", "white")


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


class BootPanel:
    """Boot sequence panel."""

    def __init__(self, console: Console, settings: HarnessSettings) -> None:
        self.console = console
        self.settings = settings

    def render(self, model_adapter: ModelAdapter) -> None:
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


class ResultPanel:
    """Final result panel."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def render(self, output: HarnessOutput) -> None:
        from pydantic_ai import DeferredToolRequests

        if isinstance(output, DeferredToolRequests):
            self.console.print(Text("unexpected deferred output at render phase.", style="red"))
            return

        self.console.print()
        self.console.print(Rule("result", style="cyan"))
        self.console.print(Padding(Markdown(output.summary), (0, 2)))

        if output.reasoning_summary:
            self.console.print()
            self.console.print(Rule("reasoning", style="grey50"))
            self.console.print(Padding(Markdown(output.reasoning_summary, style="grey50"), (0, 2)))

        if output.files_considered:
            self.console.print()
            self.console.print(Rule("files", style="grey50"))
            self.console.print(
                Padding(Text(" · ".join(output.files_considered), style="grey50"), (0, 2))
            )

        if output.actions:
            self.console.print()
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
            self.console.print()
            self.console.print(Rule("next steps", style="grey50"))
            for step in output.next_steps:
                self.console.print(Text.assemble((f"  {_TREE_BRANCH} ", "cyan"), (step, "")))
        self.console.print(Rule(style="grey30"))


class ApprovalPanel:
    """Approval prompt panel."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def render(self, approval: Any) -> None:
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
            args_view: RenderableType = Syntax(args_text, "json", theme="ansi_dark", word_wrap=True)
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


class HelpPanel:
    """Help system panel."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def render_personas(self, personas: list[PromptDocument], current: str) -> None:
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
