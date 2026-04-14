# `src/memory/` -- long-term memory

Durable, semantic memory that survives across sessions. Lives in the
same sqlite file as the session store, but with its own tables and a
vector index (sqlite-vec, 384-dim MiniLM embeddings).

## Files

| File                           | Role                                                                                                                   |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| [`__init__.py`](__init__.py)   | Re-exports schema + `MemoryStore`.                                                                                     |
| [`schema.py`](schema.py)       | Pydantic models: `MemoryEntry`, `MemoryEntryPublic`, `ExtractedMemories`, `MemoryExtractionRequest`.                   |
| [`store.py`](store.py)         | `MemoryStore` -- CRUD + vector search over `memories` table.                                                           |
| [`extractor.py`](extractor.py) | `MemoryExtractor` -- background pydantic-ai agent that reads recent turn messages and proposes new `MemoryEntry` rows. |

## Pipeline

```text
after_model_request hook
  -> MemoryExtractor.run(messages, max_memories)
       -> ExtractedMemories (pydantic output)
  -> MemoryStore.add_many(entries, embedding=embed(content))

before_model_request hook
  -> MemoryStore.search(query, k) via sqlite-vec cosine
  -> injected into HarnessDeps.retrieved_memories
  -> rendered in _dynamic.md for the next turn
```

## Schema snapshot

- `MemoryEntry` -- full row (id, session_id, kind, content, tags,
  source_message_ids, created_at, embedding).
- `MemoryEntryPublic` -- redacted view handed to the model (no ids,
  no embedding).
- `ExtractedMemories` -- `list[MemoryEntry]` output schema for the
  extractor agent.

## Related

- Extractor prompt: [`src/prompts/agents/memory-extractor.md`](../prompts/agents/memory-extractor.md)
- Extraction template: [`src/prompts/instructions/memory-extract.md`](../prompts/instructions/memory-extract.md)
- Hook wiring: [`src/hooks/hooks.py`](../hooks/hooks.py)
- Agent-facing guidance: [`src/prompts/skills/memory/SKILL.md`](../prompts/skills/memory/SKILL.md)
