"""Background agent runner.

Spawns disposable agent runs in `asyncio.Task`s so the user's REPL stays
responsive. Jobs are persisted via `BackgroundJobRepository` so `/jobs`
sees history across restarts. In-flight `asyncio.Task` handles live in
`BackgroundRunner._tasks` (in-memory only).
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.policy import HarnessDeps, RuntimePolicy, WorkingMemory
from src.schema import FinalAnswer

if TYPE_CHECKING:
    from src.agent import AgentBuilder
    from src.session import UnifiedStore
    from src.session.repos.background import BackgroundJobRow


@dataclass
class BackgroundJobHandle:
    """In-memory handle for an in-flight background job."""

    id: str
    task: asyncio.Task[None]


class BackgroundRunner:
    """Spawn and track background agent runs."""

    def __init__(self, builder: AgentBuilder, session_store: UnifiedStore) -> None:
        self.builder = builder
        self.session_store = session_store
        self._tasks: dict[str, BackgroundJobHandle] = {}
        # ids that finished since the last `drain_completed` poll.
        self._completed_since_poll: list[str] = []
        self._lock = asyncio.Lock()

    async def spawn(
        self,
        parent_deps: HarnessDeps,
        prompt: str,
        persona: str = "AGENTS",
    ) -> BackgroundJobRow:
        """Create + start a new background job. Returns immediately."""
        job_id = uuid.uuid4().hex[:10]
        row = await self.session_store.background.create(
            job_id=job_id,
            parent_session_id=parent_deps.session_id,
            persona=persona,
            prompt=prompt,
        )

        task = asyncio.create_task(self._run(job_id, parent_deps, prompt, persona))
        self._tasks[job_id] = BackgroundJobHandle(id=job_id, task=task)
        return row

    async def _run(
        self,
        job_id: str,
        parent_deps: HarnessDeps,
        prompt: str,
        persona: str,
    ) -> None:
        try:
            await self.session_store.background.update_status(job_id, "running")

            sub_session_id = f"bg-{job_id}"
            await self.session_store.ensure_session(
                sub_session_id, parent_id=parent_deps.session_id
            )

            sub_deps = HarnessDeps(
                settings=parent_deps.settings,
                workspace=parent_deps.workspace,
                session_store=self.session_store,
                session_id=sub_session_id,
                policy=RuntimePolicy(parent_deps.settings, parent_deps.workspace.root),
                model_adapter=parent_deps.model_adapter,
                memory_store=parent_deps.memory_store,
                working_memory=WorkingMemory(task=prompt[:300]),
            )

            handle = self.builder.setup(sub_deps, history=[], persona_name=persona)
            result = await handle.agent.run(prompt, deps=sub_deps)

            output = result.output
            summary = (
                output.summary
                if isinstance(output, FinalAnswer)
                else f"[deferred: {type(output).__name__}]"
            )
            await self.session_store.background.update_status(
                job_id, "done", result_summary=summary
            )
        except asyncio.CancelledError:
            await self.session_store.background.update_status(job_id, "cancelled")
            raise
        except Exception as exc:
            await self.session_store.background.update_status(
                job_id, "failed", error=f"{type(exc).__name__}: {exc}"
            )
        finally:
            async with self._lock:
                self._tasks.pop(job_id, None)
                self._completed_since_poll.append(job_id)

    async def cancel(self, job_id: str) -> bool:
        handle = self._tasks.get(job_id)
        if handle is None:
            return False
        handle.task.cancel()
        return True

    async def drain_completed(self) -> list[str]:
        """Return job ids that finished since the last call. Used by REPL banner."""
        async with self._lock:
            ids = list(self._completed_since_poll)
            self._completed_since_poll.clear()
        return ids

    async def shutdown(self) -> None:
        for handle in list(self._tasks.values()):
            handle.task.cancel()
        for handle in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await handle.task
