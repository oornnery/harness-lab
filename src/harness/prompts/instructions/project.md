# harness-lab -- Project Instructions

Pydantic-AI 1.x agent harness / lab. CLI-first, sqlite-backed session
store, persona-driven delegation, memory retrieval via sqlite-vec.

## Stack

- Python 3.12+, `uv` for deps, `ruff`/`ty`/`rumdl` for lint, `pytest -q`.
- Runtime: `pydantic-ai==1.*`, `rich`, `watchfiles`, `sqlite-vec`,
  `sentence-transformers` (all-MiniLM-L6-v2, 384 dim).
- Storage: local SQLite (`session_store.db`) with tables for events,
  todos, messages, memories, and vss_memory (vec0 384).

## Architecture

- `src/agent/builder.py` -- composes `Agent` with persona prompt,
  toolsets (MCP wrapped in `PrefixedToolset`), capabilities (`Hooks`,
  history processors), usage limits.
- `src/agent/personas.py` -- loads markdown personas with frontmatter
  `base:` chain. `load_system_prompt()` for `system/` helpers.
- `src/agent/delegation.py` -- structured cross-persona handoff,
  returns `{persona, status, answer: FinalAnswer.model_dump()}`.
- `src/agent/history.py` -- processors: pii filter, dedupe reads,
  adaptive truncate, summarize-old.
- `src/hooks/hooks.py` -- runtime hooks: event stream mirror, tool
  timing, prepare_tools read-only filter, memory injection/extraction.
- `src/session/` -- sqlite repositories (events, todos, memory,
  messages) behind a single `SessionStore` facade.
- `src/memory/` -- extractor agent, schema, retrieval.
- `src/cli/` -- `HarnessCliApp`, `TurnRunner` (run_stream_events loop),
  `StreamRenderer` (Rich), slash commands in `commands/`.
- `src/tools/` -- file read/write/edit, shell, memory tools tagged
  with `category` metadata for read-only gating.

## Non-negotiables

- Never inline prompt strings in `.py`. Author markdown under
  `src/prompts/` and load via `load_persona` or `load_system_prompt`.
- Never bypass `SessionStore` for direct sqlite access from app code.
- Deps in `HarnessDeps`; never global mutable state.
- Hooks mutate events, capabilities mutate runtime -- keep them apart.
- Read-only mode must filter mutating tools via `prepare_tools`.
- All new features ship with a pytest case; 35+/35 must stay green.

## Validation gate (run in order)

```bash
uv run ruff format --check .
uv run ruff check .
uv run rumdl check .
uv run ty check
uv run pytest -q
```
