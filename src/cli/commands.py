from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import BinaryContent, DocumentUrl, ImageUrl, UsageLimits
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..agent import list_personas, load_persona
from ..policy import HarnessDeps
from ..sessions import SessionStore

if TYPE_CHECKING:
    from ..agent import AgentBuilder, AgentHandle
    from .renderer import StreamRenderer

CommandHandler = Callable[["ExtensionState", str], Awaitable[None]]


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
    session_store: SessionStore
    session_id: str
    known_tools: list[str]
    workspace_summary: str
    handle: AgentHandle | None = None
    builder: AgentBuilder | None = None
    renderer: StreamRenderer | None = None
    pending_attachments: list[Any] = field(default_factory=list)


async def _help_command(state: ExtensionState, _: str) -> None:
    table = Table(title="Slash commands")
    table.add_column("Command")
    table.add_column("Description")
    for extension in default_extensions():
        for command in extension.commands:
            table.add_row(f"/{command.name}", command.help_text)
    state.console.print(table)


async def _context_command(state: ExtensionState, _: str) -> None:
    state.console.print(Panel(state.workspace_summary, title="Workspace context"))


async def _tools_command(state: ExtensionState, _: str) -> None:
    table = Table(title="Known tools")
    table.add_column("Tool")
    for tool in state.known_tools:
        table.add_row(tool)
    state.console.print(table)


async def _session_command(state: ExtensionState, _: str) -> None:
    events = await state.session_store.read_events(state.session_id, limit=10)
    body = "\n".join(f"- {event}" for event in events) or "No recent events."
    state.console.print(Panel(body, title=f"Session {state.session_id}"))


async def _fork_command(state: ExtensionState, arg: str) -> None:
    child_id = arg.strip() or None
    new_id = await state.session_store.fork_session(state.session_id, child_id=child_id)
    state.session_id = new_id
    state.deps.session_id = new_id
    await state.session_store.ensure_session(new_id, parent_id=None)
    state.console.print(f"[bold green]Forked session:[/] {new_id}")


async def _clear_command(state: ExtensionState, _: str) -> None:
    await state.session_store.save_history(state.session_id, [])
    state.console.print("[bold green]Conversation history cleared.[/]")


async def _compact_command(state: ExtensionState, arg: str) -> None:
    try:
        keep = (
            int(arg.strip())
            if arg.strip()
            else max(4, state.deps.settings.max_history_messages // 2)
        )
    except ValueError:
        state.console.print("[red]Usage:[/] /compact [keep_last_n]")
        return
    history = await state.session_store.load_history(state.session_id)
    if len(history) <= keep:
        state.console.print(f"[dim]Nothing to compact ({len(history)} messages).[/]")
        return
    compacted = history[-keep:]
    await state.session_store.save_history(state.session_id, compacted)
    state.console.print(f"[bold green]Compacted[/] {len(history)} → {len(compacted)} messages.")


async def _resume_command(state: ExtensionState, arg: str) -> None:
    target = arg.strip()
    if not target:
        sessions = state.session_store.list_sessions()
        if not sessions:
            state.console.print("[dim]No previous sessions found.[/]")
            return
        table = Table(title="Available sessions (most recent first)")
        table.add_column("Session id")
        for sid in sessions[:20]:
            table.add_row(sid)
        state.console.print(table)
        state.console.print("[dim]Use:[/] /resume <session_id>")
        return
    if not state.session_store.session_exists(target):
        state.console.print(f"[red]Session not found:[/] {target}")
        return
    state.session_id = target
    state.deps.session_id = target
    state.console.print(f"[bold green]Resumed session:[/] {target}")


async def _replay_command(state: ExtensionState, _: str) -> None:
    events = await state.session_store.read_events(state.session_id, limit=30)
    body = "\n".join(stringify(event) for event in events) or "No events recorded."
    state.console.print(Panel(body, title="Recent replay log"))


async def _agent_command(state: ExtensionState, arg: str) -> None:
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


_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_DOC_EXT = {".pdf", ".txt", ".md", ".html", ".csv"}


async def _attach_command(state: ExtensionState, arg: str) -> None:
    target = arg.strip()
    if not target:
        if not state.pending_attachments:
            state.console.print("[dim]no pending attachments.[/]")
            return
        state.console.print(f"[green]{len(state.pending_attachments)} pending attachment(s)[/]")
        return
    if target.lower().startswith(("http://", "https://")):
        ext = Path(target.split("?")[0]).suffix.lower()
        if ext in _IMAGE_EXT:
            state.pending_attachments.append(ImageUrl(url=target))
        else:
            state.pending_attachments.append(DocumentUrl(url=target))
        state.console.print(f"[green]attached URL:[/] {target}")
        return
    path = Path(target).expanduser()
    if not path.exists() or not path.is_file():
        state.console.print(f"[red]file not found:[/] {target}")
        return
    ext = path.suffix.lower()
    if ext in _IMAGE_EXT:
        media_type = f"image/{ext.lstrip('.').replace('jpg', 'jpeg')}"
    elif ext == ".pdf":
        media_type = "application/pdf"
    elif ext in _DOC_EXT:
        media_type = "text/plain"
    else:
        state.console.print(f"[red]unsupported extension:[/] {ext}")
        return
    state.pending_attachments.append(BinaryContent(data=path.read_bytes(), media_type=media_type))
    state.console.print(f"[green]attached file:[/] {path} ({media_type})")


async def _step_command(state: ExtensionState, arg: str) -> None:
    from .step import StepRunner

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


_BUDGETS = {
    "cheap": UsageLimits(request_limit=20, total_tokens_limit=50_000),
    "rich": UsageLimits(request_limit=100, total_tokens_limit=500_000),
    "none": None,
}


async def _mode_command(state: ExtensionState, arg: str) -> None:
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


def stringify(value: Any) -> str:
    return str(value)


def default_extensions() -> list[HarnessExtension]:
    inspector = HarnessExtension(
        name="inspector",
        description="PI-style CLI inspection commands.",
        commands=[
            CommandSpec("help", "Show slash commands.", _help_command),
            CommandSpec("context", "Show the current workspace context.", _context_command),
            CommandSpec("tools", "List known tools.", _tools_command),
            CommandSpec(
                "session", "Show current session info and recent events.", _session_command
            ),
            CommandSpec(
                "fork", "Fork the current conversation into a child session.", _fork_command
            ),
            CommandSpec("replay", "Replay recent structured session events.", _replay_command),
            CommandSpec(
                "clear", "Clear conversation history for the current session.", _clear_command
            ),
            CommandSpec(
                "compact",
                "Compact history, keeping last N messages (default: half of max).",
                _compact_command,
            ),
            CommandSpec(
                "resume",
                "Resume a previous session. No arg lists available sessions.",
                _resume_command,
            ),
            CommandSpec(
                "agent",
                "Switch persona. No arg lists available personas.",
                _agent_command,
            ),
            CommandSpec(
                "mode",
                "Show or toggle mode (readonly|manual|auto|never|budget=...).",
                _mode_command,
            ),
            CommandSpec(
                "attach",
                "Attach a file or URL to the next turn.",
                _attach_command,
            ),
            CommandSpec(
                "step",
                "Run a prompt step-by-step via agent.iter (debug).",
                _step_command,
            ),
        ],
    )
    return [inspector]


def build_command_index() -> dict[str, CommandSpec]:
    index: dict[str, CommandSpec] = {}
    for extension in default_extensions():
        for command in extension.commands:
            index[command.name] = command
    return index
