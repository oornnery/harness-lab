from __future__ import annotations

import asyncio
import contextlib
import signal
import time

from rich.console import Console

from ..agent import AgentBuilder, AgentHandle
from ..context import ContextBuilder
from ..model import HarnessSettings, ModelAdapter
from ..policy import HarnessDeps, RuntimePolicy
from ..sessions import SessionStore
from .commands import ExtensionState, build_command_index
from .renderer import StreamRenderer
from .turn import TurnRunner


def _configure_logfire(settings: HarnessSettings) -> None:
    if not settings.logfire_enable:
        return
    import logfire

    logfire.configure(send_to_logfire="if-token-present")
    logfire.instrument_pydantic_ai()
    if settings.logfire_capture_http:
        logfire.instrument_httpx(capture_all=True)


class HarnessCliApp:
    def __init__(self) -> None:
        self.console = Console()
        self.settings = HarnessSettings()
        _configure_logfire(self.settings)
        self.model_adapter = ModelAdapter(self.settings)
        self.session_store = SessionStore(self.settings.resolved_session_dir())
        self.renderer = StreamRenderer(self.console, self.settings)
        self.builder = AgentBuilder(self.settings, self.model_adapter, self.session_store)
        self.turn_runner = TurnRunner(self.renderer, self.settings)
        self.commands = build_command_index()

    async def setup(self) -> tuple[AgentHandle, ExtensionState]:
        self.renderer.boot_panel(self.model_adapter)

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

        handle = self.builder.setup(deps, history)

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
            handle=handle,
            builder=self.builder,
            renderer=self.renderer,
        )
        return handle, state

    async def run(self) -> None:
        handle, ext_state = await self.setup()

        self.console.print(
            "[dim]commands:[/] /help /agent /mode /attach /step /context /tools "
            "/session /fork /replay /clear /compact /resume /quit"
        )

        # Handle SIGINT cleanly
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        def _signal_handler() -> None:
            shutdown_event.set()
            self.console.print("\n[dim]bye.[/]")

        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGINT, _signal_handler)

        try:
            while not shutdown_event.is_set():
                try:
                    # Print colored prompt, then input for clean Ctrl+C
                    self.console.print(
                        self.renderer.prompt_prefix(handle.persona.name), end=""
                    )
                    user_input = await asyncio.to_thread(input, "")
                except (EOFError, KeyboardInterrupt):
                    self.console.print("\n[dim]bye.[/]")
                    return
                except asyncio.CancelledError:
                    self.console.print("\n[dim]bye.[/]")
                    return
                if not user_input.strip():
                    continue
                if user_input.strip() in {"/quit", "/exit"}:
                    self.console.print("[dim]bye.[/]")
                    return

                if user_input.startswith("/"):
                    handle = await self._handle_command(handle, ext_state, user_input)
                    continue

                started = time.monotonic()
                attachments = ext_state.pending_attachments
                ext_state.pending_attachments = []
                result = await self.turn_runner.run(handle, user_input, attachments)
                elapsed = time.monotonic() - started
                if result is None:
                    continue
                handle.history = result.all_messages()
                await self.session_store.save_history(ext_state.session_id, handle.history)
                self.renderer.final_result(result.output)
                self.renderer.turn_stats(result, elapsed, self.model_adapter.model_name)
        finally:
            pass  # Rich Prompt handles history internally

    async def _handle_command(
        self, handle: AgentHandle, state: ExtensionState, raw: str
    ) -> AgentHandle:
        name, _, arg = raw[1:].partition(" ")
        command = self.commands.get(name)
        if not command:
            self.console.print(f"[red]Unknown command:[/] {name}")
            return handle
        await command.handler(state, arg)
        handle = state.handle or handle
        handle.history = await self.session_store.load_history(state.session_id)
        state.handle = handle
        return handle

def main() -> None:
    asyncio.run(HarnessCliApp().run())


if __name__ == "__main__":
    main()
