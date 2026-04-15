"""In-process scheduler for recurring / one-shot agent runs.

Runs as an asyncio task that ticks every N seconds. On each tick it
pulls due tasks from `ScheduledTaskRepository` and dispatches them via
`BackgroundRunner.spawn` -- so scheduled runs reuse the exact same
background execution path (persistence, cancellation, /jobs visibility).

Schedule formats supported (user-facing, the parser is intentionally tiny):

- `every 30m`     -> interval, re-fires 30 minutes after last run
- `every 2h`      -> interval, every 2 hours
- `every 45s`     -> interval, every 45 seconds
- `at 14:30`      -> one-shot, fires once at next 14:30 local time,
                     then disables itself
- `at 2026-05-01T09:00`  -> one-shot ISO timestamp

`interval` tasks never disable themselves; `at` tasks disable on first run.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.background import BackgroundRunner
    from src.policy import HarnessDeps
    from src.session import UnifiedStore
    from src.session.repos.scheduled import ScheduledRow

_INTERVAL_RE = re.compile(r"^\s*every\s+(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_AT_HHMM_RE = re.compile(r"^\s*at\s+(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)
_AT_ISO_RE = re.compile(r"^\s*at\s+(\S+)\s*$", re.IGNORECASE)

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ScheduleParseError(ValueError):
    pass


def parse_schedule(spec: str) -> tuple[str, str, datetime]:
    """Return (kind, normalized_value, first_run) for a user schedule spec."""
    if m := _INTERVAL_RE.match(spec):
        count = int(m.group(1))
        unit = m.group(2).lower()
        if count <= 0:
            raise ScheduleParseError("interval must be positive")
        seconds = count * _UNIT_SECONDS[unit]
        first_run = datetime.now() + timedelta(seconds=seconds)
        return "interval", f"every {count}{unit}", first_run

    if m := _AT_HHMM_RE.match(spec):
        hh = int(m.group(1))
        mm = int(m.group(2))
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ScheduleParseError("invalid HH:MM")
        now = datetime.now()
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return "at", f"at {hh:02d}:{mm:02d}", candidate

    if m := _AT_ISO_RE.match(spec):
        raw = m.group(1)
        try:
            when = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ScheduleParseError(f"cannot parse ISO timestamp {raw!r}") from exc
        if when <= datetime.now():
            raise ScheduleParseError("at-timestamp is in the past")
        return "at", f"at {when.isoformat()}", when

    raise ScheduleParseError(
        f"unrecognized schedule {spec!r}. Use 'every Nm|Nh|Ns|Nd' or 'at HH:MM' or 'at ISO'."
    )


def compute_next_interval(value: str) -> datetime | None:
    """For kind='interval', return the next fire time based on the stored value."""
    m = _INTERVAL_RE.match(value)
    if not m:
        return None
    count = int(m.group(1))
    unit = m.group(2).lower()
    return datetime.now() + timedelta(seconds=count * _UNIT_SECONDS[unit])


class Scheduler:
    """Periodic asyncio loop dispatching due scheduled tasks."""

    def __init__(
        self,
        session_store: UnifiedStore,
        background_runner: BackgroundRunner,
        parent_deps_provider,
        tick_seconds: float = 5.0,
    ) -> None:
        self.session_store = session_store
        self.background_runner = background_runner
        self._parent_deps_provider = parent_deps_provider  # callable -> HarnessDeps
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                # Scheduler must survive one bad task.
                await self._tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except TimeoutError:
                continue

    async def _tick(self) -> None:
        now = datetime.now()
        due = await self.session_store.scheduled.list_due(now)
        if not due:
            return
        parent_deps: HarnessDeps = self._parent_deps_provider()
        for task in due:
            await self._dispatch(task, parent_deps)

    async def _dispatch(self, task: ScheduledRow, parent_deps: HarnessDeps) -> None:
        await self.background_runner.spawn(
            parent_deps=parent_deps,
            prompt=task.prompt,
            persona=task.persona,
        )
        next_run = compute_next_interval(task.schedule_value) if task.kind == "interval" else None
        await self.session_store.scheduled.mark_run(task.id, next_run)
