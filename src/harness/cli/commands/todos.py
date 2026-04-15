"""Slash commands for session todos."""

from __future__ import annotations

from rich.table import Table

from .base import ExtensionState

_STATUS_STYLE = {
    "open": "yellow",
    "doing": "cyan",
    "done": "green",
    "cancelled": "dim",
}


async def todos_command(state: ExtensionState, arg: str) -> None:
    """`/todos` (list) | `/todos add <title>` | `/todos done <id>` | `/todos rm <id>`."""
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    repo = state.session_store.todos
    sid = state.session_id

    if not sub or sub == "list":
        rows = await repo.list_for_session(sid)
        if not rows:
            state.console.print("[dim]no todos in this session.[/]")
            return
        table = Table(title=f"Todos -- {sid}")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("status")
        table.add_column("priority")
        table.add_column("title")
        for r in rows:
            table.add_row(
                str(r.id),
                f"[{_STATUS_STYLE.get(r.status, 'white')}]{r.status}[/]",
                r.priority,
                r.title,
            )
        state.console.print(table)
        return

    if sub == "add":
        if not rest:
            state.console.print("[red]usage:[/] /todos add <title>")
            return
        row = await repo.add(sid, title=rest)
        state.console.print(f"[green]added[/] #{row.id}: {row.title}")
        return

    if sub in {"done", "doing", "open", "cancelled"}:
        try:
            todo_id = int(rest)
        except ValueError:
            state.console.print(f"[red]usage:[/] /todos {sub} <id>")
            return
        ok = await repo.update_status(todo_id, sub)
        msg = f"#{todo_id} -> {sub}" if ok else f"#{todo_id} not found"
        state.console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return

    if sub == "rm":
        try:
            todo_id = int(rest)
        except ValueError:
            state.console.print("[red]usage:[/] /todos rm <id>")
            return
        ok = await repo.delete(todo_id)
        msg = f"#{todo_id} deleted" if ok else f"#{todo_id} not found"
        state.console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return

    state.console.print(
        "[red]usage:[/] /todos | /todos add <title> | "
        "/todos done|doing|open|cancelled <id> | /todos rm <id>"
    )
