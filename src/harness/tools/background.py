"""Background agent tools.

Let the model spawn agent runs that execute outside the user's REPL,
poll their status, read results, and cancel mid-flight. Useful for
long shell commands, multi-file refactors, or research that takes
minutes.
"""

from __future__ import annotations

from pydantic_ai import ModelRetry, RunContext
from src.policy import HarnessDeps


def _format_job_line(row: object) -> str:
    status = getattr(row, "status", "?")
    jid = getattr(row, "id", "?")
    persona = getattr(row, "persona", "?")
    prompt = getattr(row, "prompt", "")[:60]
    return f"{jid} [{status}] {persona}: {prompt}"


class BackgroundTools:
    """Spawn / inspect / cancel background agent jobs."""

    async def spawn_background(
        self,
        ctx: RunContext[HarnessDeps],
        prompt: str,
        persona: str = "AGENTS",
    ) -> str:
        """Start an agent run in the background.

        Returns immediately with the new job id. Use `get_job_status`
        or `get_job_result` to poll it. The background agent runs in a
        fresh child session with its own working memory.

        Args:
            ctx: Run context.
            prompt: User-style instruction for the sub-agent.
            persona: Persona to use (default AGENTS).

        Returns:
            `spawned job_id=<id>` confirmation.
        """
        runner = ctx.deps.background_runner
        if runner is None:
            raise ModelRetry("background runner not available in this context")
        row = await runner.spawn(ctx.deps, prompt=prompt, persona=persona)
        return f"spawned job_id={row.id}"

    async def list_background_jobs(
        self,
        ctx: RunContext[HarnessDeps],
        status: str | None = None,
    ) -> str:
        """List recent background jobs from this parent session.

        Args:
            ctx: Run context.
            status: Optional filter: queued / running / done / failed / cancelled.
        """
        rows = await ctx.deps.session_store.background.list_recent(
            parent_session_id=ctx.deps.session_id, limit=50
        )
        if status:
            rows = [r for r in rows if r.status == status]
        if not rows:
            return "(none)"
        return "\n".join(_format_job_line(r) for r in rows)

    async def get_job_status(self, ctx: RunContext[HarnessDeps], job_id: str) -> str:
        """Return the current status of a background job."""
        row = await ctx.deps.session_store.background.get(job_id)
        if row is None:
            raise ModelRetry(f"job {job_id} not found")
        return (
            f"id={row.id} status={row.status} persona={row.persona}\n"
            f"created_at={row.created_at.isoformat()}\n"
            f"started_at={row.started_at.isoformat() if row.started_at else '-'}\n"
            f"finished_at={row.finished_at.isoformat() if row.finished_at else '-'}"
        )

    async def get_job_result(self, ctx: RunContext[HarnessDeps], job_id: str) -> str:
        """Return the final summary (done) or error (failed) for a job."""
        row = await ctx.deps.session_store.background.get(job_id)
        if row is None:
            raise ModelRetry(f"job {job_id} not found")
        if row.status == "done":
            return row.result_summary or "(no summary)"
        if row.status == "failed":
            return f"[failed] {row.error or '(no error)'}"
        return f"[{row.status}] not finished yet"

    async def cancel_job(self, ctx: RunContext[HarnessDeps], job_id: str) -> str:
        """Cancel a running background job."""
        runner = ctx.deps.background_runner
        if runner is None:
            raise ModelRetry("background runner not available in this context")
        ok = await runner.cancel(job_id)
        return f"cancel {'ok' if ok else 'failed: not running'} for {job_id}"
