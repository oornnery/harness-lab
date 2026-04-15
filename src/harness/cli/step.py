"""Step-by-step turn execution using `agent.iter()`.

Lets the user advance one node at a time, inspecting state between
`UserPromptNode -> ModelRequestNode -> CallToolsNode -> End`. Useful
for teaching the agent loop and for human-in-the-loop debugging.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from src.agent import AgentHandle


class StepRunner:
    def __init__(self, console: Console) -> None:
        self.console = console

    async def run(self, handle: AgentHandle, user_prompt: str) -> Any:
        handle.deps.policy.recent_calls.clear()
        handle.deps.policy.tool_timings.clear()

        self.console.print(
            Panel(
                f"step mode: prompt = {user_prompt!r}",
                title="/step",
                border_style="magenta",
            )
        )

        async with handle.agent.iter(
            user_prompt=user_prompt,
            message_history=handle.history,
            deps=handle.deps,
        ) as agent_run:
            node = agent_run.next_node
            step = 0
            while True:
                step += 1
                self._render_node(step, node)
                if hasattr(agent_run, "result") and agent_run.result is not None:
                    self.console.print(Panel("run complete.", border_style="green"))
                    return agent_run.result
                cont = Confirm.ask("advance?", console=self.console, default=True)
                if not cont:
                    self.console.print("[yellow]step mode aborted.[/]")
                    return None
                try:
                    node = await agent_run.next(node)
                except StopAsyncIteration:
                    self.console.print(Panel("iterator exhausted.", border_style="green"))
                    return agent_run.result

    def _render_node(self, step: int, node: Any) -> None:
        kind = type(node).__name__
        body = f"[bold]{kind}[/]\n\n{repr(node)[:400]}"
        self.console.print(Panel(body, title=f"step {step}", border_style="cyan"))
