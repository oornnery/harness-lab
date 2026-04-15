"""Working-memory tools the model uses to capture and recall short notes."""

from __future__ import annotations

from pydantic_ai import RunContext

from src.policy import HarnessDeps


class NotesTools:
    """Save / query session-scoped notes (in-process, not persisted to disk)."""

    async def save_note(self, ctx: RunContext[HarnessDeps], key: str, content: str) -> str:
        """Capture a short note in working memory.

        Use this to remember user preferences, decisions, or task state
        within the current session. Notes are auto-evicted (FIFO, max 5).
        For permanent cross-session memory, the harness extracts after
        each turn automatically -- you do not call that.

        Args:
            ctx: Run context.
            key: Short label (max 60 chars).
            content: One- or two-sentence note body.

        Returns:
            Confirmation with the stored key.
        """
        ctx.deps.working_memory.save_note(key, content)
        return f"saved note: {key}"

    async def query_notes(self, ctx: RunContext[HarnessDeps], query: str = "") -> str:
        """Return notes from working memory, optionally filtered by substring.

        Args:
            ctx: Run context.
            query: Optional case-insensitive substring filter.

        Returns:
            Newline-separated key/value pairs, or '(none)' if empty.
        """
        notes = ctx.deps.working_memory.query_notes(query)
        if not notes:
            return "(none)"
        return "\n".join(f"{k}: {v}" for k, v in notes.items())
