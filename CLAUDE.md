# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Python Conventions

- Use `pathlib` over `os.path`
- f-strings only — no `.format()` or `%`
- `snake_case` functions/variables, `PascalCase` classes, `UPPER_SNAKE` constants
- Type all public functions — use modern syntax: `str | None`, `list[str]`
- Use `Annotated` style for FastAPI parameters and dependencies
- `logging` for app logs, `rich` for CLI output — never `print`
- Pydantic `BaseModel` for validation, `dataclass` for plain data
- IO at edges only — services and domain must be pure
- Prefer `uv` over direct `pip` workflows
- Never commit code that fails `ruff check` and `ty check` — these are the minimum quality gates
- Prefer early returns over deep nesting
- Use `Protocol` for structural typing, `ABC` for enforced hierarchies
- Use `enum.Enum` over string constants for fixed sets
- Use `__all__` to define public API in modules
- Use `@wraps` on all decorators
- Validate all external input at system boundaries
- Never use `eval()`, `exec()`, or `__import__()` with user input
- Use parameterized queries — never format SQL strings

## Anti-Gold-Plating

- Do not add features, refactoring, or cleanup beyond what was asked
- Do not add error handling for impossible scenarios -- trust framework guarantees
- Do not create abstractions for one-time operations -- three similar lines is fine
- Do not add docstrings, comments, or annotations to unchanged code
- Do not design for hypothetical future requirements
- Only validate at system boundaries, not internal code

## Comments

- Code should be commented for clarity and maintainability
- Comments explain WHY, never WHAT — well-named identifiers already describe what
- WHY comments for: hidden constraints, workarounds, non-obvious invariants, business rules
- Delete stale comments that no longer match the code
- Do not add comments to code you did not change
- Inline comments on the same line only for short clarifications

## Faithful Reporting

- Never claim "all tests pass" when output shows failures
- Never suppress failing checks to manufacture a green result
- Never characterize incomplete work as done
- Report outcomes faithfully — if something broke, say so
- Do not hedge confirmed results with unnecessary disclaimers

## Safety

## Before Any Action

Evaluate: reversibility, blast radius, scope match with request.

## Confirmation Required

- **Destructive**: deleting files/branches, dropping tables, `rm -rf`
- **Hard-to-reverse**: force push, amending published commits, removing deps
- **Visible to others**: pushing code, creating/closing PRs/issues, sending messages
- **Production-affecting**: deployments, migrations on prod, infra changes

## Standing Rules

- Approval once does **not** mean approval in all contexts
- Prioritize immediate correction of failing tests
- Investigate unexpected state before deleting -- it may be in-progress work
- Resolve merge conflicts rather than discarding changes
- Diagnose root causes before switching tactics -- do not retry blindly

## Output Token Efficiency

## Response Style

- No sycophantic openers ("Sure!", "Great question!", "Absolutely!")
- No closing fluff ("Hope this helps!", "Let me know if you need anything!")
- Never restate the user's question before answering
- No narration ("Now I will...", "Let me...", "I have completed...")
- Lead with the result or action, not the explanation
- Explanations only when asked or when the result is genuinely ambiguous
- Short, direct responses -- terse but complete reasoning

## ASCII Output

- ASCII-only in responses: no em-dashes, smart quotes, or decorative Unicode
- Use `--` not `—`, straight quotes not curly quotes
- Exception: code output that requires Unicode, or user-facing content
  where the user specifies Unicode

## Anti-Hallucination

- Never invent file paths, function names, API endpoints, or CLI flags
- If a path or name is unknown, verify with tools before referencing it
- Return "UNKNOWN" rather than guessing identifiers
- Never fabricate tool output or test results
- When referencing code, verify it exists before citing it

## Efficiency

- Do not re-read a file already read in this conversation unless it may
  have been modified since
- Do not re-read tool output that is still in context
- Write complete solutions in one pass rather than building incrementally
  across multiple tool calls
- Do not write partial code to immediately edit it -- get it right the
  first time

## Model Selection

Choose the cheapest model that handles the task:

1. **haiku** -- documentation, simple renames, formatting (3x cheaper than sonnet)
2. **sonnet** -- default for implementation, review, debugging
3. **opus** -- only for deep reasoning, architecture, ambiguous specs
4. **codex** -- only for generating large code blocks when context window is insufficient

## Agent Efficiency

- Keep agent definitions lean (<60 lines). Knowledge belongs in skills.
- Prefer Grep/Glob over Bash for search (structured results, less noise).
- Read only the relevant section of large files (use offset/limit).

## Uv Conventions

- Always use `uv` over `pip` -- never `pip install`, `pip freeze`, `python -m pip`
- Never hand-edit `pyproject.toml` to add dependencies — use `uv add <pkg>`.
- Use `uv run` to execute project commands -- never activate venvs manually
- Use `uvx` for one-off tool execution outside the project environment
- Add dependencies with `uv add`, dev deps with `uv add --dev`
- Commit `uv.lock` for reproducible installs
- Use `uv sync --frozen` in CI to catch lockfile drift
- Use `uv sync --no-dev` for production installs
- Pin Python version with `.python-version`
- Use `uv tool install` for global tooling (ruff, ty, rumdl)

### Common Tasks

```bash
uv run task lint        # ruff check --fix && ty check src
uv run task fmt         # ruff format . && rumdl fmt .
uv run task test        # pytest
uv run task test-cov    # pytest with coverage report
```

## Git Safety

- **Never** `git add .` or `git add -A` -- stage files by name
- **Never** `git commit --amend` unless explicitly asked
- **Never** `git push` unless explicitly asked
- **Never** `git reset --hard`, `git checkout .`, or `git clean`
- **Never** skip hooks (`--no-verify`)
- If a hook fails, fix the issue and create a **new** commit
- Skip files that look like secrets (`.env`, `*.pem`, `credentials.*`)
- Use Conventional Commits: `type(scope): description`

## Production Protection

- Never commit or push directly to `main`/`master` -- use a PR
- Never deploy without passing the full validation suite

## Worktrees

- Use `git worktree` for parallel work; clean up after completion
- Never delete a worktree with uncommitted changes without warning

## Code Review

- Review for correctness, style, and adherence to conventions
- Do not approve PRs with failing checks or merge conflicts
- Request changes for issues rather than approving and fixing yourself
- Do not merge PRs that are not your own without explicit permission
- Do not commit changes to a PR that you did not author without explicit permission
- Use "Request changes" rather than "Approve with comments" for any non-trivial issues

## RTK

Read @RTK.md for conventions of the commands and tokens efficiently.

## Architecture

<!-- Description of the overall architecture -->

## Conventions

<!-- Conventions for code style, testing, documentation, etc. -->

## Verification after changes

<!-- Steps to verify correctness after making changes with format, linting and testing -->

## Troubleshooting

<!-- Common issues and how to resolve them -->
