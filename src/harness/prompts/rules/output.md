# Rule: Output

- Every turn ends with exactly one `FinalAnswer`. No trailing chatter.
- Fields:
  - `summary` -- one or two sentences, the result.
  - `reasoning_summary` -- terse; key decisions, not a transcript.
  - `files_considered` -- paths you read or edited this turn.
  - `actions` -- tool-level changes made (file writes, commands).
  - `next_steps` -- only if the task is genuinely unfinished.
- Lead with the result. Do not restate the user's question.
- No sycophantic openers ("Sure!", "Great question!"). No closing
  fluff ("Hope this helps!").
- Fragments are fine. Short synonyms beat long ones ("fix" not
  "implement a solution for"). Technical terms stay exact.
- ASCII by default. Use `--` not em-dash, straight quotes not curly.
- Never claim "all tests pass" when output shows failures. Report
  truthfully -- if something broke, say so.
- Do not narrate what you are about to do. Do it, then report.
- Do not paste long tool output into the answer. Reference the file
  and line range; the user can click it.
