# `src/session/` -- sqlite session store

Single sqlite file backs every runtime fact the harness cares about:
sessions, messages, structured events, todos, scheduled jobs, background
jobs, and long-term memories (the memory package writes here too).

## Top-level files

| File                         | Role                                                            |
| ---------------------------- | --------------------------------------------------------------- |
| [`__init__.py`](__init__.py) | Re-exports `UnifiedStore`, repos, `DatabaseManager`.            |
| [`database.py`](database.py) | `DatabaseManager` -- opens sqlite, loads `sqlite-vec`, pragmas. |
| [`schema.py`](schema.py)     | Table DDL for sessions, messages, events, todos, memories, vss. |
| [`store.py`](store.py)       | `UnifiedStore` -- owns the connection, hands out repos.         |

## Repositories (`repos/`)

Thin data-access classes. Every repo takes a shared sqlite connection
and exposes CRUD + a narrow query API. No ORM.

| File                                   | Repo                  | Responsibility                                       |
| -------------------------------------- | --------------------- | ---------------------------------------------------- |
| [`session.py`](repos/session.py)       | `SessionRepository`   | Create/load/list sessions, touch last-active.        |
| [`message.py`](repos/message.py)       | `MessageRepository`   | Append `ModelMessage` rows; hydrate history on load. |
| [`event.py`](repos/event.py)           | `EventRepository`     | Append + query the structured event stream.          |
| [`todo.py`](repos/todo.py)             | `TodoRepository`      | Per-session todo list (used by `TodoTools`).         |
| [`scheduled.py`](repos/scheduled.py)   | `ScheduledRepository` | Cron/interval jobs for `Scheduler`.                  |
| [`background.py`](repos/background.py) | `BackgroundRepository`| Fire-and-forget jobs for `BackgroundRunner`.         |
| [`memory.py`](repos/memory.py)         | `MemoryRepository`    | Long-term memory rows + sqlite-vec ANN search.       |

## Event stream

Every tool call, text chunk, and error goes through
`EventRepository.append(kind, payload)`. The CLI's `/logs` and `/replay`
commands read from here, and `on_stream_event` on the renderer mirrors
live events to the same table.

Event kinds currently in use: `tool_call`, `tool_result`, `tool_error`,
`text`, `memory_inject`, `memory_store`, `model_error`.

## Related

- Agent-facing guidance: [`src/prompts/skills/sessions/SKILL.md`](../prompts/skills/sessions/SKILL.md)
- Hook that feeds events: [`src/hooks/hooks.py`](../hooks/hooks.py)
- Memory tables live here too: [`src/memory/README.md`](../memory/README.md)
