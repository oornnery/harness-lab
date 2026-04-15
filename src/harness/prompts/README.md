# `src/prompts/` -- markdown prompt source

All agent-facing text lives here as plain markdown. Python code never
inlines prompt strings; it loads from this tree via
[`src/agent/personas.py`](../agent/personas.py).

## Layout

```text
src/prompts/
  agents/                  # Personas + helper agents
    AGENTS.md              # Base persona (parent of coder/planner/reviewer)
    coder.md
    planner.md
    reviewer.md
    memory-extractor.md    # Helper agent: memory extraction
    history-summarizer.md  # Helper agent: history compaction
  instructions/            # Project context + dynamic templates
    project.md             # Injected once into the static prefix
    _dynamic.md            # Per-turn template (render_dynamic)
    memory-extract.md      # Prompt template with {messages}, {max_memories}
  rules/                   # Always-on rules, concatenated into the system prompt
    persona.md
    safety.md
    output.md
    tools.md
  skills/                  # On-demand knowledge modules (load via tool)
    harness/SKILL.md
    memory/SKILL.md
    sessions/SKILL.md
  README.md                # This file
```

## Runtime wiring

| Layer        | When used                          | How loaded                                                         |
| ------------ | ---------------------------------- | ------------------------------------------------------------------ |
| Personas     | Every agent build                  | `load_persona(name)` -> `agents/<name>.md` (with `base:` chain)    |
| Helpers      | Sub-agents (extractor, summarizer) | `load_system_prompt("agents/<name>")`                              |
| Instructions | Static prefix (project context)    | `load_instructions("project")` from `builder._build_static_prefix` |
| Templates    | Prompt formatting                  | `load_system_prompt("instructions/<name>")`                        |
| Dynamic      | Per-turn injection                 | `render_dynamic(ctx, persona_name)` from `@agent.instructions`     |
| Rules        | Static prefix (always-on)          | `combined_rules_text()` concatenates every `rules/*.md`            |
| Skills       | On-demand via tool                 | `list_skills()` + `load_skill(name)` exposed as harness tools      |

## Static prefix order (`builder._build_static_prefix`)

```text
1. # Project           <- instructions/project.md
2. # Rules             <- rules/*.md concatenated
3. # Persona: <name>   <- merged base chain from agents/
4. # Workspace         <- WorkspaceContext.prompt_summary()
```

Per-turn volatile context (session id, recent events, retrieved
memories, working memory, open todos) is layered on top via
[`instructions/_dynamic.md`](instructions/_dynamic.md).

## Purpose of each layer

- `agents/` -- **who** the agent is. Personas declare voice, default
  mode, allowed delegates. Helper agents (extractor, summarizer) have
  their own, narrower role.
- `instructions/` -- **what** the project is. Stable context about
  stack, conventions, validation gate. Plus the per-turn dynamic
  template that renders live state.
- `rules/` -- **how** to behave. Always-on discipline (persona,
  safety, output, tools). Short, numbered, enforceable.
- `skills/` -- **deep knowledge** loaded on demand. Not in the static
  prefix; the agent pulls a skill only when a task needs it. Three
  skills orient the agent to the harness itself:
  - `harness` -- what the harness is and how a turn runs.
  - `memory` -- when and how to use long-term memory.
  - `sessions` -- when and how to use the session store.

## Layer mapping (harness <-> Claude Code)

| Layer        | Harness path                              | Claude Code path            |
| ------------ | ----------------------------------------- | --------------------------- |
| Agents       | `src/prompts/agents/*.md`                 | `.claude/agents/*.md`       |
| Rules        | `src/prompts/rules/*.md`                  | `.claude/rules/*.md`        |
| Skills       | `src/prompts/skills/*/SKILL.md`           | `.claude/skills/*/SKILL.md` |
| Instructions | `src/prompts/instructions/project.md`     | `.claude/CLAUDE.md`         |
| Hooks        | `src/hooks/` (pydantic-ai runtime)        | `.claude/hooks/*.sh`        |
| MCP          | `MCP_CONFIG_PATH` env (runtime)           | `.claude/mcp_servers*.json` |
| Plugins      | pydantic-ai capabilities / toolsets       | `.claude/plugins/` (unused) |

Everything under `src/prompts/` is harness-specific, authored for this
project. Claude Code's `.claude/` layout covers global IDE workflows;
harness personas, rules, and skills are narrower.

## Authoring rules

- Never inline prompt strings in `.py`. Author markdown here and use
  the appropriate loader.
- `agents/*.md` MUST have frontmatter (`name`, `description`, optional
  `base`, optional `thinking`, `model_settings`, `retries`).
- `instructions/*.md` and `rules/*.md` are plain markdown.
- `skills/<name>/SKILL.md` is plain markdown; siblings like
  `references/` can hold deeper notes (not loaded automatically).
- ASCII only unless the output is user-facing and must keep Unicode.
- Any change to an `.md` file triggers `clear_persona_cache()` via the
  watcher when `hot_reload_personas=True`.
