from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RunContext,
    TextPart,
    TextPartDelta,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolDenied,
)
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn
from rich.prompt import Confirm
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .commands import ExtensionState, build_command_index
from .context import ContextBuilder
from .model import HarnessSettings, ModelAdapter
from .policy import HarnessDeps, RuntimePolicy, build_capabilities
from .prompts import build_dynamic_instructions, build_static_instructions
from .schema import HarnessOutput, build_output_types, register_output_validator
from .sessions import SessionStore
from .tools import ToolRuntime, register_tools


def _extract_partial_string_field(buffer: str, key: str) -> str | None:
    pattern = re.compile(rf'"{re.escape(key)}"\s*:\s*"')
    match = pattern.search(buffer)
    if not match:
        return None
    i = match.end()
    out: list[str] = []
    escape_map = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
    while i < len(buffer):
        ch = buffer[i]
        if ch == "\\" and i + 1 < len(buffer):
            out.append(escape_map.get(buffer[i + 1], buffer[i + 1]))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out)


class HarnessCliApp:
    def __init__(self) -> None:
        self.console = Console()
        self.settings = HarnessSettings()
        self.model_adapter = ModelAdapter(self.settings)
        self.session_store = SessionStore(self.settings.resolved_session_dir())
        self.agent: Agent[HarnessDeps, HarnessOutput] | None = None
        self._tool_runtime: ToolRuntime | None = None
        self.commands = build_command_index()
        self._progress: Progress | None = None
        self._progress_task: TaskID | None = None
        self._progress_active = False
        self._live: Live | None = None
        self._stream_buffer = ""

    async def setup(self) -> tuple[HarnessDeps, ExtensionState, list[Any]]:
        self.console.print(Panel(self.model_adapter.explain(), title="Harness boot"))

        workspace = await ContextBuilder(self.settings).build()
        session_id = self.session_store.create_session_id()
        await self.session_store.ensure_session(session_id)
        history = await self.session_store.load_history(session_id)

        deps = HarnessDeps(
            settings=self.settings,
            workspace=workspace,
            session_store=self.session_store,
            session_id=session_id,
            policy=RuntimePolicy(self.settings, workspace.root),
        )
        self._tool_runtime = ToolRuntime(deps)

        self.agent = self._build_agent()

        state = ExtensionState(
            console=self.console,
            deps=deps,
            session_store=self.session_store,
            session_id=session_id,
            known_tools=[
                "list_files",
                "read_file",
                "search_text",
                "write_file",
                "replace_in_file",
                "run_shell",
            ],
            workspace_summary=workspace.prompt_summary(),
        )
        return deps, state, history

    def _build_agent(self) -> Agent[HarnessDeps, HarnessOutput]:
        agent = Agent[HarnessDeps, HarnessOutput](
            self.model_adapter.build_model(),
            deps_type=HarnessDeps,
            output_type=build_output_types(),
            instructions=build_static_instructions(),
            model_settings=self.model_adapter.build_model_settings(),
            history_processors=[
                self.session_store.history_processor(self.settings.max_history_messages)
            ],
            capabilities=build_capabilities(),
            end_strategy="exhaustive",
            metadata=lambda ctx: {
                "session_id": ctx.deps.session_id,
                "workspace": str(ctx.deps.workspace.root),
            },
        )

        @agent.instructions
        async def _dynamic_instructions(ctx: RunContext[HarnessDeps]) -> str:
            return build_dynamic_instructions(ctx)

        assert self._tool_runtime is not None
        register_tools(agent, self._tool_runtime)
        register_output_validator(agent)
        return agent

    async def run(self) -> None:
        deps, ext_state, history = await self.setup()
        assert self.agent is not None

        self.console.print(
            "[bold]Commands:[/] /help, /context, /tools, /session, /fork, /replay, "
            "/clear, /compact, /resume, /quit"
        )

        while True:
            try:
                user_input = await self._ainput("[bold cyan]you> [/] ")
            except (EOFError, KeyboardInterrupt):
                self.console.print("\nBye.")
                return
            if not user_input.strip():
                continue
            if user_input.strip() in {"/quit", "/exit"}:
                self.console.print("Bye.")
                return

            if user_input.startswith("/"):
                history = await self._handle_command(ext_state, user_input, history)
                continue

            started = time.monotonic()
            result = await self._run_turn(deps, history, user_input)
            elapsed = time.monotonic() - started
            history = result.all_messages()
            await self.session_store.save_history(ext_state.session_id, history)
            self._render_result(result.output)
            self._render_stats(result, elapsed)

    async def _handle_command(
        self, state: ExtensionState, raw: str, history: list[Any]
    ) -> list[Any]:
        name, _, arg = raw[1:].partition(" ")
        command = self.commands.get(name)
        if not command:
            self.console.print(f"[red]Unknown command:[/] {name}")
            return history
        await command.handler(state, arg)
        history = await self.session_store.load_history(state.session_id)
        return history

    async def _run_turn(self, deps: HarnessDeps, history: list[Any], user_prompt: str):
        assert self.agent is not None

        current_history = history
        deferred_results: DeferredToolResults | None = None
        current_prompt: str | None = user_prompt

        while True:
            result = None
            self._start_status("[bold cyan]thinking...[/]")
            try:
                async for event in self.agent.run_stream_events(
                    user_prompt=current_prompt,
                    message_history=current_history,
                    deferred_tool_results=deferred_results,
                    deps=deps,
                ):
                    if isinstance(event, AgentRunResultEvent):
                        result = event.result
                        continue
                    self._render_stream_event(event)
            finally:
                self._stop_status()
                self._stop_live_panel()
                self._progress = None
                self._progress_task = None

            if result is None:
                raise RuntimeError("Agent finished streaming without yielding a final result.")

            if isinstance(result.output, DeferredToolRequests):
                self.console.print(
                    Panel("Approval required before continuing.", title="Deferred tools")
                )
                deferred_results = await self._collect_approvals(result.output)
                current_history = result.all_messages()
                current_prompt = None
                continue

            return result

    async def _collect_approvals(self, requests: DeferredToolRequests) -> DeferredToolResults:
        results = DeferredToolResults()

        for approval in requests.approvals:
            self._render_approval_request(approval)

            if self.settings.approval_mode == "never":
                self.console.print("[red]✗ auto-denied (approval_mode=never)[/]")
                results.approvals[approval.tool_call_id] = ToolDenied(
                    "Approval mode is set to never."
                )
                continue

            if self.settings.approval_mode == "auto-safe":
                allowed = approval.tool_name != "run_shell"
                marker = "[green]✓ auto-approved[/]" if allowed else "[red]✗ auto-denied (shell)[/]"
                self.console.print(f"{marker} (approval_mode=auto-safe)")
                results.approvals[approval.tool_call_id] = allowed
                continue

            approved = await asyncio.to_thread(
                Confirm.ask,
                "[bold yellow]Approve this action?[/]",
                console=self.console,
                default=False,
            )
            if approved:
                self.console.print("[green]✓ approved[/]")
                results.approvals[approval.tool_call_id] = True
            else:
                self.console.print("[red]✗ denied[/]")
                results.approvals[approval.tool_call_id] = ToolDenied("User denied via prompt.")

        return results

    def _render_approval_request(self, approval: Any) -> None:
        raw_args = approval.args
        if isinstance(raw_args, str):
            try:
                args_data = json.loads(raw_args)
            except json.JSONDecodeError:
                args_data = {"_raw": raw_args}
        elif isinstance(raw_args, dict):
            args_data = raw_args
        else:
            args_data = {"_raw": repr(raw_args)}

        path = args_data.get("path") if isinstance(args_data, dict) else None

        header = Table.grid(padding=(0, 1))
        header.add_column(style="bold cyan", justify="right")
        header.add_column()
        header.add_row("tool", f"[bold yellow]{approval.tool_name}[/]")
        if path:
            header.add_row("path", str(path))
        header.add_row("call id", approval.tool_call_id)

        try:
            args_text = json.dumps(args_data, indent=2, ensure_ascii=False, default=str)
            args_view: Any = Syntax(args_text, "json", theme="ansi_dark", word_wrap=True)
        except Exception:
            args_view = repr(raw_args)

        self.console.print(
            Panel(
                Group(header, "", args_view),
                title="[bold red]⚠ Approval required[/]",
                border_style="red",
            )
        )

    def _make_progress(self) -> Progress:
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("[dim]({task.elapsed:.1f}s)[/]"),
            console=self.console,
            transient=True,
        )

    def _start_status(self, label: str) -> None:
        self._progress = self._make_progress()
        self._progress_task = self._progress.add_task(label, total=None)
        self._progress.start()
        self._progress_active = True

    def _stop_status(self) -> None:
        if self._progress is not None and self._progress_active:
            self._progress.stop()
            self._progress_active = False

    def _restart_status(self, label: str) -> None:
        if self._progress is None:
            return
        if self._progress_task is not None:
            self._progress.update(self._progress_task, description=label)
        if not self._progress_active:
            self._progress.start()
            self._progress_active = True

    def _start_live_panel(self) -> None:
        if self._live is not None:
            return
        self._stream_buffer = ""
        self._live = Live(
            Panel("", title="Streaming...", border_style="cyan"),
            console=self.console,
            transient=True,
            refresh_per_second=12,
        )
        self._live.start()

    def _feed_live_panel(self, chunk: str) -> None:
        if self._live is None:
            self._start_live_panel()
        assert self._live is not None
        self._stream_buffer += chunk
        summary = _extract_partial_string_field(self._stream_buffer, "summary")
        reasoning = _extract_partial_string_field(self._stream_buffer, "reasoning_summary")

        summary_body: Any = Markdown(summary) if summary else "[dim]waiting for content...[/]"
        panels: list[Any] = [
            Panel(summary_body, title="Summary", border_style="cyan"),
        ]
        if reasoning:
            panels.append(
                Panel(
                    Markdown(reasoning, style="grey50"),
                    title="Reasoning",
                    border_style="grey50",
                    style="grey50",
                )
            )
        self._live.update(Group(*panels))

    def _stop_live_panel(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
            self._stream_buffer = ""

    def _render_stream_event(self, event: Any) -> None:
        if isinstance(event, PartStartEvent):
            if isinstance(event.part, TextPart):
                self._stop_status()
                if event.part.content:
                    self._feed_live_panel(event.part.content)
                return
            if isinstance(event.part, ToolCallPart) and event.part.tool_name == "final_result":
                self._stop_status()
                args = event.part.args
                if args:
                    self._feed_live_panel(args if isinstance(args, str) else repr(args))
                return

        if isinstance(event, PartDeltaEvent):
            if isinstance(event.delta, TextPartDelta):
                self._stop_status()
                self._feed_live_panel(event.delta.content_delta)
                return
            if isinstance(event.delta, ToolCallPartDelta) and event.delta.args_delta is not None:
                self._stop_status()
                chunk = event.delta.args_delta
                self._feed_live_panel(chunk if isinstance(chunk, str) else repr(chunk))
                return
            if self.settings.show_thinking and isinstance(event.delta, ThinkingPartDelta):
                self._stop_status()
                self.console.print(f"[dim]{event.delta.content_delta}[/]", end="")
                return

        if isinstance(event, FunctionToolCallEvent):
            self._stop_status()
            self._stop_live_panel()
            self.console.print(
                f"[bold yellow]tool call[/] {event.part.tool_name} {event.part.args}"
            )
            return

        if isinstance(event, FunctionToolResultEvent):
            self._stop_status()
            self._stop_live_panel()
            snippet = repr(event.result.content)
            self.console.print(f"[green]tool result[/] {snippet[:200]}")
            self._restart_status("[bold cyan]thinking...[/]")
            return

        if isinstance(event, FinalResultEvent):
            return

    def _render_result(self, output: HarnessOutput) -> None:
        if isinstance(output, DeferredToolRequests):
            self.console.print("[red]Unexpected deferred output at render phase.[/]")
            return

        sections: list[Any] = [Markdown(output.summary)]

        if output.reasoning_summary:
            sections.append(Rule("Reasoning", style="grey50"))
            sections.append(Markdown(output.reasoning_summary, style="grey50"))

        if output.files_considered:
            sections.append(Rule("Files", style="grey50"))
            sections.append(Text(" · ".join(output.files_considered), style="grey50"))

        if output.actions:
            sections.append(Rule("Actions", style="grey50"))
            actions_md = "\n".join(f"- **{a.kind}** — {a.summary}" for a in output.actions)
            sections.append(Markdown(actions_md))

        if output.next_steps:
            sections.append(Rule("Next steps", style="grey50"))
            steps_md = "\n".join(f"- {step}" for step in output.next_steps)
            sections.append(Markdown(steps_md))

        self.console.print()
        self.console.print(Panel(Group(*sections), title="Result", border_style="cyan"))

    def _render_stats(self, result: Any, elapsed: float) -> None:
        usage = result.usage()
        parts = [
            f"[bold]model[/]={self.model_adapter.model_name}",
            f"[bold]elapsed[/]={elapsed:.2f}s",
            f"[bold]input[/]={usage.input_tokens}",
            f"[bold]output[/]={usage.output_tokens}",
            f"[bold]total[/]={usage.total_tokens}",
        ]
        if getattr(usage, "requests", None):
            parts.append(f"[bold]requests[/]={usage.requests}")
        self.console.print("[dim]" + " | ".join(parts) + "[/]")

    async def _ainput(self, prompt: str) -> str:
        return await asyncio.to_thread(self.console.input, prompt)


def main() -> None:
    asyncio.run(HarnessCliApp().run())


if __name__ == "__main__":
    main()
