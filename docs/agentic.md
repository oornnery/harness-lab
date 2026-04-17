# agentic -- framework harness (Pydantic AI)

## 1. Scope

Same capabilities as `tooled` (tools, hooks, policy, memory,
history) but backed by Pydantic AI. The tool loop, dispatch, schema
generation, and validation come from the framework. Adds
structured output, multi-agent orchestration, and pluggable tracing.

Loop (same idea as `tooled`, implicit):

```text
user -> Agent.run(deps) -> [framework runs tool loop + validates] -> result_type
```

## 2. Non-goals

- Replacing the REPL / slash commands (reused from `simple` / `tooled`)
- Hiding the raw payload (still possible via `OpenAIModel` but not the focus)

## 3. Requirements

| Item                | Value                                                   |
| ------------------- | ------------------------------------------------------- |
| Runtime             | Python 3.12+                                            |
| Package manager     | `uv`                                                    |
| Provider            | OpenAI-compatible via `OpenAIModel(base_url=...)`       |
| Env vars (required) | `MODEL`, `BASE_URL`, `API_KEY`                          |
| Deps runtime        | `pydantic-ai`, `pydantic`, `httpx`, `rich>=15`, `typer` |
| Deps optional       | `logfire` (tracing), `sqlalchemy` (if memory grows)     |
| Storage             | `sqlite3` stdlib or JSONL                               |

## 4. Architecture

```mermaid
flowchart LR
    U([User]) --> CLI[main.py<br/>Typer + REPL]
    CLI --> AGENT[pydantic_ai.Agent]
    AGENT --> MODEL[OpenAIModel]
    MODEL --> API[(Provider)]
    AGENT --> TOOLS[@agent.tool<br/>RunContext Deps]
    AGENT --> VAL[result_validator<br/>policy + guardrails]
    TOOLS --> DEPS[Deps<br/>memory, policy, console]
    DEPS --> MEM[(memory store)]
    DEPS --> POL[(Policy)]
    AGENT --> HIST[(ModelMessage history)]
    CLI --> SESS[session.py]
    SESS --> DISK[(./.agentic/)]
    AGENT -.->|optional| TRACE[Logfire / OTel]
```

## 5. Components

| Module         | Responsibility                                     |
| -------------- | -------------------------------------------------- |
| `agent.py`     | `Agent`, tools, validators                         |
| `deps.py`      | `Deps` dataclass (memory, policy, console)         |
| `tools/`       | `fs.py`, `shell.py`, `memory.py` etc               |
| `memory.py`    | Store (SQLite / JSONL / vector)                    |
| `policy.py`    | `Policy` + confirm CLI                             |
| `history.py`   | `ModelMessage` persistence                         |
| `commands.py`  | Slash REPL                                         |
| `main.py`      | Typer CLI + REPL + streaming                       |

## 6. Features

### 6.1 Agent

```python
agent = Agent(
    OpenAIModel(model_name, base_url=url, api_key=key),
    deps_type=Deps,
    result_type=Reply,         # optional structured output
    system_prompt=SYS,
)
```

### 6.2 Tools with injected deps

```python
@agent.tool
async def read_file(ctx: RunContext[Deps], path: str) -> str:
    ctx.deps.policy.check("read_file", {"path": path})
    return Path(path).read_text()
```

- Schema generated from type hints + docstring
- `RunContext[Deps]` exposes memory, policy, console
- Native async; parallel tool calls supported

### 6.3 Structured output

```python
class Reply(BaseModel):
    answer: str
    citations: list[str] = []
    confidence: float
```

Framework validates before returning. `ModelRetry` if invalid --
agent retries automatically.

### 6.4 Hooks -- validators and wrappers

- `@agent.result_validator` -- post-processing; may reject and retry
- `@agent.system_prompt` -- dynamic per run, reads `ctx.deps`
- Decorator wrappers around `@agent.tool` for pre / post (log, metrics, redaction)

### 6.5 Policy -- deps + validator

- `Deps.policy` available in every tool via `ctx.deps`
- Tool raises `ModelRetry("tool X not allowed: ...")` -- agent reacts
- Confirm prompts live in `Deps.console` (CLI handles input)

### 6.6 Memory

- Separate from history; stored in `Deps.memory`
- Exposed as tools `remember` and `recall`
- Backend pluggable: SQLite FTS, JSONL, vector store

### 6.7 History

- `ModelMessage` objects, serializable
- `agent.run(..., message_history=past)` resumes conversation
- Autosave per turn, same id pattern as `simple` / `tooled`

### 6.8 Multi-agent

One agent exposed as a tool of another:

```python
@planner.tool
async def delegate_to_coder(ctx, task: str) -> str:
    result = await coder_agent.run(task, deps=ctx.deps)
    return result.data
```

Framework isolates per-agent history.

### 6.9 Streaming

`agent.run_stream(...)` yields text deltas AND tool events (call,
return). REPL renders both channels.

### 6.10 Tracing (optional)

```python
logfire.configure()
logfire.instrument_pydantic_ai()
```

Each run, tool, and prompt gains a span. OpenTelemetry compatible.

## 7. Storage

| Path                            | Purpose                           |
| ------------------------------- | --------------------------------- |
| `./.agentic/sessions/<id>.json` | serialized `ModelMessage` history |
| `./.agentic/memory.db`          | SQLite memory store               |
| `./.agentic/policy.json`        | persisted policy                  |
| `./.agentic/transcript.jsonl`   | turn log                          |
| `./.agentic/exports/<id>.md`    | markdown export                   |
| `./.agentic/history`            | readline prompt history           |

Add `.agentic/` to `.gitignore`.

## 8. What still has to be written

- REPL slash commands (reuse from `simple` / `tooled`)
- Policy confirm UI (writes to `Deps.console`)
- Session id lifecycle and autosave plumbing
- Migration code if `ModelMessage` schema evolves between framework versions

## 9. When to use

- Tool catalog exceeds ~10 with nested types
- Need validated **structured output**
- Need **multi-agent** without reinventing orchestration
- Want pluggable **tracing** (Logfire / OTel)
- Already comfortable with `simple` and `tooled`

## 10. Limits

- Abstraction hides the loop -- debugging requires framework knowledge
- Version bumps may break API (framework still young)
- Less fine control over the raw payload
- Extra runtime dependency

## 11. Effort estimate

| Milestone                                          | Days |
| -------------------------------------------------- | ---- |
| MVP: single agent + 5 tools + memory + policy      | 2-3  |
| Extend: multi-agent + structured output + tracing  | +2-3 |

## 12. Layer comparison

| Layer             | simple        | tooled                    | agentic                      |
| ----------------- | ------------- | ------------------------- | ---------------------------- |
| Chat loop         | manual        | manual                    | framework                    |
| Tools             | --            | manual registry           | `@agent.tool`                |
| Tool schema       | --            | hand / Pydantic adapter   | auto (Pydantic)              |
| Hooks             | --            | `_PRE` / `_POST` lists    | validators + wrappers        |
| Policy            | --            | dataclass + confirm       | `Deps.policy` + `ModelRetry` |
| Memory            | --            | JSONL + tools             | pluggable store + tools      |
| History           | JSON autosave | JSON + tool msgs          | `ModelMessage` autosave      |
| Structured output | --            | --                        | `result_type` Pydantic       |
| Multi-agent       | --            | manual (tool calls agent) | agent-as-tool native         |
| Tracing           | --            | local log                 | Logfire / OTel               |
| Deps              | --            | closures / globals        | `RunContext[Deps]`           |
| Runtime deps      | httpx + rich  | httpx + rich              | httpx + rich + pydantic-ai   |
