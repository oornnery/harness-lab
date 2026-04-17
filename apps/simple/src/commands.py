import json
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.table import Table

from .agent import Agent, Thinking
from .session import (
    SessionState,
    list_sessions,
    load_session,
    reset_sessions,
)

Command = Callable[[Console, Agent, SessionState, str], bool]
_REGISTRY: dict[str, Command] = {}

COMMANDS_HELP: list[tuple[str, str]] = [
    ("/help", "show this help"),
    ("/stream", "toggle streaming on/off"),
    ("/thinking [low|medium|high|off]", "toggle reasoning effort"),
    ("/usage", "show session token usage"),
    ("/clear", "clear current history and usage"),
    ("/session [<id>|reset]", "list/load sessions; reset deletes all"),
    ("/set <key> [value]", "set chat param (unset if value omitted)"),
    ("/params", "show current params"),
    ("/model [<id>]", "list models or switch"),
    ("/instructions [<text>]", "show or set system instructions"),
    ("/compact [<keep>]", "summarize history, keep last N"),
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


def register(*names: str) -> Callable[[Command], Command]:
    def deco(fn: Command) -> Command:
        for n in names:
            _REGISTRY[n] = fn
        return fn

    return deco


def dispatch(console: Console, agent: Agent, state: SessionState, user_input: str) -> bool:
    cmd, _, arg = user_input.strip().partition(" ")
    handler = _REGISTRY.get(cmd.lower())
    if handler is None:
        console.print(f"[bold red]Unknown command:[/bold red] {user_input} [dim](type /help)[/dim]")
        return False
    return handler(console, agent, state, arg.strip())


def status_banner(state: SessionState) -> str:
    thinking = state.params.get("reasoning_effort") or "off"
    return f"[dim]stream={'on' if state.stream_mode else 'off'} | thinking={thinking}[/dim]"


@register("/quit", "/exit")
def _cmd_quit(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    return True


@register("/help")
def _cmd_help(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    console.print("[bold]Commands:[/bold]")
    console.print(build_help())
    return False


@register("/stream")
def _cmd_stream(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    state.stream_mode = not state.stream_mode
    console.print(status_banner(state))
    return False


@register("/thinking")
def _cmd_thinking(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    arg = arg.lower()
    if not arg:
        if state.params.pop("reasoning_effort", None) is None:
            state.params["reasoning_effort"] = Thinking.MEDIUM
    elif arg == "off":
        state.params.pop("reasoning_effort", None)
    else:
        try:
            state.params["reasoning_effort"] = Thinking(arg)
        except ValueError:
            levels = ", ".join(t.value for t in Thinking)
            console.print(f"[bold red]Invalid level:[/bold red] {arg}. Use: off, {levels}")
            return False
    console.print(status_banner(state))
    return False


@register("/usage")
def _cmd_usage(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
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
def _cmd_clear(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    agent.reset()
    console.print("[dim]Current session cleared.[/dim]")
    return False


@register("/session")
def _cmd_session(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
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
            console.print(f"  [cyan]{f.stem}[/cyan] [dim]{created}[/dim] turns={data['turns']} -- {preview}")
        return False
    if arg == "reset":
        n = reset_sessions()
        console.print(f"[dim]Deleted {n} sessions.[/dim]")
        return False
    if not load_session(arg, agent, state):
        console.print(f"[red]Session not found:[/red] {arg}")
        return False
    console.print(f"[dim]Loaded session {arg} ({len(agent.messages)} messages)[/dim]")
    return False


@register("/set")
def _cmd_set(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
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
def _cmd_params(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    if not state.params:
        console.print("[dim]No params set (defaults).[/dim]")
        return False
    for k, v in state.params.items():
        console.print(f"  {k}: {v}")
    return False


@register("/model")
def _cmd_model(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
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
def _cmd_instructions(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
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
def _cmd_compact(console: Console, agent: Agent, state: SessionState, arg: str) -> bool:
    keep = int(arg) if arg.isdigit() else KEEP_LAST_DEFAULT
    with console.status("[yellow]Compacting...[/yellow]"):
        result = agent.compact(keep)
    if result is None:
        console.print("[dim]Nothing to compact.[/dim]")
        return False
    console.print(f"[dim]Compacted {result.summarized} messages into summary ({result.tokens_used} tokens used).[/dim]")
    return False
