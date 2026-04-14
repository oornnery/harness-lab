"""SQLModel schemas for session storage."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class SessionModel(SQLModel, table=True):
    """Session metadata."""

    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    parent_id: str | None = Field(default=None, foreign_key="sessions.id")
    created_at: datetime


class Message(SQLModel, table=True):
    """Message in conversation history.

    `payload` stores the serialized pydantic-ai ModelMessage JSON (lossless).
    `role`/`content` remain for legacy rows and human inspection.
    """

    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id")
    role: str
    content: str
    payload: str | None = Field(default=None)
    timestamp: int


class Event(SQLModel, table=True):
    """Structured event for debugging/replay."""

    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id")
    kind: str
    payload: str | None = None
    timestamp: int


class Todo(SQLModel, table=True):
    """Actionable item the agent or user tracks within a session."""

    __tablename__ = "todos"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(foreign_key="sessions.id", index=True)
    title: str
    status: str = Field(default="open")  # open | doing | done | cancelled
    priority: str = Field(default="normal")  # low | normal | high
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class BackgroundJob(SQLModel, table=True):
    """Long-running agent task spawned outside the user's REPL."""

    __tablename__ = "background_jobs"

    id: str = Field(primary_key=True)
    parent_session_id: str = Field(foreign_key="sessions.id", index=True)
    persona: str
    prompt: str
    status: str = Field(default="queued")  # queued | running | done | failed | cancelled
    result_summary: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ScheduledTask(SQLModel, table=True):
    """Recurring or one-shot task fired by the in-process scheduler."""

    __tablename__ = "scheduled_tasks"

    id: int | None = Field(default=None, primary_key=True)
    parent_session_id: str = Field(foreign_key="sessions.id", index=True)
    kind: str  # interval | at
    schedule_value: str  # e.g. "30m", "2h", "14:30"
    persona: str
    prompt: str
    enabled: bool = Field(default=True)
    last_run: datetime | None = None
    next_run: datetime
    created_at: datetime
