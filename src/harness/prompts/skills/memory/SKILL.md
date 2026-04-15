# Skill: Memory (when and how to use it)

Load this when you are about to save, retrieve, or forget a memory.
It answers "is this worth remembering, and how should I pull it back
later?"

## Mental model

Memory is a long-term layer under working memory. Working memory is
this turn. Session history is this conversation. Memory is everything
that survives across conversations.

Three ways memory enters a turn:

1. **Auto-injected.** The harness searches your stored memories at
   the start of every turn and prepends the top matches to your
   context as `memories:`. Read them first; they are usually the most
   relevant.
2. **Auto-extracted.** The harness runs a secondary agent at the end
   of each turn to pull durable facts out of the conversation. You do
   not have to decide "should I call save_memory?" by default.
3. **Explicit.** You can call the `save_note` / `query_notes` tools
   when you want control -- e.g. to pin a decision the auto extractor
   might miss.

## What to remember

Save a memory when the fact:

- will be **true in future sessions** (not just this turn)
- is **specific** enough to retrieve by keyword (names, versions,
  decisions, file paths, preferences)
- is **costly to re-derive** (the user told you once, you do not want
  to ask again)

Good examples:

- "Project uses `uv` not `pip`; always run via `uv run`."
- "Test db lives at `tmp/test.db`; fixture in `tests/conftest.py`."
- "User prefers terse reports; no filler openers."
- "ADR: delegation returns `FinalAnswer.model_dump()`, not raw strings."

## What NOT to remember

Skip memories that are:

- **Volatile.** "The build is currently broken on branch X" -- stale
  by next week.
- **Obvious.** "Python is a programming language."
- **Duplicated.** If the context already carries the fact, do not
  re-save it.
- **Secret.** API keys, tokens, credentials. The harness has a PII
  redactor, but do not rely on it.
- **Opinions mid-debate.** Wait until the user accepts the decision.

## How to retrieve

Retrieval is automatic, but when you need a targeted lookup:

- `query_notes(query: str, k: int = 5)` -- vector search over your
  notes. Use concrete keywords, not paraphrases ("pydantic-ai
  capabilities" beats "the thing that configures agents").
- Treat the top-k as hints, not authority. Verify against the current
  workspace before quoting.

## Confidence and trust

Each memory has a confidence score from the extractor. Below
`memory_extraction_threshold` (default 0.7) it is dropped. When you
see a memory you authored, treat its confidence as a soft signal --
if the claim contradicts the live workspace, trust the workspace.

## Forgetting

Use `/memory forget <id>` (user-driven) when a memory is wrong or
stale. Do not try to "overwrite" memories by saving a contradicting
one; delete the old first.

## Red flags that mean "pause and re-check memory"

- You are about to edit a file the user says they already moved.
- You are about to install a package the user already rejected.
- You keep re-asking the same clarifying question across turns.

In each case, `query_notes` for the topic before acting.
