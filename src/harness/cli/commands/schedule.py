"""/schedule slash command: manage scheduled agent runs."""

from __future__ import annotations

from rich.table import Table
from src.scheduler import ScheduleParseError, parse_schedule

from .base import ExtensionState


async def schedule_command(state: ExtensionState, arg: str) -> None:
    """`/schedule` (list) | `/schedule add <when> :: <prompt>`.

    Also: `/schedule rm <id>`, `/schedule pause <id>`, `/schedule resume <id>`.
    """
    parts = arg.strip().split(maxsplit=1)
    sub = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""

    repo = state.session_store.scheduled

    if not sub or sub == "list":
        rows = await repo.list_all(parent_session_id=state.session_id)
        if not rows:
            state.console.print("[dim]no scheduled tasks in this session.[/]")
            return
        table = Table(title="Scheduled tasks")
        table.add_column("#", style="cyan", justify="right")
        table.add_column("state")
        table.add_column("when")
        table.add_column("next_run", style="dim")
        table.add_column("persona")
        table.add_column("prompt")
        for r in rows:
            table.add_row(
                str(r.id),
                "[green]on[/]" if r.enabled else "[dim]off[/]",
                r.schedule_value,
                r.next_run.isoformat(timespec="seconds"),
                r.persona,
                r.prompt[:60],
            )
        state.console.print(table)
        return

    if sub == "add":
        if "::" not in rest:
            state.console.print(
                "[red]usage:[/] /schedule add <when> :: <prompt>\n"
                "example: /schedule add every 30m :: check build status"
            )
            return
        when_raw, _, prompt = rest.partition("::")
        try:
            kind, normalized, first_run = parse_schedule(when_raw.strip())
        except ScheduleParseError as exc:
            state.console.print(f"[red]parse error:[/] {exc}")
            return
        row = await repo.add(
            parent_session_id=state.session_id,
            kind=kind,
            schedule_value=normalized,
            persona=state.handle.persona.name if state.handle else "AGENTS",
            prompt=prompt.strip(),
            next_run=first_run,
        )
        state.console.print(
            f"[green]scheduled[/] #{row.id} next_run={row.next_run.isoformat(timespec='seconds')}"
        )
        return

    if sub in {"pause", "resume", "rm"}:
        try:
            task_id = int(rest)
        except ValueError:
            state.console.print(f"[red]usage:[/] /schedule {sub} <id>")
            return
        if sub == "rm":
            ok = await repo.delete(task_id)
            msg = f"#{task_id} removed" if ok else f"#{task_id} not found"
        else:
            ok = await repo.set_enabled(task_id, sub == "resume")
            msg = f"#{task_id} {sub}d" if ok else f"#{task_id} not found"
        state.console.print(f"[{'green' if ok else 'red'}]{msg}[/]")
        return

    state.console.print(
        "[red]usage:[/] /schedule | /schedule add <when> :: <prompt> | "
        "/schedule pause|resume|rm <id>"
    )
