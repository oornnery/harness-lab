"""Filtered structured event log browser."""

from __future__ import annotations

import time

from rich.table import Table

from .base import ExtensionState


def _parse_args(arg: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for token in arg.strip().split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        if key and value:
            parsed[key.strip()] = value.strip()
    return parsed


async def logs_command(state: ExtensionState, arg: str) -> None:
    """`/logs [kind=K1,K2] [tool=NAME] [since=SEC] [last=N]` — filter session events."""
    opts = _parse_args(arg)

    kinds_raw = opts.get("kind") or opts.get("kinds")
    kinds: list[str] | None = kinds_raw.split(",") if kinds_raw else None
    tool_name = opts.get("tool")

    since_arg = opts.get("since")
    since_ts: int | None = None
    if since_arg:
        try:
            since_ts = int(time.time()) - int(since_arg)
        except ValueError:
            state.console.print("[red]Invalid 'since' (seconds int expected).[/]")
            return

    try:
        limit = int(opts.get("last", "25"))
    except ValueError:
        state.console.print("[red]Invalid 'last' (int expected).[/]")
        return

    events = await state.session_store.query_events(
        state.session_id,
        kinds=kinds,
        tool_name=tool_name,
        since_ts=since_ts,
        limit=limit,
    )

    if not events:
        state.console.print("[dim]No events match filter.[/]")
        return

    table = Table(title=f"Logs — {len(events)} events", show_lines=False)
    table.add_column("ts", style="dim", no_wrap=True)
    table.add_column("kind", style="cyan")
    table.add_column("detail", overflow="fold")

    for event in events:
        ts_raw = event.get("timestamp")
        ts_label = time.strftime("%H:%M:%S", time.localtime(int(ts_raw))) if ts_raw else "-"
        kind = event.get("kind", "?")
        detail_bits: list[str] = []
        for key in ("tool", "error", "summary", "model", "duration_ms"):
            value = event.get(key)
            if value is not None:
                detail_bits.append(f"{key}={value}")
        detail = " ".join(detail_bits) or "-"
        table.add_row(ts_label, kind, detail[:200])

    state.console.print(table)
