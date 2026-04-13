from __future__ import annotations

from typing import Any

from pydantic_ai import (
    AgentRunResultEvent,
    DeferredToolRequests,
    DeferredToolResults,
    ToolDenied,
    UsageLimitExceeded,
)
from rich.panel import Panel

from ..agent import AgentHandle
from ..model import HarnessSettings
from .renderer import StreamRenderer


class TurnRunner:
    # Uses `agent.run_stream_events` for explicit Rich-driven rendering.
    # For non-Rich frontends (FastAPI SSE, Discord, WebSocket), prefer
    # `Agent(event_stream_handler=...)` which offers a cleaner callback
    # shape without requiring a manual async-for loop here.

    def __init__(self, renderer: StreamRenderer, settings: HarnessSettings) -> None:
        self.renderer = renderer
        self.settings = settings

    async def run(
        self,
        handle: AgentHandle,
        user_prompt: str,
        attachments: list[Any] | None = None,
    ) -> Any:
        handle.deps.policy.recent_calls.clear()
        handle.deps.policy.tool_timings.clear()

        current_history: list[Any] = handle.history
        deferred_results: DeferredToolResults | None = None
        current_prompt: Any = [user_prompt, *attachments] if attachments else user_prompt

        while True:
            result = None
            self.renderer.start_status("[bold cyan]thinking...[/]")
            try:
                async for event in handle.agent.run_stream_events(
                    user_prompt=current_prompt,
                    message_history=current_history,
                    deferred_tool_results=deferred_results,
                    deps=handle.deps,
                    usage_limits=self.settings.usage_limits,
                ):
                    if isinstance(event, AgentRunResultEvent):
                        result = event.result
                        continue
                    self.renderer.on_stream_event(event)
            except UsageLimitExceeded as exc:
                self.renderer.console.print(
                    Panel(
                        str(exc),
                        title="usage limit exceeded",
                        border_style="red",
                    )
                )
                return None
            finally:
                self.renderer.cleanup_turn()

            if result is None:
                raise RuntimeError("Agent finished streaming without yielding a final result.")

            if isinstance(result.output, DeferredToolRequests):
                self.renderer.console.print(
                    Panel("Approval required before continuing.", title="Deferred tools")
                )
                deferred_results = await self._collect_approvals(result.output)
                current_history = result.all_messages()
                current_prompt = None
                continue

            return result

    async def _collect_approvals(self, requests: DeferredToolRequests) -> DeferredToolResults:
        results = DeferredToolResults()
        console = self.renderer.console

        for approval in requests.approvals:
            self.renderer.render_approval_request(approval)

            if self.settings.approval_mode == "never":
                console.print("[red]auto-denied (approval_mode=never)[/]")
                results.approvals[approval.tool_call_id] = ToolDenied(
                    "Approval mode is set to never."
                )
                continue

            if self.settings.approval_mode == "auto-safe":
                allowed = approval.tool_name != "run_shell"
                marker = "[green]auto-approved[/]" if allowed else "[red]auto-denied (shell)[/]"
                console.print(f"{marker} (approval_mode=auto-safe)")
                results.approvals[approval.tool_call_id] = allowed
                continue

            approved = await self.renderer.approval_prompt()
            if approved:
                console.print("[green]approved[/]")
                results.approvals[approval.tool_call_id] = True
            else:
                console.print("[red]denied[/]")
                results.approvals[approval.tool_call_id] = ToolDenied("User denied via prompt.")

        return results
