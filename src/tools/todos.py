"""Per-session todo tools.

Lets the agent capture and track actionable items inside the current
session. Persisted to SQLite via `SessionStore.todos`.
"""

from __future__ import annotations

from typing import Literal

from pydantic_ai import ModelRetry, RunContext

from src.policy import HarnessDeps

Status = Literal["open", "doing", "done", "cancelled"]
Priority = Literal["low", "normal", "high"]


class TodoTools:
    """Add / list / update / delete todos scoped to the current session."""

    async def add_todo(
        self,
        ctx: RunContext[HarnessDeps],
        title: str,
        priority: Priority = "normal",
        notes: str | None = None,
    ) -> str:
        """Create a new todo for the current session.

        Args:
            ctx: Run context.
            title: Short imperative title (e.g. "fix login redirect bug").
            priority: One of low / normal / high.
            notes: Optional longer description.

        Returns:
            Confirmation with the new todo id.
        """
        if not title.strip():
            raise ModelRetry("todo title must not be empty")
        row = await ctx.deps.session_store.todos.add(
            session_id=ctx.deps.session_id,
            title=title.strip(),
            priority=priority,
            notes=notes,
        )
        return f"todo #{row.id} created: {row.title} [{row.priority}]"

    async def list_todos(
        self,
        ctx: RunContext[HarnessDeps],
        status: Status | None = None,
    ) -> str:
        """List todos for the current session, optionally filtered by status.

        Args:
            ctx: Run context.
            status: Optional filter (open / doing / done / cancelled).

        Returns:
            Newline-separated todos, or '(none)'.
        """
        rows = await ctx.deps.session_store.todos.list_for_session(
            session_id=ctx.deps.session_id, status=status
        )
        if not rows:
            return "(none)"
        return "\n".join(
            f"#{r.id} [{r.status}] [{r.priority}] {r.title}"
            + (f"  -- {r.notes}" if r.notes else "")
            for r in rows
        )

    async def update_todo(
        self,
        ctx: RunContext[HarnessDeps],
        todo_id: int,
        status: Status,
    ) -> str:
        """Change the status of an existing todo.

        Args:
            ctx: Run context.
            todo_id: Id returned by `add_todo`.
            status: New status.

        Returns:
            Confirmation or error.
        """
        ok = await ctx.deps.session_store.todos.update_status(todo_id, status)
        if not ok:
            raise ModelRetry(f"todo #{todo_id} not found")
        return f"todo #{todo_id} -> {status}"

    async def delete_todo(self, ctx: RunContext[HarnessDeps], todo_id: int) -> str:
        """Delete a todo permanently."""
        ok = await ctx.deps.session_store.todos.delete(todo_id)
        if not ok:
            raise ModelRetry(f"todo #{todo_id} not found")
        return f"todo #{todo_id} deleted"
