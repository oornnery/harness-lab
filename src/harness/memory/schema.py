from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from sqlmodel import Field, SQLModel


class MemoryEntry(SQLModel, table=True):
    """Structured memory extracted from conversations.

    Stored in SQLite for persistent semantic search across sessions.
    """

    __tablename__ = "memories"

    id: int | None = Field(default=None, primary_key=True)
    entity_type: str = Field(index=True)  # user_preference, workspace_fact, decision
    content: str
    confidence: float  # 0-1 score from extraction agent
    session_id: str = Field(index=True)
    extracted_at: datetime
    embedding: bytes | None = None  # sqlite-vec BLOB (384 floats from all-MiniLM-L6-v2)


class MemoryEntryPublic(BaseModel):
    """Memory without embedding BLOB (for APIs and display)."""

    id: int
    entity_type: str
    content: str
    confidence: float
    session_id: str
    extracted_at: datetime

    class Config:
        from_attributes = True


class MemoryExtractionRequest(BaseModel):
    """Request to extract memories from conversation history."""

    session_id: str
    messages: list[str]  # Text content of messages
    max_memories: int = 10
    min_confidence: float = 0.7


class ExtractedMemories(BaseModel):
    """Result from memory extraction agent."""

    memories: list[MemoryEntry]
    reasoning: str
