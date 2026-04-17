import argparse
import logging
import os

from dotenv import load_dotenv
from rich.console import Group
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text
from rich_argparse import RichHelpFormatter

from .agent import Agent, AgentConfig, AgentError, ChatParams, ChatResponse
from .commands import KEEP_LAST_DEFAULT, build_help, dispatch, status_banner
from .prompt import cancel_turn, init_readline, read_input, save_history
from .session import SessionState, autosave_session, ensure_session_id, latest_session_id, load_session, log_turn
from .utils import console, thinking_progress


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="simple", description="Tiny OpenAI-compatible chat harness", formatter_class=RichHelpFormatter)
    p.add_argument("--session", help="Resume session by id")
    p.add_argument("-c", "--continue", dest="resume", action="store_true", help="Resume the most recent session")
    p.add_argument("--compact", action="store_true", help="Compact history after resume")
    p.add_argument("--model", help="Override model for this run")
    p.add_argument("--instructions", help="Extra system instructions")
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "WARNING"), help="Python logging level (DEBUG, INFO, WARNING, ERROR)")
    p.add_argument("--no-stream", action="store_true", help="Disable streaming by default")
    p.add_argument("--diagram", choices=["flow", "lifecycle", "all"], nargs="?", const="all", help="Render architecture diagram and exit (flow | lifecycle | all)")
    return p.parse_args()


def build_info(resp: ChatResponse) -> Group:
    u = resp.usage
    extra = f" | Reasoning: {u.reasoning_tokens}" if u.reasoning_tokens else ""
    return Group(
        Text.from_markup(
            f"[dim]Model: {resp.model} | Tokens: {u.total_tokens} "
            f"(prompt={u.prompt_tokens}, completion={u.completion_tokens}){extra} | "
            f"Time: {resp.response_time}s | Finish: {resp.finish_reason or 'N/A'}[/dim]"
        ),
        Text("-" * 50),
    )


def _run_stream_turn(agent: Agent, user_input: str, params: ChatParams | None = None) -> ChatResponse:
    content_buf: list[str] = []
    thinking_buf: list[str] = []
    thinking_live: Live | None = None
    agent_live: Live | None = None

    progress = thinking_progress("Thinking...")
    progress.add_task("thinking", total=None)
    progress.start()

    def on_reasoning(piece: str) -> None:
        nonlocal thinking_live
        if thinking_live is None:
            progress.stop()
            console.print()
            console.print("[dim italic]Thinking:[/dim italic]")
            thinking_live = Live(Markdown("", style="dim"), console=console, refresh_per_second=15)
            thinking_live.start()
        thinking_buf.append(piece)
        thinking_live.update(Markdown("".join(thinking_buf), style="dim"))

    def on_content(piece: str) -> None:
        nonlocal agent_live
        if agent_live is None:
            if thinking_live is not None:
                thinking_live.stop()
                console.print()
            else:
                progress.stop()
            agent_live = Live(Markdown(""), console=console, refresh_per_second=15)
            agent_live.start()
        content_buf.append(piece)
        agent_live.update(Markdown(f"**Agent:** {''.join(content_buf)}"))

    try:
        resp = agent.chat_stream(
            user_input,
            on_content=on_content,
            on_reasoning=on_reasoning,
            params=params,
        )
    finally:
        if agent_live is not None:
            agent_live.stop()
        elif thinking_live is not None:
            thinking_live.stop()
        else:
            progress.stop()
    return resp


def _run_turn(agent: Agent, user_input: str, params: ChatParams | None = None) -> ChatResponse:
    with thinking_progress("Thinking...") as progress:
        progress.add_task("thinking", total=None)
        resp = agent.chat(user_input, params=params)
    if resp.reasoning:
        console.print()
        console.print("[dim italic]Thinking:[/dim italic]")
        console.print(Markdown(resp.reasoning), style="dim")
        console.print()
    console.print(Markdown(f"**Agent:** {resp.content}"))
    return resp


def main() -> None:
    args = _parse_args()
    load_dotenv(".env")
    logging.getLogger().setLevel(args.log_level.upper())

    if args.diagram:
        from .diagram import render as render_diagram

        render_diagram(args.diagram)
        return

    state = SessionState()
    if args.no_stream:
        state.stream_mode = False

    console.print("[bold green]Welcome to the Simple Agent![/bold green]")
    console.print()
    console.print("[bold]Commands:[/bold]")
    console.print(build_help())
    console.print()

    config = AgentConfig()
    if args.model:
        config.model = args.model
    if args.instructions:
        config.instructions = args.instructions.strip()

    with Agent(config=config) as agent:
        init_readline(agent)
        target_sid = args.session
        if not target_sid and args.resume:
            target_sid = latest_session_id()
            if target_sid is None:
                console.print("[yellow]No previous session to resume.[/yellow]")
        if target_sid:
            if load_session(target_sid, agent, state):
                console.print(f"[dim]Resumed session {target_sid} ({len(agent.messages)} messages)[/dim]")
                if args.compact:
                    with thinking_progress("Compacting...") as progress:
                        progress.add_task("compacting", total=None)
                        result = agent.compact(KEEP_LAST_DEFAULT)
                    if result is None:
                        console.print("[dim]Nothing to compact.[/dim]")
                    else:
                        console.print(f"[dim]Compacted {result.summarized} messages into summary ({result.tokens_used} tokens used).[/dim]")
            else:
                console.print(f"[red]Session not found:[/red] {target_sid}")
                ensure_session_id(state)
        else:
            ensure_session_id(state)

        console.print(status_banner(state, agent))
        console.print()

        try:
            while True:
                if state.pending_input is not None:
                    user_input = state.pending_input
                    state.pending_input = None
                    console.print(f"[bold blue]You (retry):[/bold blue] {user_input}")
                else:
                    prefill = state.prefill
                    state.prefill = ""
                    try:
                        user_input = read_input(prefill=prefill)
                    except KeyboardInterrupt:
                        break
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    if dispatch(agent, state, user_input):
                        break
                    continue

                try:
                    console.print()
                    run = _run_stream_turn if state.stream_mode else _run_turn
                    resp = run(agent, user_input, params=state.params)
                    log_turn(
                        "user",
                        user_input,
                        session_id=state.current_id,
                        model=agent.config.model,
                    )
                    log_turn(
                        "assistant",
                        resp.content,
                        session_id=state.current_id,
                        model=resp.model,
                        usage=resp.usage,
                        response_time=resp.response_time,
                    )
                    console.print()
                    console.print(build_info(resp))
                    autosave_session(agent, state)
                    console.print(status_banner(state, agent))
                except KeyboardInterrupt:
                    console.print(cancel_turn(agent))
                    continue
                except AgentError as e:
                    console.print(cancel_turn(agent))
                    msg = e.clean_message
                    if e.status_code is not None:
                        console.print(f"[bold red]HTTP {e.status_code}:[/bold red] {msg}")
                    else:
                        console.print(f"[bold red]Error:[/bold red] {msg}")
        finally:
            path = autosave_session(agent, state)
            if path is not None:
                console.print(f"\n[dim]Session saved: {path.stem}[/dim]")
            save_history()
            console.print("\n[bold red]Goodbye![/bold red]")


if __name__ == "__main__":
    main()
