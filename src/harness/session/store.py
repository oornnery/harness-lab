"""Unified SQLite storage facade delegating to specialized repositories."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec
from pydantic_ai import ModelMessage
from src.memory.schema import MemoryEntry

from .database import DatabaseManager
from .repos import (
    BackgroundJobRepository,
    EventRepository,
    MemoryRepository,
    MessageRepository,
    ScheduledTaskRepository,
    SessionRepository,
    TodoRepository,
)


class UnifiedStore:
    """Facade over SQLite repositories.

    Preserves the legacy UnifiedStore surface used across the harness
    while delegating each concern to a focused repository.
    """

    def __init__(self, root: Path, enable_embeddings: bool = True) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        db_path = self.root / "harness.db"

        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self._init_schema()

        self.enable_embeddings = enable_embeddings

        self.sessions = SessionRepository(self.conn)
        self.messages = MessageRepository(self.conn)
        self.events = EventRepository(self.conn, db_path)
        self.memories = MemoryRepository(self.conn, enable_embeddings)
        self.todos = TodoRepository(self.conn, db_path)
        self.background = BackgroundJobRepository(self.conn, db_path)
        self.scheduled = ScheduledTaskRepository(self.conn, db_path)

    def _init_schema(self) -> None:
        """Initialize all tables using SQLModel metadata and vec0 index."""
        DatabaseManager.create_tables(self.conn)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS vss_memories USING vec0(embedding float(384))"
        )
        self.conn.commit()

    def create_session_id(self) -> str:
        return self.sessions.create_session_id()

    def session_exists(self, session_id: str) -> bool:
        return self.sessions.exists(session_id)

    async def ensure_session(
        self, session_id: str, parent_id: str | None = None
    ) -> tuple[str, str | None]:
        return await self.sessions.ensure(session_id, parent_id)

    async def fork_session(self, session_id: str, child_id: str | None = None) -> str:
        return await self.sessions.fork(session_id, child_id)

    async def list_sessions(self) -> list[tuple[str, str, str]]:
        return await self.sessions.list_all()

    async def load_history(self, session_id: str) -> list[ModelMessage]:
        return await self.messages.load(session_id)

    async def save_history(self, session_id: str, messages: list[ModelMessage]) -> None:
        await self.messages.save(session_id, messages)

    async def append_event(self, session_id: str, payload: dict[str, Any]) -> None:
        await self.events.append(session_id, payload)

    async def read_events(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await self.events.read(session_id, limit)

    async def query_events(
        self,
        session_id: str,
        *,
        kinds: list[str] | None = None,
        tool_name: str | None = None,
        since_ts: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return await self.events.query(
            session_id,
            kinds=kinds,
            tool_name=tool_name,
            since_ts=since_ts,
            limit=limit,
        )

    async def save_memories(self, memories: list[MemoryEntry]) -> None:
        await self.memories.save(memories)

    async def search_memories(
        self, query: str, limit: int = 5, min_confidence: float = 0.5
    ) -> list[MemoryEntry]:
        return await self.memories.search(query, limit, min_confidence)

    async def list_all_memories(self) -> list[MemoryEntry]:
        return await self.memories.list_all()

    async def delete_memory(self, memory_id: int) -> bool:
        return await self.memories.delete(memory_id)

    def history_processor(self, max_messages: int):
        async def _keep_recent(messages: list[ModelMessage]) -> list[ModelMessage]:
            if len(messages) <= max_messages:
                return messages
            return messages[-max_messages:]

        return _keep_recent

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def describe_recent_events_sync(self, session_id: str, limit: int = 5) -> str:
        try:
            events = self.events.read_sync(session_id, limit)
        except Exception:
            return ""
        if not events:
            return ""
        return "\n".join(f"- {event.get('kind')}: {event}" for event in events)
