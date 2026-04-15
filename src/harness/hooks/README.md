# Hooks

Two distinct hook layers coexist in this repo. They target different
runtimes and never interact.

## 1. Runtime hooks -- `src/hooks/hooks.py`

`pydantic-ai` `Hooks` capability attached to the main agent via
`AgentBuilder`. Runs in-process on every turn.

| Hook                   | Purpose                                            |
| ---------------------- | -------------------------------------------------- |
| `run_event_stream`     | Mirror tool-call / tool-result / text into events. |
| `before_tool_execute`  | Start per-tool elapsed timer.                      |
| `after_tool_execute`   | Record elapsed-ms event.                           |
| `tool_execute_error`   | Record tool-error event.                           |
| `prepare_tools`        | Filter out mutating tools in read-only mode.       |
| `before_model_request` | Inject retrieved memories into deps.               |
| `after_model_request`  | Extract + persist memories via `MemoryExtractor`.  |
| `model_request_error`  | Record model-error event.                          |

Entry point: `build_harness_hooks()` -> `Hooks[HarnessDeps]`. Wired in
[src/agent/builder.py](../agent/builder.py) via
`capabilities=[build_harness_hooks()]`.

## 2. Claude Code shell hooks -- `.claude/hooks/*.sh`

Shell scripts invoked by the Claude Code CLI (the external editor tool),
not by this harness. They run outside the Python process on CLI lifecycle
events (PreToolUse, PostToolUse, SessionStart, PreCompact, Stop, etc.)
as configured in [.claude/settings.json](../../.claude/settings.json).

Examples: `rtk-rewrite.sh`, `py-autofix.sh`, `git-safety-gate.sh`,
`md-autofix.sh`, `session-start.sh`, `pre-compact.sh`.

## When to use which

- Needs `HarnessDeps` / agent state / event stream -> **runtime hook**.
- Needs to touch the repo filesystem, run linters, or gate CLI actions
  ahead of an LLM call -> **shell hook**.
- Never duplicate logic across layers; pick the one closest to the
  event source.
