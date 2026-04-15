"""Semantic memory repository with embeddings."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import datetime
from functools import cached_property
from typing import Any

import sqlite_vec

from src.memory.schema import MemoryEntry

_ST_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Pin revision to guard against upstream supply-chain swaps.
_ST_MODEL_REVISION = "main"


def _escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards so user queries match literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MemoryRepository:
    """Manage memory CRUD and semantic search on a shared SQLite connection."""

    def __init__(self, conn: sqlite3.Connection, enable_embeddings: bool) -> None:
        self.conn = conn
        self.enable_embeddings = enable_embeddings

    @cached_property
    def embedder(self) -> Any | None:
        if not self.enable_embeddings:
            return None
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(_ST_MODEL_NAME, revision=_ST_MODEL_REVISION)

    async def save(self, memories: list[MemoryEntry]) -> None:
        def _save() -> None:
            embedder = self.embedder
            for memory in memories:
                embedding_blob: bytes | None = None
                if embedder is not None:
                    vec = embedder.encode(memory.content).tolist()
                    embedding_blob = sqlite_vec.serialize_float32(vec)

                cursor = self.conn.execute(
                    """
                    INSERT INTO memories
                    (entity_type, content, confidence, session_id, extracted_at, embedding)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory.entity_type,
                        memory.content,
                        memory.confidence,
                        memory.session_id,
                        memory.extracted_at.isoformat(),
                        embedding_blob,
                    ),
                )

                if embedding_blob is not None:
                    with contextlib.suppress(Exception):
                        self.conn.execute(
                            "INSERT INTO vss_memories(rowid, embedding) VALUES (?, ?)",
                            (cursor.lastrowid, embedding_blob),
                        )

            self.conn.commit()

        await asyncio.to_thread(_save)

    async def search(
        self, query: str, limit: int = 5, min_confidence: float = 0.5
    ) -> list[MemoryEntry]:
        def _search() -> list[MemoryEntry]:
            embedder = self.embedder
            if embedder is None:
                escaped = _escape_like(query)
                rows = self.conn.execute(
                    """
                    SELECT id, entity_type, content, confidence, session_id, extracted_at
                    FROM memories
                    WHERE confidence >= ? AND content LIKE ? ESCAPE '\\'
                    ORDER BY confidence DESC
                    LIMIT ?
                    """,
                    (min_confidence, f"%{escaped}%", limit),
                ).fetchall()
                return [self._row_to_memory(r) for r in rows]

            query_vec = embedder.encode(query).tolist()
            query_embedding = sqlite_vec.serialize_float32(query_vec)

            rows = self.conn.execute(
                """
                SELECT m.id, m.entity_type, m.content, m.confidence, m.session_id, m.extracted_at,
                       distance
                FROM vss_memories v
                JOIN memories m ON m.rowid = v.rowid
                WHERE v.embedding MATCH ? AND k = ? AND m.confidence >= ?
                ORDER BY distance
                """,
                (query_embedding, limit, min_confidence),
            ).fetchall()
            return [self._row_to_memory(r) for r in rows]

        return await asyncio.to_thread(_search)

    async def list_all(self) -> list[MemoryEntry]:
        def _list() -> list[MemoryEntry]:
            rows = self.conn.execute(
                """
                SELECT id, entity_type, content, confidence, session_id, extracted_at
                FROM memories
                ORDER BY extracted_at DESC
                """
            ).fetchall()
            return [self._row_to_memory(r) for r in rows]

        return await asyncio.to_thread(_list)

    async def delete(self, memory_id: int) -> bool:
        def _delete() -> bool:
            cursor = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self.conn.commit()
            return cursor.rowcount > 0

        return await asyncio.to_thread(_delete)

    @staticmethod
    def _row_to_memory(row: tuple) -> MemoryEntry:
        id_, entity_type, content, confidence, session_id, extracted_at = row[:6]
        return MemoryEntry(
            id=id_,
            entity_type=entity_type,
            content=content,
            confidence=confidence,
            session_id=session_id,
            extracted_at=datetime.fromisoformat(extracted_at),
        )
