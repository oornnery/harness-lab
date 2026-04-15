"""Memory inspection and deletion commands."""

from __future__ import annotations

from rich.table import Table

from .base import ExtensionState


async def memory_command(state: ExtensionState, arg: str) -> None:
    if state.deps.memory_store is None:
        state.console.print("[red]Memory is disabled.[/]")
        return

    memory_store = state.deps.memory_store
    arg = arg.strip()

    if not arg:
        memories = await memory_store.list_all_memories()
        if not memories:
            state.console.print("[dim]No memories stored yet.[/]")
            return

        table = Table(title="Stored Memories")
        table.add_column("ID", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Content", style="white")
        table.add_column("Confidence", style="yellow")
        table.add_column("Session", style="dim")

        for m in memories[:20]:
            conf_pct = f"{m.confidence:.0%}"
            table.add_row(str(m.id), m.entity_type, m.content[:60], conf_pct, m.session_id[:8])

        state.console.print(table)
        return

    if arg.startswith("forget "):
        memory_id_str = arg[7:].strip()
        try:
            memory_id = int(memory_id_str)
        except ValueError:
            state.console.print(f"[red]Invalid memory ID:[/] {memory_id_str}")
            return

        deleted = await memory_store.delete_memory(memory_id)
        if deleted:
            state.console.print(f"[green]Deleted memory:[/] {memory_id}")
        else:
            state.console.print(f"[red]Memory not found:[/] {memory_id}")
        return

    state.console.print("[red]Usage:[/] /memory or /forget <id>")


async def forget_command(state: ExtensionState, arg: str) -> None:
    await memory_command(state, f"forget {arg}")
