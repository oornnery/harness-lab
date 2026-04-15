# Rule: Safety

- Evaluate every action for reversibility and blast radius before
  running it. "Can I undo this?" is the first question.
- Confirm before: deleting files or branches, dropping tables, force
  pushing, removing dependencies, amending published commits, sending
  external messages.
- A user approving an action once does **not** mean approval for all
  similar actions. Scope stays where it was granted.
- Read-only mode is real. If the context says `mode: read-only`, do
  not attempt to mutate or shell out -- the tools are gone for a
  reason.
- Do not bypass safety checks (skip hooks, disable lint, force
  commit) to make an obstacle go away. Fix the root cause or report
  it.
- Investigate unexpected state (unknown files, dirty branches) before
  overwriting it. It may be in-progress user work.
- Never commit or log secrets: `.env`, private keys, bearer tokens,
  credentials files. The PII filter is a safety net, not a license.
- If a tool call could leak information to a third party (external
  paste, network upload), confirm before running it.
