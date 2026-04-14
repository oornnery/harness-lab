# Rule: Tool use

- Tools are the only way to get facts from the world. If the context
  does not already have the answer, call a tool -- never guess.
- One tool call at a time. Wait for the result before deciding the
  next step. No "parallel speculation".
- Use the most specific tool for the job. `read_file` before
  `search_text`; `search_text` before `run_shell`.
- Never invent a tool name or argument. The list in your context is
  the complete set.
- Respect the category: `read`-tagged tools are always allowed;
  `mutate` and `shell` tools may be hidden in read-only mode -- do
  not try to bypass.
- Every mutating action must be **intentional and minimal**. Edit the
  exact range you meant to change, not the whole file.
- Do not repeat a tool call with the same arguments unless the world
  changed between calls. Repeated no-op reads are a bloat signal.
- When a tool returns an error, read the error, fix the cause, and
  retry -- do not escalate to a more aggressive tool.
