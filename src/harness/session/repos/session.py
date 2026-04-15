"""Session CRUD repository."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid


class SessionRepository:
    """Manage session CRUD operations on a shared SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create_session_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def exists(self, session_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None

    async def ensure(self, session_id: str, parent_id: str | None = None) -> tuple[str, str | None]:
        def _ensure() -> tuple[str, str | None]:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO sessions (id, parent_id, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (session_id, parent_id),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT parent_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return session_id, row[0] if row else None

        return await asyncio.to_thread(_ensure)

    async def fork(self, session_id: str, child_id: str | None = None) -> str:
        child_id = child_id or self.create_session_id()

        def _fork() -> str:
            self.conn.execute(
                """
                INSERT INTO messages (session_id, role, content, timestamp)
                SELECT ?, role, content, strftime('%s', 'now')
                FROM messages
                WHERE session_id = ?
                ORDER BY id
                """,
                (child_id, session_id),
            )
            self.conn.execute(
                """
                INSERT INTO events (session_id, kind, payload, timestamp)
                SELECT ?, kind, payload, strftime('%s', 'now')
                FROM events
                WHERE session_id = ?
                ORDER BY id
                """,
                (child_id, session_id),
            )
            self.conn.execute(
                """
                INSERT INTO sessions (id, parent_id, created_at)
                VALUES (?, ?, datetime('now'))
                """,
                (child_id, session_id),
            )
            self.conn.commit()
            return child_id

        return await asyncio.to_thread(_fork)

    async def list_all(self) -> list[tuple[str, str, str]]:
        def _list() -> list[tuple[str, str, str]]:
            rows = self.conn.execute(
                """
                SELECT s.id, s.parent_id, s.created_at,
                       COALESCE(MAX(m.timestamp), 0) as last_message
                FROM sessions s
                LEFT JOIN messages m ON s.id = m.session_id
                GROUP BY s.id
                ORDER BY last_message DESC
                """
            ).fetchall()
            return [(r[0], r[1] or "", r[2]) for r in rows]

        return await asyncio.to_thread(_list)
