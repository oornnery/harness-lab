import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.prompt import Confirm
from rich.table import Table

from .agent import Agent, Thinking
from .session import (
    SessionState,
    export_markdown,
    list_sessions,
    load_session,
    reset_sessions,
)
from .utils import console, thinking_progress

Command = Callable[[Agent, SessionState, str], bool]
_REGISTRY: dict[str, Command] = {}

COMMANDS_HELP: list[tuple[str, str]] = [
    ("/help", "show this help"),
    ("/stream", "toggle streaming on/off"),
    ("/thinking [low|medium|high|off]", "show or set reasoning effort"),
    ("/usage", "show session token usage"),
    ("/clear", "clear current history and usage"),
    ("/session [<id>|reset]", "list/load sessions; reset deletes all"),
    ("/set <key> [value]", "set chat param (unset if value omitted)"),
    ("/params", "show current params"),
    ("/model [<id>]", "list models or switch"),
    ("/instructions [<text>]", "show or set system instructions"),
    ("/compact [<keep>|undo]", "summarize history, keep last N; undo last compact"),
    ("/retry", "drop last assistant reply and re-run last user turn"),
    ("/edit", "drop last assistant reply and re-edit last user turn"),
    ("/history [N]", "show last N messages"),
    ("/export [path]", "export conversation as markdown"),
    ("/diagram [flow|lifecycle]", "render architecture diagram (default: both)"),
    ("/quit, /exit", "leave chat (autosaves)"),
]


def build_help() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for cmd, desc in COMMANDS_HELP:
        table.add_row(cmd, f"-- {desc}")
    return table


PARAM_PARSERS: dict[str, Callable[[str], Any]] = {
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "seed": int,
    "reasoning_effort": Thinking,
}

KEEP_LAST_DEFAULT = 4
HISTORY_PREVIEW_LIMIT = 10
CONTENT_PREVIEW = 200


def register(*names: str) -> Callable[[Command], Command]:
    def deco(fn: Command) -> Command:
        for n in names:
            _REGISTRY[n] = fn
        return fn

    return deco


def known_commands() -> list[str]:
    return sorted(_REGISTRY)


def dispatch(agent: Agent, state: SessionState, user_input: str) -> bool:
    cmd, _, arg = user_input.strip().partition(" ")
    handler = _REGISTRY.get(cmd.lower())
    if handler is None:
        console.print(f"[bold red]Unknown command:[/bold red] {user_input} [dim](type /help)[/dim]")
        return False
    return handler(agent, state, arg.strip())


def status_banner(state: SessionState, agent: Agent | None = None) -> str:
    thinking = state.params.get("reasoning_effort") or "off"
    parts: list[str] = []
    if agent is not None:
        sid = state.current_id or "new"
        parts.append(f"session={sid}")
        parts.append(f"model={agent.config.model}")
    parts.append(f"stream={'on' if state.stream_mode else 'off'}")
    parts.append(f"thinking={thinking}")
    if agent is not None:
        parts.append(f"turns={agent.turns}")
        parts.append(f"tokens={agent.session_usage.total_tokens}")
    return f"[dim]{' | '.join(parts)}[/dim]"


@register("/quit", "/exit")
def _cmd_quit(agent: Agent, state: SessionState, arg: str) -> bool:
    return True


@register("/help")
def _cmd_help(agent: Agent, state: SessionState, arg: str) -> bool:
    console.print("[bold]Commands:[/bold]")
    console.print(build_help())
    return False


@register("/stream")
def _cmd_stream(agent: Agent, state: SessionState, arg: str) -> bool:
    state.stream_mode = not state.stream_mode
    console.print(status_banner(state, agent))
    return False


@register("/thinking")
def _cmd_thinking(agent: Agent, state: SessionState, arg: str) -> bool:
    levels = ", ".join(t.value for t in Thinking)
    arg = arg.lower()
    if not arg:
        current = state.params.get("reasoning_effort") or "off"
        console.print(f"[dim]thinking = {current}[/dim] [dim](off, {levels})[/dim]")
        return False
    if arg == "off":
        state.params.pop("reasoning_effort", None)
    else:
        try:
            state.params["reasoning_effort"] = Thinking(arg)
        except ValueError:
            console.print(f"[bold red]Invalid level:[/bold red] {arg}. Use: off, {levels}")
            return False
    console.print(status_banner(state, agent))
    return False


@register("/usage")
def _cmd_usage(agent: Agent, state: SessionState, arg: str) -> bool:
    u = agent.session_usage
    console.print(
        f"[bold]Session usage[/bold] ({agent.turns} turns)\n"
        f"  prompt:     {u.prompt_tokens}\n"
        f"  completion: {u.completion_tokens}\n"
        f"  reasoning:  {u.reasoning_tokens}\n"
        f"  [bold]total:      {u.total_tokens}[/bold]"
    )
    return False


@register("/clear")
def _cmd_clear(agent: Agent, state: SessionState, arg: str) -> bool:
    if not Confirm.ask("[yellow]Clear history?[/yellow]", console=console, default=False):
        console.print("[dim]cancelled[/dim]")
        return False
    agent.reset()
    console.print("[dim]cleared.[/dim]")
    return False


