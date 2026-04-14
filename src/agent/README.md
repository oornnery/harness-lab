# `src/agent/` -- agent assembly

Everything that turns a persona markdown file into a live
`pydantic_ai.Agent` instance. This is the single composition seam for
the harness -- no other module instantiates `Agent` directly.

## Files

| File                                   | Role                                                                 |
| -------------------------------------- | -------------------------------------------------------------------- |
| [`__init__.py`](__init__.py)           | Re-exports `AgentBuilder`, `AgentHandle`, persona helpers.           |
| [`builder.py`](builder.py)             | `AgentBuilder` -- composes tools, capabilities, history, prefix.     |
| [`personas.py`](personas.py)           | Markdown loaders for personas, rules, instructions, skills.          |
| [`delegation.py`](delegation.py)       | `build_delegation_tools` -- structured persona-to-persona handoff.   |
| [`history.py`](history.py)             | History processors: PII redact, dedupe reads, truncate, summarize.   |
| [`watcher.py`](watcher.py)             | `PersonaWatcher` -- `watchfiles`-powered hot reload of `prompts/`.   |

## Static prefix (built once per agent)

`AgentBuilder._build_static_prefix` concatenates:

1. `# Project` from `prompts/instructions/project.md`
2. `# Rules` from `prompts/rules/*.md` (via `combined_rules_text`)
3. `# Persona: <name>` from the merged `base:` chain
4. `# Workspace` from `WorkspaceContext.prompt_summary()`

Cacheable across turns; the volatile per-turn context is injected via
`@agent.instructions -> render_dynamic(ctx, persona_name)` which
formats `prompts/instructions/_dynamic.md`.

## Loaders (personas.py)

| Function                   | Source                                           |
| -------------------------- | ------------------------------------------------ |
| `load_persona(name)`       | `prompts/agents/<name>.md` (+ recursive `base:`) |
| `load_system_prompt(path)` | `prompts/<path>.md` (helper agents + templates)  |
| `load_instructions(name)`  | `prompts/instructions/<name>.md`                 |
| `load_rule(name)`          | `prompts/rules/<name>.md`                        |
| `combined_rules_text()`    | Concat of every `rules/*.md`                     |
| `load_skill(name)`         | `prompts/skills/<name>/SKILL.md`                 |
| `render_dynamic(ctx, p)`   | Per-turn template (`_dynamic.md`) formatted      |

All loaders are `@cache`d. `clear_persona_cache()` invalidates them --
called by `PersonaWatcher` when `hot_reload_personas=True`.

## History pipeline (history.py)

Runs on every turn before the model sees the transcript:

1. `pii_filter_processor` -- redact common secret patterns.
2. `dedupe_reads_processor` -- drop repeat `read_file` calls in older
   history.
3. (optional) `summarize_old_processor` -- summarize prefix via a
   secondary agent when `SUMMARIZE_MODEL` is set.
4. `adaptive_truncate_processor` -- aggressive trim when token usage
   crosses the soft limit.
5. `truncate_processor` -- final hard cap.

## Delegation (delegation.py)

`build_delegation_tools(builder, persona_meta)` emits one tool per
target listed in the persona's `delegates:` frontmatter. Each tool:

1. Builds a fresh sub-agent in read-only mode.
2. Runs the requested prompt.
3. Returns `{persona, status, answer: FinalAnswer.model_dump()}`.

Children never mutate the parent session; deferred tool requests
short-circuit with `status="deferred"`.
