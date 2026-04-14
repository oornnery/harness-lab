"""/jobs slash command: monitor background agent runs."""

from __future__ import annotations

from rich.table import Table

from .base import ExtensionState

_STATUS_STYLE = {
    "queued": "yellow",
    "running": "cyan",
    "done": "green",
    "failed": "red",
    "cancelled": "dim",
}


async def jobs_command(state: ExtensionState, arg: str) -> None:
    """`/jobs` (list) | `/jobs show <id>` | `/jobs cancel <id>`."""
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    repo = state.session_store.background

    if not sub or sub == "list":
        rows = await repo.list_recent(parent_session_id=state.session_id, limit=30)
        if not rows:
            state.console.print("[dim]no background jobs in this session.[/]")
            return
        table = Table(title="Background jobs")
        table.add_column("id", style="cyan")
        table.add_column("status")
        table.add_column("persona")
        table.add_column("prompt")
        table.add_column("started", style="dim")
        for r in rows:
            table.add_row(
                r.id,
                f"[{_STATUS_STYLE.get(r.status, 'white')}]{r.status}[/]",
                r.persona,
                r.prompt[:60],
                r.started_at.isoformat(timespec="seconds") if r.started_at else "-",
            )
        state.console.print(table)
        return

    if sub == "show":
        if not rest:
            state.console.print("[red]usage:[/] /jobs show <id>")
            return
        row = await repo.get(rest)
        if row is None:
            state.console.print(f"[red]job not found:[/] {rest}")
            return
        state.console.print(
            f"[bold]{row.id}[/] [{_STATUS_STYLE.get(row.status, 'white')}]{row.status}[/]\n"
            f"persona={row.persona}\n"
            f"created_at={row.created_at.isoformat()}\n"
            f"started_at={row.started_at.isoformat() if row.started_at else '-'}\n"
            f"finished_at={row.finished_at.isoformat() if row.finished_at else '-'}\n\n"
            f"prompt:\n{row.prompt}"
        )
        if row.result_summary:
            state.console.print(f"\n[green]result:[/]\n{row.result_summary}")
        if row.error:
            state.console.print(f"\n[red]error:[/]\n{row.error}")
        return

    if sub == "cancel":
        if state.deps.background_runner is None:
            state.console.print("[red]background runner unavailable.[/]")
            return
        ok = await state.deps.background_runner.cancel(rest)
        state.console.print(
            f"[{'green' if ok else 'red'}]cancel {'ok' if ok else 'failed'}[/] {rest}"
        )
        return

    state.console.print("[red]usage:[/] /jobs | /jobs show <id> | /jobs cancel <id>")
