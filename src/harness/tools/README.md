# `src/tools/` -- harness tool implementations

Every tool wired into the main agent lives here. Each module defines
a `*Tools` class whose methods become tool functions via the registry.
Read-only mode is enforced by `PolicyGuard` at registration time.

## Files

| File                             | Role                                                                                                        |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| [`__init__.py`](__init__.py)     | Re-exports public surface (`ToolRuntime`, `*Tools`).                                                        |
| [`registry.py`](registry.py)     | `ToolRegistry` + `ToolRuntime` -- builds the pydantic-ai `FunctionToolset`, tags each tool with a category. |
| [`policy.py`](policy.py)         | `PolicyGuard` -- filters mutating/shell tools for read-only personas.                                       |
| [`file.py`](file.py)             | `FileTools` -- `read_file`, `write_file`, `edit_file`, `list_files`, binary-extension guard.                |
| [`search.py`](search.py)         | `SearchTools` -- glob + ripgrep-style content search.                                                       |
| [`shell.py`](shell.py)           | `ShellTools` -- sandboxed shell exec (category=`shell`).                                                    |
| [`notes.py`](notes.py)           | `NotesTools` -- `save_note`, `query_notes` over memory store.                                               |
| [`todos.py`](todos.py)           | `TodoTools` -- per-session todo CRUD.                                                                       |
| [`background.py`](background.py) | `BackgroundTools` -- launch/poll non-blocking jobs.                                                         |
| [`scheduling.py`](scheduling.py) | `ScheduleTools` -- cron/interval job CRUD.                                                                  |
| [`skills.py`](skills.py)         | `SkillTools` -- `list_skills`, `load_skill` on-demand loader.                                               |
| [`_clip.py`](_clip.py)           | Small helper: clip long strings for event-stream payloads.                                                  |

## Categories

Every tool carries one of:

- `read` -- pure observation (read_file, list_files, search, query_notes).
- `mutate` -- changes workspace or store (write_file, edit_file, save_note).
- `shell` -- arbitrary shell execution (treated as mutate + network).

`PolicyGuard.filter(tool_defs, read_only=True)` drops `mutate` and
`shell` so read-only personas physically cannot call them.

## Registration flow

```text
AgentBuilder
  -> ToolRuntime(deps, store)
       -> ToolRegistry.build()
            -> FunctionToolset with categories
  -> PolicyGuard (via prepare_tools hook) strips mutating tools when needed
  -> Agent(..., toolsets=[runtime.toolset, *mcp_toolsets])
```

## Related

- Runtime hooks that timestamp tool calls: [`src/hooks/hooks.py`](../hooks/hooks.py)
- Tool use discipline rule: [`src/prompts/rules/tools.md`](../prompts/rules/tools.md)
