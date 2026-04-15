"""Background job persistence."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VALID_STATUS = ("queued", "running", "done", "failed", "cancelled")


@dataclass
class BackgroundJobRow:
    id: str
    parent_session_id: str
    persona: str
    prompt: str
    status: str
    result_summary: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


def _row(row: tuple) -> BackgroundJobRow:
    return BackgroundJobRow(
        id=row[0],
        parent_session_id=row[1],
        persona=row[2],
        prompt=row[3],
        status=row[4],
        result_summary=row[5],
        error=row[6],
        created_at=datetime.fromisoformat(row[7]),
        started_at=datetime.fromisoformat(row[8]) if row[8] else None,
        finished_at=datetime.fromisoformat(row[9]) if row[9] else None,
    )


class BackgroundJobRepository:
    """Persist background job lifecycle to SQLite."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self.conn = conn
        self.db_path = db_path

    async def create(
        self,
        job_id: str,
        parent_session_id: str,
        persona: str,
        prompt: str,
    ) -> BackgroundJobRow:
        now = datetime.now().isoformat()

        def _insert() -> None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                conn.execute(
                    """
                    INSERT INTO background_jobs
                    (id, parent_session_id, persona, prompt, status, created_at)
                    VALUES (?, ?, ?, ?, 'queued', ?)
                    """,
                    (job_id, parent_session_id, persona, prompt, now),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_insert)
        return BackgroundJobRow(
            id=job_id,
            parent_session_id=parent_session_id,
            persona=persona,
            prompt=prompt,
            status="queued",
            result_summary=None,
            error=None,
            created_at=datetime.fromisoformat(now),
            started_at=None,
            finished_at=None,
        )

    async def update_status(
        self,
        job_id: str,
        status: str,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> None:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status {status!r}")
        now = datetime.now().isoformat()
        started = now if status == "running" else None
        finished = now if status in ("done", "failed", "cancelled") else None

        def _update() -> None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                fields = ["status = ?"]
                params: list = [status]
                if started:
                    fields.append("started_at = COALESCE(started_at, ?)")
                    params.append(started)
                if finished:
                    fields.append("finished_at = ?")
                    params.append(finished)
                if result_summary is not None:
                    fields.append("result_summary = ?")
                    params.append(result_summary)
                if error is not None:
                    fields.append("error = ?")
                    params.append(error)
                params.append(job_id)
                conn.execute(
                    f"UPDATE background_jobs SET {', '.join(fields)} WHERE id = ?",
                    tuple(params),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_update)

    async def get(self, job_id: str) -> BackgroundJobRow | None:
        def _query() -> BackgroundJobRow | None:
            row = self.conn.execute(
                """
                SELECT id, parent_session_id, persona, prompt, status,
                       result_summary, error, created_at, started_at, finished_at
                FROM background_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            return _row(row) if row else None

        return await asyncio.to_thread(_query)

    async def list_recent(
        self, parent_session_id: str | None = None, limit: int = 50
    ) -> list[BackgroundJobRow]:
        def _query() -> list[BackgroundJobRow]:
            if parent_session_id:
                rows = self.conn.execute(
                    """
                    SELECT id, parent_session_id, persona, prompt, status,
                           result_summary, error, created_at, started_at, finished_at
                    FROM background_jobs
                    WHERE parent_session_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (parent_session_id, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT id, parent_session_id, persona, prompt, status,
                           result_summary, error, created_at, started_at, finished_at
                    FROM background_jobs
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [_row(r) for r in rows]

        return await asyncio.to_thread(_query)