@register("/session")
def _cmd_session(agent: Agent, state: SessionState, arg: str) -> bool:
    if not arg:
        files = list_sessions()
        if not files:
            console.print("[dim]No sessions.[/dim]")
            return False
        for f in files:
            data = json.loads(f.read_text())
            preview = next(
                (m["content"][:60] for m in data["messages"] if m.get("role") == "user"),
                "(empty)",
            )
            created = data.get("created_at", "")
            marker = " [bold green](current)[/bold green]" if f.stem == state.current_id else ""
            console.print(f"  [cyan]{f.stem}[/cyan]{marker} [dim]{created}[/dim] turns={data['turns']} -- {preview}")
        return False
    if arg == "reset":
        if not Confirm.ask("[yellow]Delete all sessions?[/yellow]", console=console, default=False):
            console.print("[dim]cancelled[/dim]")
            return False
        n = reset_sessions()
        console.print(f"[dim]Deleted {n}.[/dim]")
        return False
    if not load_session(arg, agent, state):
        console.print(f"[red]Session not found:[/red] {arg}")
        return False
    console.print(f"[dim]Loaded session {arg} ({len(agent.messages)} messages)[/dim]")
    return False


@register("/set")
def _cmd_set(agent: Agent, state: SessionState, arg: str) -> bool:
    key, _, raw = arg.partition(" ")
    parser = PARAM_PARSERS.get(key)
    if parser is None:
        console.print(f"[red]Unknown param:[/red] {key}. Known: {', '.join(PARAM_PARSERS)}")
        return False
    if not raw:
        state.params.pop(key, None)  # ty: ignore[no-matching-overload]
        console.print(f"[dim]{key} unset[/dim]")
        return False
    try:
        value = parser(raw.strip())
    except (ValueError, TypeError) as e:
        console.print(f"[red]Invalid value:[/red] {e}")
        return False
    state.params[key] = value  # ty: ignore[invalid-key]
    console.print(f"[dim]{key} = {value}[/dim]")
    return False


@register("/params")
def _cmd_params(agent: Agent, state: SessionState, arg: str) -> bool:
    if not state.params:
        console.print("[dim]No params set (defaults).[/dim]")
        return False
    for k, v in state.params.items():
        console.print(f"  {k}: {v}")
    return False


@register("/model")
def _cmd_model(agent: Agent, state: SessionState, arg: str) -> bool:
    if not arg:
        current = agent.config.model
        for mid in agent.models():
            marker = " [bold green](current)[/bold green]" if mid == current else ""
            console.print(f"  {mid}{marker}")
        return False
    agent.config.model = arg
    console.print(f"[dim]model = {arg}[/dim]")
    return False


@register("/instructions")
def _cmd_instructions(agent: Agent, state: SessionState, arg: str) -> bool:
    if not arg:
        current = agent.config.instructions
        if current:
            console.print(f"[dim]Current instructions:[/dim]\n{current}")
        else:
            console.print("[dim]No instructions set.[/dim]")
        return False
    agent.config.instructions = arg.strip()
    console.print("[dim]Instructions set.[/dim]")
    return False


@register("/compact")
def _cmd_compact(agent: Agent, state: SessionState, arg: str) -> bool:
    if arg.lower() == "undo":
        restored = agent.undo_compact()
        if restored is None:
            console.print("[dim]Nothing to undo.[/dim]")
        else:
            console.print(f"[dim]Restored {restored} messages from last compact.[/dim]")
        return False
    keep = int(arg) if arg.isdigit() else KEEP_LAST_DEFAULT
    with thinking_progress("Compacting...") as progress:
        progress.add_task("compacting", total=None)
        result = agent.compact(keep)
    if result is None:
        console.print("[dim]Nothing to compact.[/dim]")
        return False
    console.print(f"[dim]Compacted {result.summarized} messages into summary ({result.tokens_used} tokens used).[/dim]")
    return False


@register("/retry")
def _cmd_retry(agent: Agent, state: SessionState, arg: str) -> bool:
    text = agent.pop_last_user()
    if text is None:
        console.print("[dim]Nothing to retry.[/dim]")
        return False
    state.pending_input = text
    preview = text if len(text) <= 80 else text[:77] + "..."
    console.print(f"[dim]Retrying: {preview}[/dim]")
    return False


@register("/edit")
def _cmd_edit(agent: Agent, state: SessionState, arg: str) -> bool:
    text = agent.pop_last_user()
    if text is None:
        console.print("[dim]Nothing to edit.[/dim]")
        return False
    state.prefill = text
    console.print("[dim]Edit previous input (Enter to submit).[/dim]")
    return False


@register("/history")
def _cmd_history(agent: Agent, state: SessionState, arg: str) -> bool:
    n = int(arg) if arg.isdigit() and int(arg) > 0 else HISTORY_PREVIEW_LIMIT
    msgs = agent.messages[-n:]
    if not msgs:
        console.print("[dim]No messages.[/dim]")
        return False
    for m in msgs:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if len(content) > CONTENT_PREVIEW:
            content = content[:CONTENT_PREVIEW] + "..."
        console.print(f"[cyan]{role}[/cyan]:")
        console.print(Markdown(content))
        console.print()
    return False


@register("/export")
def _cmd_export(agent: Agent, state: SessionState, arg: str) -> bool:
    dest = Path(arg).expanduser() if arg else None
    path = export_markdown(agent, state, dest)
    console.print(f"[dim]Exported to {path}[/dim]")
    return False


@register("/diagram")
def _cmd_diagram(agent: Agent, state: SessionState, arg: str) -> bool:
    from .diagram import render as render_diagram

    which = arg.strip().lower() or "all"
    if which not in {"flow", "lifecycle", "all"}:
        console.print(f"[red]Invalid:[/red] {which}. Use: flow, lifecycle, all")
        return False
    render_diagram(which)
    return False
