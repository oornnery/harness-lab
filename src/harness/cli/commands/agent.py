"""Agent persona, mode, and step commands."""

from __future__ import annotations

from pydantic_ai import UsageLimits
from src.agent import list_personas, load_persona

from .base import ExtensionState

_BUDGETS = {
    "cheap": UsageLimits(request_limit=20, total_tokens_limit=50_000),
    "rich": UsageLimits(request_limit=100, total_tokens_limit=500_000),
    "none": None,
}


async def agent_command(state: ExtensionState, arg: str) -> None:
    if state.renderer is None or state.builder is None or state.handle is None:
        state.console.print("[red]/agent unavailable: renderer/builder/handle not wired.[/]")
        return
    target = arg.strip()
    if not target:
        state.renderer.help_personas(list_personas(), current=state.handle.persona.name)
        return
    try:
        load_persona(target)
    except FileNotFoundError:
        state.console.print(f"[red]Persona not found:[/] {target}")
        return
    state.handle = state.builder.rebuild(state.handle, target)
    state.renderer.persona_switch(state.handle.persona)


async def mode_command(state: ExtensionState, arg: str) -> None:
    if state.renderer is None:
        state.console.print("[red]/mode unavailable: renderer not wired.[/]")
        return
    token = arg.strip().lower()
    settings = state.deps.settings
    if not token:
        state.renderer.mode_state(settings)
        return
    if token.startswith("budget="):
        name = token.split("=", 1)[1]
        if name not in _BUDGETS:
            state.console.print("[red]Usage:[/] /mode budget=cheap|rich|none")
            return
        settings.usage_limits = _BUDGETS[name]
    elif token == "readonly":
        settings.read_only = not settings.read_only
    elif token == "manual":
        settings.approval_mode = "manual"
    elif token in {"auto", "auto-safe"}:
        settings.approval_mode = "auto-safe"
    elif token == "never":
        settings.approval_mode = "never"
    else:
        state.console.print(
            "[red]Usage:[/] /mode [readonly|manual|auto|never|budget=cheap|rich|none]"
        )
        return
    state.renderer.mode_state(settings)


async def step_command(state: ExtensionState, arg: str) -> None:
    from ..step import StepRunner

    if state.handle is None:
        state.console.print("[red]/step unavailable: no handle.[/]")
        return
    prompt = arg.strip()
    if not prompt:
        state.console.print("[red]Usage:[/] /step <prompt>")
        return
    runner = StepRunner(state.console)
    result = await runner.run(state.handle, prompt)
    if result is not None and hasattr(result, "all_messages"):
        state.handle.history = result.all_messages()
        await state.session_store.save_history(state.session_id, state.handle.history)
