"""Event storage repository."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any


class EventRepository:
    """Manage event storage on a shared SQLite connection.

    Writes open a thread-local connection because the shared UnifiedStore
    conn is not safe for concurrent writers from `asyncio.to_thread`
    workers (sqlite3 raises `InterfaceError` under contention).
    """

    def __init__(self, conn: sqlite3.Connection, db_path: Path) -> None:
        self.conn = conn
        self.db_path = db_path

    async def append(self, session_id: str, payload: dict[str, Any]) -> None:
        kind = payload.get("kind", "unknown")
        payload_json = json.dumps(payload, default=str)

        def _append() -> None:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=True)
            try:
                conn.execute(
                    """
                    INSERT INTO events (session_id, kind, payload, timestamp)
                    VALUES (?, ?, ?, strftime('%s', 'now'))
                    """,
                    (session_id, kind, payload_json),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_append)

    async def read(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.read_sync, session_id, limit)

    def read_sync(self, session_id: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT kind, payload
            FROM events
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

        events: list[dict[str, Any]] = []
        for kind, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {"_raw": payload_json}
            events.append({"kind": kind, **payload})
        return events

    async def query(
        self,
        session_id: str,
        *,
        kinds: list[str] | None = None,
        tool_name: str | None = None,
        since_ts: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.query_sync,
            session_id,
            kinds=kinds,
            tool_name=tool_name,
            since_ts=since_ts,
            limit=limit,
        )

    def query_sync(
        self,
        session_id: str,
        *,
        kinds: list[str] | None = None,
        tool_name: str | None = None,
        since_ts: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["session_id = ?"]
        params: list[Any] = [session_id]

        if kinds:
            placeholders = ",".join("?" * len(kinds))
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kinds)
        if since_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(since_ts)

        sql = (
            "SELECT kind, payload, timestamp FROM events "
            f"WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?"
        )
        params.append(limit)

        rows = self.conn.execute(sql, params).fetchall()

        events: list[dict[str, Any]] = []
        for kind, payload_json, timestamp in rows:
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                payload = {"_raw": payload_json}
            if tool_name and payload.get("tool") != tool_name:
                continue
            events.append({"kind": kind, "timestamp": timestamp, **payload})
        return events
