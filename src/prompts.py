from __future__ import annotations

from pydantic_ai import RunContext

from .policy import HarnessDeps


def build_static_instructions() -> str:
    """Stable prompt prefix.

    This is the equivalent of the "prompt skeleton" or "prefix prompt" in many
    coding harnesses. It rarely changes and is therefore a good candidate for
    provider-side caching.
    """

    return """
You are a coding-agent harness demo running inside a local CLI.

Core rules:
- Be precise, grounded, and incremental.
- Prefer tools over guessing.
- Read before writing.
- When a request requires changing files, explain the intended change in the final answer.
- Respect workspace policy, path sandbox, read-only mode, and approval requirements.
- Never invent files, commands, or tool results.
- Keep the final answer concise but operational.

Operational style:
- Use tools to inspect the repo before proposing edits.
- Mention which files were inspected.
- When a tool is denied or deferred, incorporate that into the answer.
- When the task is blocked, say exactly what approval or next action is needed.

Final answer contract:
- You MUST ALWAYS return a structured `FinalAnswer` object. Never return plain text or markdown.
- The `FinalAnswer` schema has: `summary` (str, required),
  `reasoning_summary` (str, required), `files_considered` (list[str]),
  `actions` (list of objects with `kind` and `summary`),
  `next_steps` (list[str]).
- Every single response -- including greetings, simple questions, and
  error messages -- must be a valid `FinalAnswer`.
- Put the main response in `summary`. Use `reasoning_summary` to explain your approach.
- Summarize what was learned.
- Mention meaningful actions performed.
- Include reasonable next steps only when helpful.
""".strip()


def build_dynamic_instructions(ctx: RunContext[HarnessDeps]) -> str:
    deps = ctx.deps
    mode = "read-only" if deps.settings.read_only else "read-write"
    recent_events = deps.session_store.describe_recent_events_sync(deps.session_id, limit=5)

    return f"""
Session id: {deps.session_id}
Workspace mode: {mode}
Approval mode: {deps.settings.approval_mode}

Workspace snapshot:
{deps.workspace.prompt_summary()}

Recent session signals:
{recent_events or "No prior event summary available."}
""".strip()
