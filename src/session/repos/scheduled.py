"""ScheduledTask repository."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VALID_KIND = ("interval", "at")


@dataclass
class ScheduledRow:
    id: int
    parent_session_id: str
    kind: str
    schedule_value: str
    persona: str
    prompt: str
    enabled: bool
    last_run: datetime | None
    next_run: datetime
    created_at: datetime


def _row(row: tuple) -> ScheduledRow:
    return ScheduledRow(
        id=row[0],
        parent_session_id=row[1],
        kind=row[2],
        schedule_value=row[3],
        persona=row[4],
        prompt=row[5],
        enabled=bool(row[6]),
        last_run=datetime.fromisoformat(row[7]) if row[7] else None,
        next_run=datetime.fromisoformat(row[8]),
        created_at=datetime.fromisoformat(row[9]),
    )


class ScheduledTaskRepository:
    """CRUD for scheduled tasks."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self.conn = conn
        self.db_path = db_path

    async def add(
        self,
        parent_session_id: str,
        kind: str,
        schedule_value: str,
        persona: str,
        prompt: str,
        next_run: datetime,
    ) -> ScheduledRow:
        if kind not in VALID_KIND:
            raise ValueError(f"invalid kind {kind!r}")
        now = datetime.now().isoformat()

        def _insert() -> int:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO scheduled_tasks
                    (parent_session_id, kind, schedule_value, persona, prompt,
                     enabled, next_run, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        parent_session_id,
                        kind,
                        schedule_value,
                        persona,
                        prompt,
                        next_run.isoformat(),
                        now,
                    ),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()

        new_id = await asyncio.to_thread(_insert)
        return ScheduledRow(
            id=new_id,
            parent_session_id=parent_session_id,
            kind=kind,
            schedule_value=schedule_value,
            persona=persona,
            prompt=prompt,
            enabled=True,
            last_run=None,
            next_run=next_run,
            created_at=datetime.fromisoformat(now),
        )

    async def list_all(self, parent_session_id: str | None = None) -> list[ScheduledRow]:
        def _query() -> list[ScheduledRow]:
            if parent_session_id:
                rows = self.conn.execute(
                    """
                    SELECT id, parent_session_id, kind, schedule_value, persona,
                           prompt, enabled, last_run, next_run, created_at
                    FROM scheduled_tasks WHERE parent_session_id = ?
                    ORDER BY next_run
                    """,
                    (parent_session_id,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT id, parent_session_id, kind, schedule_value, persona,
                           prompt, enabled, last_run, next_run, created_at
                    FROM scheduled_tasks ORDER BY next_run
                    """
                ).fetchall()
            return [_row(r) for r in rows]

        return await asyncio.to_thread(_query)

    async def list_due(self, now: datetime) -> list[ScheduledRow]:
        def _query() -> list[ScheduledRow]:
            rows = self.conn.execute(
                """
                SELECT id, parent_session_id, kind, schedule_value, persona,
                       prompt, enabled, last_run, next_run, created_at
                FROM scheduled_tasks
                WHERE enabled = 1 AND next_run <= ?
                ORDER BY next_run
                """,
                (now.isoformat(),),
            ).fetchall()
            return [_row(r) for r in rows]

        return await asyncio.to_thread(_query)

    async def mark_run(self, task_id: int, next_run: datetime | None) -> None:
        now = datetime.now().isoformat()

        def _update() -> None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                if next_run is None:
                    # one-shot -> disable
                    conn.execute(
                        """
                        UPDATE scheduled_tasks
                        SET last_run = ?, enabled = 0
                        WHERE id = ?
                        """,
                        (now, task_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE scheduled_tasks
                        SET last_run = ?, next_run = ?
                        WHERE id = ?
                        """,
                        (now, next_run.isoformat(), task_id),
                    )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_update)

    async def set_enabled(self, task_id: int, enabled: bool) -> bool:
        def _update() -> bool:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                cur = conn.execute(
                    "UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, task_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_update)

    async def delete(self, task_id: int) -> bool:
        def _delete() -> bool:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                cur = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_delete)
