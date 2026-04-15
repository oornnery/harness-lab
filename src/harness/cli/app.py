from __future__ import annotations

import asyncio
import contextlib
import time

from rich.console import Console
from src.agent import AgentBuilder, AgentHandle
from src.agent.watcher import PersonaWatcher
from src.background import BackgroundRunner
from src.context import ContextBuilder
from src.memory import MemoryStore
from src.model import HarnessSettings, ModelAdapter
from src.policy import HarnessDeps, RuntimePolicy
from src.scheduler import Scheduler
from src.session import UnifiedStore
from src.tools import TOOL_NAMES

from .commands import ExtensionState, build_command_index
from .turn import TurnRunner
from .ui.renderer import StreamRenderer


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
        self.session_store = UnifiedStore(self.settings.resolved_session_dir())
        self.renderer = StreamRenderer(self.console, self.settings)
        self.builder = AgentBuilder(self.settings, self.model_adapter, self.session_store)
        self.turn_runner = TurnRunner(self.renderer, self.settings)
        self.commands = build_command_index()

        self.memory_store = None
        if self.settings.enable_memory:
            self.memory_store = MemoryStore(
                self.settings.resolved_session_dir(),
                enable_embeddings=True,
            )

        self.background_runner = BackgroundRunner(self.builder, self.session_store)
        self._parent_deps: HarnessDeps | None = None
        self.scheduler = Scheduler(
            session_store=self.session_store,
            background_runner=self.background_runner,
            parent_deps_provider=self._current_parent_deps,
        )
        self._current_handle: AgentHandle | None = None
        self.persona_watcher: PersonaWatcher | None = None
        if self.settings.hot_reload_personas:
            self.persona_watcher = PersonaWatcher(on_reload=self._on_persona_reload)

    def _current_parent_deps(self) -> HarnessDeps:
        if self._parent_deps is None:
            raise RuntimeError("scheduler fired before CLI setup completed")
        return self._parent_deps

    async def _on_persona_reload(self, paths: set) -> None:
        handle = self._current_handle
        if handle is None:
            return
        try:
            new_handle = self.builder.rebuild(handle, handle.persona.name)
        except Exception as exc:
            self.console.print(f"[red]persona reload failed:[/] {exc}")
            return
        self._current_handle = new_handle
        names = ", ".join(sorted({p.stem for p in paths}))
        self.console.print(f"[bold cyan]\u276f personas reloaded:[/] {names}")

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
            model_adapter=self.model_adapter,
            memory_store=self.memory_store,
            background_runner=self.background_runner,
        )
        self._parent_deps = deps

        handle = self.builder.setup(deps, history)

        state = ExtensionState(
            console=self.console,
            deps=deps,
            session_store=self.session_store,
            known_tools=list(TOOL_NAMES),
            workspace_summary=workspace.prompt_summary(),
            handle=handle,
            builder=self.builder,
            renderer=self.renderer,
        )
        return handle, state

    async def run(self) -> None:
        handle, ext_state = await self.setup()
        self._current_handle = handle
        self.scheduler.start()
        if self.persona_watcher is not None:
            self.persona_watcher.start()

        self.console.print(
            "[dim]commands:[/] /help /agent /mode /attach /step /context /tools "
            "/session /fork /replay /clear /compact /resume /todos /jobs /schedule /quit"
        )

        try:
            while True:
                try:
                    await self._drain_background_banner()
                    self.console.print(self.renderer.prompt_prefix(handle.persona.name), end="")

                    def _read_input() -> str:
                        try:
                            return input("")
                        except KeyboardInterrupt:
                            raise EOFError from None  # Convert before asyncio sees it

                    user_input = await asyncio.to_thread(_read_input)
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
            if self.persona_watcher is not None:
                await self.persona_watcher.stop()
            await self.scheduler.stop()
            await self.background_runner.shutdown()

    async def _drain_background_banner(self) -> None:
        finished = await self.background_runner.drain_completed()
        if not finished:
            return
        ids = ", ".join(finished)
        self.console.print(f"[bold green]\u276f background done:[/] {ids} [dim](use /jobs)[/]")

    async def _sync_handle(self, handle: AgentHandle, state: ExtensionState) -> AgentHandle:
        """Synchronize handle state after command execution.

        If the command modified the handle (stored in state.handle), use it.
        Otherwise, reload history from the session store for the current handle.
        """
        handle = state.handle or handle
        handle.history = await self.session_store.load_history(state.session_id)
        state.handle = handle
        return handle

    async def _handle_command(
        self, handle: AgentHandle, state: ExtensionState, raw: str
    ) -> AgentHandle:
        name, _, arg = raw[1:].partition(" ")
        command = self.commands.get(name)
        if not command:
            self.console.print(f"[red]Unknown command:[/] {name}")
            return handle
        await command.handler(state, arg)
        return await self._sync_handle(handle, state)


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(HarnessCliApp().run())


if __name__ == "__main__":
    main()
