"""Message persistence repository.

Stores pydantic-ai ModelMessage objects losslessly as JSON payloads.
Legacy `role`/`content` columns are preserved for inspection and to
remain readable by pre-refactor rows; new rows still populate them
with a best-effort projection.
"""

from __future__ import annotations

import asyncio
import sqlite3

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

_VALID_ROLES = ("system", "user", "assistant")


def _project_role_content(message: ModelMessage) -> tuple[str, str]:
    """Return best-effort (role, content) for legacy column population."""
    kind = getattr(message, "kind", "")
    role = "user" if kind == "request" else "assistant" if kind == "response" else "system"

    parts = getattr(message, "parts", None) or []
    chunks: list[str] = []
    for part in parts:
        text = getattr(part, "content", None)
        if isinstance(text, str):
            chunks.append(text)
    content = "\n".join(chunks) if chunks else ""
    return role, content


class MessageRepository:
    """Manage message persistence on a shared SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    async def load(self, session_id: str) -> list[ModelMessage]:
        def _load() -> list[ModelMessage]:
            rows = self.conn.execute(
                """
                SELECT role, content, payload
                FROM messages
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
            if not rows:
                return []

            messages: list[ModelMessage] = []
            for role, content, payload in rows:
                if payload:
                    rehydrated = ModelMessagesTypeAdapter.validate_json(payload)
                    messages.extend(rehydrated)
                    continue
                if role in _VALID_ROLES:
                    legacy = ModelMessagesTypeAdapter.validate_python(
                        [{"role": role, "content": content}]
                    )
                    messages.extend(legacy)
            return messages

        return await asyncio.to_thread(_load)

    async def save(self, session_id: str, messages: list[ModelMessage]) -> None:
        def _save() -> None:
            self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            for msg in messages:
                role, content = _project_role_content(msg)
                payload = ModelMessagesTypeAdapter.dump_json([msg]).decode("utf-8")
                self.conn.execute(
                    """
                    INSERT INTO messages (session_id, role, content, payload, timestamp)
                    VALUES (?, ?, ?, ?, strftime('%s', 'now'))
                    """,
                    (session_id, role, content, payload),
                )
            self.conn.commit()

        await asyncio.to_thread(_save)
