# Skill: Sessions (when and how to use them)

Load this when you need to reason about "what happened earlier in
this conversation" or "should I resume an old thread?"

## Mental model

A session is one continuous conversation with the user. It has:

- an **id** (shown in your context as `session_id`)
- a **transcript** of messages (user, assistant, tool calls, results)
- an **event log** of structured events (tool calls, timings, model
  requests, memory injections)
- a **todo list** scoped to the session
- **working memory** (task, files touched, notes)

Sessions end when the user exits or starts a new one. Memory is the
only thing that persists across sessions by default.

## What the harness already does for you

- **Appends every tool call, result, and model request** to the event
  log. You do not have to "log" anything manually.
- **Compacts old history** when it grows past the soft token limit.
  Older messages are summarized; recent ones stay verbatim.
- **Dedupes repeat file reads** in the older portion of history --
  same file, same path, earlier in the session: dropped.
- **Redacts common secret patterns** (API keys, private keys, tokens)
  before the model sees them.

Trust this pipeline. Do not re-read a file just because "it was a few
turns ago" -- it is still in the compacted summary.

## When to inspect session state

- **"What did we decide earlier?"** -- check `recent_events` in your
  dynamic context first. It lists the last 5 structured events.
- **"Did that tool call succeed?"** -- `tool-result` and
  `tool-execute-error` events carry the outcome. The user can also
  run `/logs kind=tool_call last=10`.
- **"What files did I touch?"** -- working memory has `files_touched`.
  Use it instead of scanning the full log.

## When to use todos

Create a todo (`add_todo`) when:

- The task has 3+ steps and you want the user to see progress.
- You will pause for user input and want to remember where to resume.
- You are tracking "must not forget" items the user explicitly listed.

Do not create a todo for trivial single-step work. One todo per
logical step, not one per tool call.

Close todos as soon as they are done (`update_todo status=completed`)
-- do not batch. Stale open todos clutter the context.

## When to resume vs. start fresh

Resume a session (`/replay`) when:

- The user explicitly asks to continue prior work.
- You were interrupted mid-task and the working memory still points
  at the same goal.

Start fresh when:

- The topic changed. A new session gets a clean compaction budget.
- The old session is polluted with failed attempts. Memories survive,
  noise does not.

## Events you can rely on

You do not need to know every kind, but these are the ones that
matter for reasoning:

- `tool-call`, `tool-result`, `tool-execute-error` -- what you tried.
- `tool-timing` -- wall-clock per tool, useful for spotting a slow
  command.
- `memory-injection` -- which memories were pulled into the current
  turn.
- `memory-extracted` -- what the extractor saved at end of turn.
- `model-request-error` -- a provider-level failure; the run will
  usually retry.

If you see repeated `model-request-error` for the same reason, stop
retrying and surface the error in your `FinalAnswer`.

## Red flags

- You are about to do something the event log shows you already did.
- A todo from 20 minutes ago is still `in_progress` and unrelated to
  the current step -- close it or re-open deliberately.
- The user says "we already covered this" -- check `recent_events`
  and `retrieved_memories` before answering.
