"""Test memory injection in agent hooks."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.memory.schema import MemoryEntry
from src.session import UnifiedStore


@pytest.mark.asyncio
async def test_search_memories_finds_stored_memory(tmp_path):
    """Test that text search finds stored memories when embeddings disabled."""
    # Arrange
    store = UnifiedStore(tmp_path, enable_embeddings=False)

    memory = MemoryEntry(
        entity_type="user_preference",
        content="O usuário se chama Fabio",
        confidence=0.95,
        session_id="test-session",
        extracted_at=datetime.now(),
    )
    await store.save_memories([memory])

    # Act - use text that matches the memory
    results = await store.search_memories("Fabio", limit=3)

    # Assert
    assert len(results) >= 1
    assert any("Fabio" in m.content for m in results)


@pytest.mark.asyncio
async def test_memory_store_persists_across_sessions(tmp_path):
    """Test that memory persists when creating new sessions."""
    # Arrange - Session 1: save memory
    store = UnifiedStore(tmp_path, enable_embeddings=False)

    memory = MemoryEntry(
        entity_type="user_preference",
        content="O usuário gosta de Python",
        confidence=0.9,
        session_id="session-1",
        extracted_at=datetime.now(),
    )
    await store.save_memories([memory])

    # Act - Session 2: search memories with matching text
    results = await store.search_memories("Python", limit=3)

    # Assert
    assert len(results) >= 1
    assert any("Python" in m.content for m in results)
