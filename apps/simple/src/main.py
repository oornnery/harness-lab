from dotenv import load_dotenv
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from .agent import Agent, AgentConfig, AgentError, ChatParams, ChatResponse
from .commands import build_help, dispatch, status_banner
from .prompt import cancel_turn, init_readline, read_input, save_history
from .session import SessionState, autosave_session, log_turn


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


def _run_stream_turn(console: Console, agent: Agent, user_input: str, params: ChatParams | None = None) -> ChatResponse:
    content_buf: list[str] = []
    thinking_buf: list[str] = []
    thinking_live: Live | None = None
    agent_live: Live | None = None

    def on_reasoning(piece: str) -> None:
        nonlocal thinking_live
        if thinking_live is None:
            status.stop()
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
                status.stop()
            agent_live = Live(Markdown(""), console=console, refresh_per_second=15)
            agent_live.start()
        content_buf.append(piece)
        agent_live.update(Markdown(f"**Agent:** {''.join(content_buf)}"))

    status = console.status("[bold yellow]Thinking...[/bold yellow]")
    status.start()
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
            status.stop()
    return resp


def _run_turn(console: Console, agent: Agent, user_input: str, params: ChatParams | None = None) -> ChatResponse:
    with console.status("[bold yellow]Thinking...[/bold yellow]"):
        resp = agent.chat(user_input, params=params)
    if resp.reasoning:
        console.print()
        console.print("[dim italic]Thinking:[/dim italic]")
        console.print(Markdown(resp.reasoning), style="dim")
        console.print()
    console.print(Markdown(f"**Agent:** {resp.content}"))
    return resp


def main() -> None:
    load_dotenv(".env")
    console = Console()
    state = SessionState()
    init_readline()

    console.print("[bold green]Welcome to the Simple Agent![/bold green]")
    console.print()
    console.print("[bold]Commands:[/bold]")
    console.print(build_help())
    console.print()
    console.print(status_banner(state))
    console.print()

    with Agent(config=AgentConfig()) as agent:
        try:
            while True:
                try:
                    user_input = read_input(console)
                except KeyboardInterrupt:
                    break
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    if dispatch(console, agent, state, user_input):
                        break
                    continue

                try:
                    run = _run_stream_turn if state.stream_mode else _run_turn
                    resp = run(console, agent, user_input, params=state.params)
                    log_turn("user", user_input)
                    log_turn("assistant", resp.content)
                    console.print()
                    console.print(build_info(resp))
                except KeyboardInterrupt:
                    console.print(cancel_turn(agent))
                    continue
                except AgentError as e:
                    console.print(cancel_turn(agent))
                    if e.status_code is not None:
                        console.print(f"[bold red]HTTP {e.status_code}:[/bold red] {e}")
                    else:
                        console.print(f"[bold red]Erro:[/bold red] {e}")
        finally:
            path = autosave_session(agent, state)
            if path is not None:
                console.print(f"\n[dim]Session saved: {path.stem}[/dim]")
            save_history()
            console.print("\n[bold red]Goodbye![/bold red]")


if __name__ == "__main__":
    main()
