"""Misc commands: help, context, tools, attach."""

from __future__ import annotations

from pathlib import Path

from pydantic_ai import BinaryContent, DocumentUrl, ImageUrl
from rich.panel import Panel
from rich.table import Table

from .base import MAX_PENDING_ATTACHMENTS, ExtensionState

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_DOC_EXT = {".pdf", ".txt", ".md", ".html", ".csv"}


async def help_command(state: ExtensionState, _: str) -> None:
    from .registry import default_extensions

    table = Table(title="Slash commands")
    table.add_column("Command")
    table.add_column("Description")
    for extension in default_extensions():
        for command in extension.commands:
            table.add_row(f"/{command.name}", command.help_text)
    state.console.print(table)


async def context_command(state: ExtensionState, _: str) -> None:
    state.console.print(Panel(state.workspace_summary, title="Workspace context"))


async def tools_command(state: ExtensionState, _: str) -> None:
    table = Table(title="Known tools")
    table.add_column("Tool")
    for tool in state.known_tools:
        table.add_row(tool)
    state.console.print(table)


def _resolve_within_workspace(target: str, root: Path) -> Path | None:
    """Resolve target to an absolute path inside `root`, or return None."""
    candidate = Path(target).expanduser()
    candidate = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


async def attach_command(state: ExtensionState, arg: str) -> None:
    target = arg.strip()
    if not target:
        if not state.pending_attachments:
            state.console.print("[dim]no pending attachments.[/]")
            return
        state.console.print(f"[green]{len(state.pending_attachments)} pending attachment(s)[/]")
        return

    if len(state.pending_attachments) >= MAX_PENDING_ATTACHMENTS:
        state.console.print(
            f"[red]attachment limit reached ({MAX_PENDING_ATTACHMENTS}). "
            "send current turn or clear first.[/]"
        )
        return

    if target.lower().startswith(("http://", "https://")):
        ext = Path(target.split("?")[0]).suffix.lower()
        if ext in _IMAGE_EXT:
            state.pending_attachments.append(ImageUrl(url=target))
        else:
            state.pending_attachments.append(DocumentUrl(url=target))
        state.console.print(f"[green]attached URL:[/] {target}")
        return

    path = _resolve_within_workspace(target, state.deps.workspace.root)
    if path is None:
        state.console.print(f"[red]path outside workspace rejected:[/] {target}")
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

    try:
        data = path.read_bytes()
    except OSError as exc:
        state.console.print(f"[red]cannot read file:[/] {target} ({exc})")
        return

    state.pending_attachments.append(BinaryContent(data=data, media_type=media_type))
    state.console.print(f"[green]attached file:[/] {path} ({media_type})")
