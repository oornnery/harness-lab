"""Memory extraction agent using PydanticAI."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from pydantic_ai import Agent
from pydantic_ai.models import Model
from src.agent.personas import load_system_prompt

from .schema import ExtractedMemories, MemoryEntry


class MemoryExtractor:
    """Extracts structured memories from conversation using PydanticAI."""

    def __init__(self, model: Model | str) -> None:
        self.model = model
        self.agent = cast(
            "Agent[None, ExtractedMemories]",
            Agent(
                name="memory_extractor",
                model=model,
                output_type=ExtractedMemories,
                instructions=load_system_prompt("agents/memory-extractor"),
            ),
        )

    async def extract(
        self, session_id: str, messages: list[str], max_memories: int = 10
    ) -> list[MemoryEntry]:
        messages_text = "\n\n".join(f"[{i}] {msg}" for i, msg in enumerate(messages))
        prompt = load_system_prompt("instructions/memory-extract").format(
            messages=messages_text,
            max_memories=max_memories,
        )

        result = await self.agent.run(prompt)
        extracted = cast(ExtractedMemories | None, result.output)
        if extracted is None:
            return []

        return [
            MemoryEntry(
                entity_type=mem.entity_type,
                content=mem.content,
                confidence=mem.confidence,
                session_id=session_id,
                extracted_at=datetime.now(),
            )
            for mem in extracted.memories
        ]


_extractor: MemoryExtractor | None = None
_extractor_model_id: int | None = None


def get_extractor(model: Model | str) -> MemoryExtractor:
    """Get or create a memory extractor bound to the given model."""
    global _extractor, _extractor_model_id
    model_id = id(model)
    if _extractor is None or _extractor_model_id != model_id:
        _extractor = MemoryExtractor(model)
        _extractor_model_id = model_id
    return _extractor
