"""Centralized database schema management."""

from __future__ import annotations

import sqlite3
from typing import ClassVar

from sqlalchemy import create_engine
from sqlmodel import SQLModel

from src.memory.schema import MemoryEntry

from .schema import BackgroundJob, Event, Message, ScheduledTask, SessionModel, Todo


class DatabaseManager:
    """Centralized database management and schema creation."""

    TABLE_MODELS: ClassVar[list] = [
        MemoryEntry,
        Event,
        Message,
        SessionModel,
        Todo,
        BackgroundJob,
        ScheduledTask,
    ]

    @staticmethod
    def register_models() -> None:
        """Register all table models with SQLModel metadata.

        This explicit registration makes it clear what models exist
        and eliminates the need for side-effect imports.
        """
        for model in DatabaseManager.TABLE_MODELS:
            if not hasattr(model, "__tablename__"):
                raise ValueError(f"Model {model.__name__} must have __tablename__ attribute")

    @staticmethod
    def create_tables(conn: sqlite3.Connection) -> None:
        """Create all tables in the database.

        Args:
            conn: SQLite connection to use for table creation
        """
        DatabaseManager.register_models()
        engine = create_engine("sqlite://", creator=lambda: conn)
        SQLModel.metadata.create_all(engine)
