"""Scheduling tools.

Let the model register tasks the harness will fire later (interval or
one-shot). Each fire dispatches via the `BackgroundRunner`, so results
show up in `/jobs` and via `get_job_result`.
"""

from __future__ import annotations

from pydantic_ai import ModelRetry, RunContext
from src.policy import HarnessDeps
from src.scheduler import ScheduleParseError, parse_schedule


class SchedulingTools:
    """Add / list / pause / resume / cancel scheduled tasks."""

    async def schedule_task(
        self,
        ctx: RunContext[HarnessDeps],
        when: str,
        prompt: str,
        persona: str = "AGENTS",
    ) -> str:
        """Register a task to run later.

        Args:
            ctx: Run context.
            when: Schedule spec. Examples: `every 30m`, `every 2h`, `every 45s`,
                `at 14:30`, `at 2026-05-01T09:00`.
            prompt: Instruction the sub-agent receives at fire time.
            persona: Persona to use (default AGENTS).

        Returns:
            `scheduled id=<id> next_run=<iso>`.
        """
        try:
            kind, normalized, first_run = parse_schedule(when)
        except ScheduleParseError as exc:
            raise ModelRetry(str(exc)) from exc

        row = await ctx.deps.session_store.scheduled.add(
            parent_session_id=ctx.deps.session_id,
            kind=kind,
            schedule_value=normalized,
            persona=persona,
            prompt=prompt,
            next_run=first_run,
        )
        return f"scheduled id={row.id} next_run={row.next_run.isoformat()}"

    async def list_scheduled(self, ctx: RunContext[HarnessDeps]) -> str:
        """List scheduled tasks registered from the current parent session."""
        rows = await ctx.deps.session_store.scheduled.list_all(
            parent_session_id=ctx.deps.session_id
        )
        if not rows:
            return "(none)"
        lines = []
        for r in rows:
            state = "on " if r.enabled else "off"
            last = r.last_run.isoformat() if r.last_run else "-"
            lines.append(
                f"#{r.id} [{state}] {r.schedule_value} next={r.next_run.isoformat()} "
                f"last={last} persona={r.persona} :: {r.prompt[:60]}"
            )
        return "\n".join(lines)

    async def pause_scheduled(self, ctx: RunContext[HarnessDeps], task_id: int) -> str:
        """Temporarily disable a scheduled task without deleting it."""
        ok = await ctx.deps.session_store.scheduled.set_enabled(task_id, False)
        if not ok:
            raise ModelRetry(f"scheduled task #{task_id} not found")
        return f"#{task_id} paused"

    async def resume_scheduled(self, ctx: RunContext[HarnessDeps], task_id: int) -> str:
        """Re-enable a previously paused scheduled task."""
        ok = await ctx.deps.session_store.scheduled.set_enabled(task_id, True)
        if not ok:
            raise ModelRetry(f"scheduled task #{task_id} not found")
        return f"#{task_id} resumed"

    async def cancel_scheduled(self, ctx: RunContext[HarnessDeps], task_id: int) -> str:
        """Delete a scheduled task permanently."""
        ok = await ctx.deps.session_store.scheduled.delete(task_id)
        if not ok:
            raise ModelRetry(f"scheduled task #{task_id} not found")
        return f"#{task_id} cancelled"
