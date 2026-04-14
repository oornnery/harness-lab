"""Memory system wrapper around UnifiedStore."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import RunContext

from .schema import MemoryEntry, MemoryEntryPublic

if TYPE_CHECKING:
    pass


class MemoryStore:
    """Wrapper around UnifiedStore for memory operations.

    Provides backward compatibility while delegating to UnifiedStore.
    """

    def __init__(self, root_path: Path, enable_embeddings: bool = True) -> None:
        from src.session import UnifiedStore

        # root_path can be:
        # - A directory: use directly
        # - A file path: use parent directory (legacy)
        if root_path.suffix:
            # It's a file path, use parent
            root = root_path.parent
            if root.name == ".harness":
                # Avoid nested .harness/.harness
                root = root.parent
        else:
            # It's already a directory
            root = root_path

        self._store = UnifiedStore(root, enable_embeddings=enable_embeddings)

    async def save_memories(self, memories: list[MemoryEntry]) -> None:
        """Save memories to database."""
        await self._store.save_memories(memories)

    async def search_memories(
        self, query: str, limit: int = 5, min_confidence: float = 0.5
    ) -> list[MemoryEntryPublic]:
        """Search memories by semantic similarity."""
        memories = await self._store.search_memories(query, limit, min_confidence)
        # Convert to public format (same structure for now)
        return [
            MemoryEntryPublic(
                id=m.id or 0,  # Should never be None for saved memories
                entity_type=m.entity_type,
                content=m.content,
                confidence=m.confidence,
                session_id=m.session_id,
                extracted_at=m.extracted_at,
            )
            for m in memories
        ]

    async def inject_relevant_memories(self, query: str, ctx: RunContext, limit: int = 3) -> str:
        """Inject relevant memories into agent context."""
        memories = await self.search_memories(query, limit=limit)
        if not memories:
            return ""

        memory_lines = []
        for m in memories:
            memory_lines.append(f"- [{m.entity_type}] {m.content}")

        return "Relevant memories from previous conversations:\n" + "\n".join(memory_lines)

    async def list_all_memories(self) -> list[MemoryEntryPublic]:
        """List all memories."""
        memories = await self._store.list_all_memories()
        return [
            MemoryEntryPublic(
                id=m.id or 0,  # Should never be None for saved memories
                entity_type=m.entity_type,
                content=m.content,
                confidence=m.confidence,
                session_id=m.session_id,
                extracted_at=m.extracted_at,
            )
            for m in memories
        ]

    async def delete_memory(self, memory_id: int) -> bool:
        """Delete a memory by ID."""
        return await self._store.delete_memory(memory_id)

    def close(self) -> None:
        """Close database connection."""
        self._store.close()
