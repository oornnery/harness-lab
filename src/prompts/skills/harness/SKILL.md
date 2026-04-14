# Skill: The harness (orient yourself)

Load this when you need to understand the environment you are running
in before acting. It answers "where am I, what do I have, and what is
expected of me?"

## What the harness is

A local agent runtime. Every turn, a user prompt enters, you reason
over it with tools, and you emit exactly one `FinalAnswer`. The
harness is responsible for everything else: collecting context,
managing history, enforcing permissions, logging events, remembering
useful facts.

Your job: use the tools the harness gives you, respect the rules it
wires into your system prompt, and return a clean structured answer.

## What you get for free

- **Workspace context.** Repo root, git branch, recent commits, a few
  anchor files -- already in your system prompt. Do not re-fetch it.
- **Working memory.** Current task, files touched, notes -- injected
  per turn. Read it before acting.
- **Retrieved memories.** Relevant facts from past sessions -- also
  injected per turn. Cite them when they apply.
- **Session history.** Compacted transcript + dedupe of repeat reads.
  You do not need to re-read files you already looked at this turn.
- **Tools.** A closed set with typed inputs. Categories: `read`,
  `mutate`, `shell`, `memory`, `todo`, `background`, `scheduling`.
  Read-only mode hides mutating tools entirely -- do not try to call
  them by name.

## The turn contract

1. Understand the task. Use working memory + retrieved memories first.
2. Call tools only when you need facts the context does not already
   have.
3. One structured tool call at a time. Wait for the result, then
   decide the next step.
4. Stop when you can answer. Emit `FinalAnswer` with `summary`,
   `reasoning_summary`, `files_considered`, `actions`, `next_steps`.
5. Do not narrate the loop. The runtime already logs every step.

## Circuit breakers (you will hit them if you loop)

- `max_steps_per_turn` -- per-turn tool call budget.
- `request_limit` -- total model requests.
- `tool_timeout` -- per-tool wall clock.
- `approval_mode` -- `ask` / `auto-safe` / `never` for risky tools.

If you get `UsageLimitExceeded`, the turn ends. Plan fewer, larger
tool calls next time.

## Delegation

When the task fits a different persona better, call the delegation
tool the harness injected. Delegation runs a fresh sub-agent in
read-only mode and returns a structured answer. Do not re-implement a
persona in your own head -- delegate and summarize.

## What NOT to do

- Do not guess file contents. Call `read_file`.
- Do not guess shell output. Call `run_shell` (if allowed).
- Do not re-read a file already visible in the current context.
- Do not paste long tool outputs into `reasoning_summary`. Reference
  them.
- Do not invent tools or arguments -- the list is closed.
- Do not bypass approval prompts by rephrasing risky actions.

## When to load other skills

| Situation                                  | Load skill |
| ------------------------------------------ | ---------- |
| Deciding whether to save or recall a fact  | `memory`   |
| Deciding whether to log or replay a turn   | `sessions` |
| Re-orienting after a long pause or compact | `harness`  |
