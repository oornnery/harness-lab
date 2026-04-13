from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessage, ModelMessagesTypeAdapter
from pydantic_core import to_json


@dataclass
class SessionMetadata:
    session_id: str
    parent_id: str | None = None


class SessionStore:
    """Disk persistence inspired by pi's session mindset.

    This implementation keeps the full message history on disk and allows simple
    branching by copying the current session into a child session.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create_session_id(self) -> str:
        return uuid.uuid4().hex[:12]

    async def ensure_session(
        self, session_id: str, parent_id: str | None = None
    ) -> SessionMetadata:
        await asyncio.to_thread(self._ensure_session_sync, session_id, parent_id)
        return SessionMetadata(session_id=session_id, parent_id=parent_id)

    def _ensure_session_sync(self, session_id: str, parent_id: str | None = None) -> None:
        directory = self._session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        metadata_path = directory / "session.json"
        if not metadata_path.exists():
            metadata_path.write_text(
                json.dumps({"session_id": session_id, "parent_id": parent_id}, indent=2),
                encoding="utf-8",
            )
        messages_path = directory / "messages.json"
        if not messages_path.exists():
            messages_path.write_text("[]", encoding="utf-8")
        events_path = directory / "events.jsonl"
        events_path.touch(exist_ok=True)

    async def fork_session(self, session_id: str, child_id: str | None = None) -> str:
        child_id = child_id or self.create_session_id()
        await asyncio.to_thread(self._fork_session_sync, session_id, child_id)
        return child_id

    def _fork_session_sync(self, session_id: str, child_id: str) -> None:
        src = self._session_dir(session_id)
        dst = self._session_dir(child_id)
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "messages.json", dst / "messages.json")
        shutil.copy2(src / "events.jsonl", dst / "events.jsonl")
        (dst / "session.json").write_text(
            json.dumps({"session_id": child_id, "parent_id": session_id}, indent=2),
            encoding="utf-8",
        )

    async def load_history(self, session_id: str) -> list[ModelMessage]:
        return await asyncio.to_thread(self._load_history_sync, session_id)

    def _load_history_sync(self, session_id: str) -> list[ModelMessage]:
        path = self._session_dir(session_id) / "messages.json"
        if not path.exists():
            return []
        payload = path.read_bytes()
        if not payload.strip():
            return []
        return ModelMessagesTypeAdapter.validate_json(payload)

    async def save_history(self, session_id: str, messages: list[ModelMessage]) -> None:
        await asyncio.to_thread(self._save_history_sync, session_id, messages)

    def _save_history_sync(self, session_id: str, messages: list[ModelMessage]) -> None:
        path = self._session_dir(session_id) / "messages.json"
        path.write_bytes(to_json(messages))

    async def append_event(self, session_id: str, payload: dict[str, Any]) -> None:
        await asyncio.to_thread(self._append_event_sync, session_id, payload)

    def _append_event_sync(self, session_id: str, payload: dict[str, Any]) -> None:
        path = self._session_dir(session_id) / "events.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")

    async def read_events(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._read_events_sync, session_id, limit)

    def _read_events_sync(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        path = self._session_dir(session_id) / "events.jsonl"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as fh:
            tail = deque(fh, maxlen=limit)
        return [json.loads(line) for line in tail if line.strip()]

    def describe_recent_events_sync(self, session_id: str, limit: int = 5) -> str:
        try:
            events = self._read_events_sync(session_id, limit=limit)
        except Exception:
            return ""
        if not events:
            return ""
        return "\n".join(f"- {event.get('kind')}: {event}" for event in events)

    def list_sessions(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            (p.name for p in self.root.iterdir() if p.is_dir() and (p / "messages.json").exists()),
            key=lambda name: (self.root / name / "messages.json").stat().st_mtime,
            reverse=True,
        )

    def session_exists(self, session_id: str) -> bool:
        return (self._session_dir(session_id) / "messages.json").exists()

    def history_processor(self, max_messages: int):
        async def _keep_recent(messages: list[ModelMessage]) -> list[ModelMessage]:
            if len(messages) <= max_messages:
                return messages
            return messages[-max_messages:]

        return _keep_recent

    def _session_dir(self, session_id: str) -> Path:
        return self.root / session_id
