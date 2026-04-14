"""Statistics display."""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from src.agent import PromptDocument
from src.model import HarnessSettings

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


class StatsRenderer:
    """Render execution statistics."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def render_turn_stats(self, result: Any, elapsed: float, model_name: str) -> None:
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

    def render_persona_switch(self, persona: PromptDocument) -> None:
        self.console.print()
        self.console.print(
            Text.assemble(
                ("  persona  ", "dim"),
                _persona_chip(persona.name),
                ("  ", ""),
                (persona.description or "", "italic dim"),
            )
        )

    def render_mode_state(self, settings: HarnessSettings) -> None:
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
