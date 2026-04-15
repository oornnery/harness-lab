"""Todo repository for per-session actionable items."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VALID_STATUS = ("open", "doing", "done", "cancelled")
VALID_PRIORITY = ("low", "normal", "high")


@dataclass
class TodoRow:
    id: int
    session_id: str
    title: str
    status: str
    priority: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


def _row_to_todo(row: tuple) -> TodoRow:
    return TodoRow(
        id=row[0],
        session_id=row[1],
        title=row[2],
        status=row[3],
        priority=row[4],
        notes=row[5],
        created_at=datetime.fromisoformat(row[6]),
        updated_at=datetime.fromisoformat(row[7]),
        completed_at=datetime.fromisoformat(row[8]) if row[8] else None,
    )


class TodoRepository:
    """CRUD for per-session todos. Writes use a thread-local conn."""

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self.conn = conn
        self.db_path = db_path

    async def add(
        self,
        session_id: str,
        title: str,
        priority: str = "normal",
        notes: str | None = None,
    ) -> TodoRow:
        if priority not in VALID_PRIORITY:
            raise ValueError(f"invalid priority {priority!r}")
        now = datetime.now().isoformat()

        def _insert() -> int:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO todos
                    (session_id, title, status, priority, notes, created_at, updated_at)
                    VALUES (?, ?, 'open', ?, ?, ?, ?)
                    """,
                    (session_id, title, priority, notes, now, now),
                )
                conn.commit()
                return int(cur.lastrowid or 0)
            finally:
                conn.close()

        new_id = await asyncio.to_thread(_insert)
        return TodoRow(
            id=new_id,
            session_id=session_id,
            title=title,
            status="open",
            priority=priority,
            notes=notes,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            completed_at=None,
        )

    async def list_for_session(self, session_id: str, status: str | None = None) -> list[TodoRow]:
        def _query() -> list[TodoRow]:
            sql = (
                "SELECT id, session_id, title, status, priority, notes, "
                "created_at, updated_at, completed_at FROM todos WHERE session_id = ?"
            )
            params: tuple = (session_id,)
            if status:
                sql += " AND status = ?"
                params = (session_id, status)
            sql += " ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, id"
            rows = self.conn.execute(sql, params).fetchall()
            return [_row_to_todo(r) for r in rows]

        return await asyncio.to_thread(_query)

    async def update_status(self, todo_id: int, status: str) -> bool:
        if status not in VALID_STATUS:
            raise ValueError(f"invalid status {status!r}")
        now = datetime.now().isoformat()
        completed = now if status in ("done", "cancelled") else None

        def _update() -> bool:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                cur = conn.execute(
                    """
                    UPDATE todos
                    SET status = ?, updated_at = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (status, now, completed, todo_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_update)

    async def delete(self, todo_id: int) -> bool:
        def _delete() -> bool:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

        return await asyncio.to_thread(_delete)

    def count_open_sync(self, session_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM todos WHERE session_id = ? AND status IN ('open','doing')",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row else 0
