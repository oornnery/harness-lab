# `src/` -- harness-lab runtime

Python package layout for the pydantic-ai 1.x agent harness.

## Top-level modules

| File                                         | Role                                                      |
| -------------------------------------------- | --------------------------------------------------------- |
| [`__init__.py`](__init__.py)                 | Package marker.                                           |
| [`model.py`](model.py)                       | `HarnessSettings` + `ModelAdapter` (provider routing).    |
| [`schema.py`](schema.py)                     | `FinalAnswer`, `HarnessOutput`, output validator.         |
| [`policy.py`](policy.py)                     | `HarnessDeps` -- deps object passed to every tool.        |
| [`context.py`](context.py)                   | `WorkspaceContext` (git-aware cwd snapshot).              |
| [`background.py`](background.py)             | `BackgroundRunner` for async, non-blocking jobs.          |
| [`scheduler.py`](scheduler.py)               | `Scheduler` for timed/periodic tasks.                     |

## Sub-packages

| Package                | Responsibility                                               |
| ---------------------- | ------------------------------------------------------------ |
| [`agent/`](agent/)     | Persona loading, agent build, delegation, history pipeline.  |
| [`hooks/`](hooks/)     | `pydantic-ai` runtime hooks (events, timing, memory).        |
| [`memory/`](memory/)   | Extraction agent + schema for long-term memory.              |
| [`session/`](session/) | sqlite-backed session store + typed repositories.            |
| [`tools/`](tools/)     | Tool implementations wired into every agent.                 |
| [`cli/`](cli/)         | `HarnessCliApp`, turn runner, Rich renderer, slash commands. |
| [`prompts/`](prompts/) | Markdown source for personas, rules, instructions, skills.   |

## Boot order (who wires what)

```text
HarnessCliApp.setup()
  -> HarnessSettings (env)
  -> UnifiedStore (sqlite + sqlite-vec)
  -> ModelAdapter (provider)
  -> HarnessDeps
  -> AgentBuilder
       -> load_persona + rules + instructions
       -> ToolRuntime (registry)
       -> Hooks capability
       -> MCP toolsets (PrefixedToolset)
       -> Agent
```

Each sub-package has its own `README.md` with a file-by-file map.

## Validation gate

```bash
uv run ruff format --check .
uv run ruff check .
uv run rumdl check .
uv run ty check
uv run pytest -q
```

## References

- Agent composition: [`src/agent/README.md`](agent/README.md)
- Prompt layers: [`src/prompts/README.md`](prompts/README.md)
- Session store: [`src/session/README.md`](session/README.md)
