"""Session lifecycle commands: session/fork/clear/compact/resume/replay."""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table

from .base import ExtensionState, stringify


async def session_command(state: ExtensionState, _: str) -> None:
    events = await state.session_store.read_events(state.session_id, limit=10)
    body = "\n".join(f"- {event}" for event in events) or "No recent events."
    state.console.print(Panel(body, title=f"Session {state.session_id}"))


async def fork_command(state: ExtensionState, arg: str) -> None:
    child_id = arg.strip() or None
    new_id = await state.session_store.fork_session(state.session_id, child_id=child_id)
    state.deps.session_id = new_id
    await state.session_store.ensure_session(new_id, parent_id=None)
    state.console.print(f"[bold green]Forked session:[/] {new_id}")


async def clear_command(state: ExtensionState, _: str) -> None:
    await state.session_store.save_history(state.session_id, [])
    state.console.print("[bold green]Conversation history cleared.[/]")


async def compact_command(state: ExtensionState, arg: str) -> None:
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


async def resume_command(state: ExtensionState, arg: str) -> None:
    target = arg.strip()
    if not target:
        sessions = await state.session_store.list_sessions()
        if not sessions:
            state.console.print("[dim]No previous sessions found.[/]")
            return
        table = Table(title="Available sessions (most recent first)")
        table.add_column("Session id")
        for sid_tuple in sessions[:20]:
            table.add_row(sid_tuple[0])
        state.console.print(table)
        state.console.print("[dim]Use:[/] /resume <session_id>")
        return
    if not state.session_store.session_exists(target):
        state.console.print(f"[red]Session not found:[/] {target}")
        return
    state.deps.session_id = target
    state.console.print(f"[bold green]Resumed session:[/] {target}")


async def replay_command(state: ExtensionState, _: str) -> None:
    events = await state.session_store.read_events(state.session_id, limit=30)
    body = "\n".join(stringify(event) for event in events) or "No events recorded."
    state.console.print(Panel(body, title="Recent replay log"))
