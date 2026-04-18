from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.prompt import Confirm
from rich.table import Table

from .core.agents import Agent, Thinking
from .core.hooks import hooks_list
from .core.memory import forget, memory_clear, memory_list
from .core.memory import recall as mem_recall
from .core.memory import remember as mem_remember
from .core.policy import save_policy
from .core.providers import get_provider, list_providers
from .core.session import (
    SessionState,
    export_markdown,
    list_sessions,
    load_session,
    reset_sessions,
)
from .core.tool import registry_list
from .core.utils import console, thinking_progress
from .diagram import render

type MaybeAsync = bool | Coroutine[Any, Any, bool]
Command = Callable[["Agent[Any]", SessionState, str], MaybeAsync]
_REGISTRY: dict[str, Command] = {}

COMMANDS_HELP: list[tuple[str, str]] = [
    ("/help", "show this help"),
    ("/stream", "toggle streaming on/off"),
    ("/thinking [low|medium|high|off]", "show or set reasoning effort"),
    ("/usage", "show session token usage"),
    ("/clear", "clear current history and usage"),
    ("/session [<id>|reset]", "list/load sessions; reset deletes all"),
    ("/set <key> [value]", "set chat param (unset if value omitted)"),
    ("/params", "show current params"),
    ("/model [<id>]", "list models or switch"),
    ("/instructions [<text>]", "show or set system instructions"),
    ("/compact [<keep>|undo]", "summarize history, keep last N; undo last compact"),
    ("/retry", "drop last assistant reply and re-run last user turn"),
    ("/edit", "drop last assistant reply and re-edit last user turn"),
    ("/history [N]", "show last N messages"),
    ("/export [path]", "export conversation as markdown"),
    ("/tools [enable|disable <name>]", "list tools or enable/disable by name"),
    ("/memory [list|recall <q>|add <text>|clear]", "manage memory tiers"),
    ("/policy [show|allow|confirm|deny|condition <tool> [pattern]]", "view or update tool policy"),
    ("/config", "show current TOML config, roles, providers"),
    ("/provider [<name>]", "list or switch active provider"),
    ("/role [<name>]", "show or switch agent role (hot-swap model)"),
    ("/hooks", "list registered hooks"),
    ("/diagram [flow|lifecycle|all]", "render architecture diagrams"),
    ("/quit, /exit", "leave chat (autosaves)"),
]


def build_help() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    for cmd, desc in COMMANDS_HELP:
        table.add_row(cmd, f"-- {desc}")
    return table


PARAM_PARSERS: dict[str, Callable[[str], Any]] = {
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "seed": int,
    "reasoning_effort": Thinking,
}

KEEP_LAST_DEFAULT = 4
HISTORY_PREVIEW_LIMIT = 10
CONTENT_PREVIEW = 200


def register(*names: str) -> Callable[[Command], Command]:
    def deco(fn: Command) -> Command:
        for n in names:
            _REGISTRY[n] = fn
        return fn

    return deco


def known_commands() -> list[str]:
    return sorted(_REGISTRY)


async def dispatch(agent: Agent[Any], state: SessionState, user_input: str) -> bool:
    cmd, _, arg = user_input.strip().partition(" ")
    handler = _REGISTRY.get(cmd.lower())
    if handler is None:
        console.print(f"[bold red]Unknown command:[/bold red] {user_input} [dim](type /help)[/dim]")
        return False
    result = handler(agent, state, arg.strip())
    if asyncio.iscoroutine(result):
        return await result
    return result  # type: ignore[return-value]


def status_banner(state: SessionState, agent: Agent[Any] | None = None) -> str:
    thinking = state.params.get("reasoning_effort") or "off"
    parts: list[str] = []
    if agent is not None:
        sid = state.current_id or "new"
        parts.append(f"session={sid}")
        parts.append(f"provider={agent.config.provider.name}")
        parts.append(f"model={agent.config.model}")
    parts.append(f"stream={'on' if state.stream_mode else 'off'}")
    parts.append(f"thinking={thinking}")
    if agent is not None:
        parts.append(f"turns={agent.turns}")
        parts.append(f"tokens={agent.session_usage.total_tokens}")
    return f"[dim]{' | '.join(parts)}[/dim]"


@register("/quit", "/exit")
def _cmd_quit(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    return True


@register("/help")
def _cmd_help(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    console.print("[bold]Commands:[/bold]")
    console.print(build_help())
    return False


@register("/stream")
def _cmd_stream(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    state.stream_mode = not state.stream_mode
    console.print(status_banner(state, agent))
    return False


@register("/thinking")
def _cmd_thinking(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    levels = ", ".join(t.value for t in Thinking)
    arg = arg.lower()
    if not arg:
        current = state.params.get("reasoning_effort") or "off"
        console.print(f"[dim]thinking = {current}[/dim] [dim](off, {levels})[/dim]")
        return False
    if arg == "off":
        state.params.pop("reasoning_effort", None)
    else:
        try:
            state.params["reasoning_effort"] = Thinking(arg)
        except ValueError:
            console.print(f"[bold red]Invalid level:[/bold red] {arg}. Use: off, {levels}")
            return False
    console.print(status_banner(state, agent))
    return False


@register("/usage")
def _cmd_usage(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    u = agent.session_usage
    console.print(
        f"[bold]Session usage[/bold] ({agent.turns} turns)\n"
        f"  prompt:     {u.prompt_tokens}\n"
        f"  completion: {u.completion_tokens}\n"
        f"  reasoning:  {u.reasoning_tokens}\n"
        f"  [bold]total:      {u.total_tokens}[/bold]"
    )
    return False


@register("/clear")
def _cmd_clear(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if not Confirm.ask("[yellow]Clear history?[/yellow]", console=console, default=False):
        console.print("[dim]cancelled[/dim]")
        return False
    agent.reset()
    console.print("[dim]cleared.[/dim]")
    return False


@register("/session")
def _cmd_session(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if not arg:
        files = list_sessions()
        if not files:
            console.print("[dim]No sessions.[/dim]")
            return False
        for f in files:
            data = json.loads(f.read_text())
            preview = next(
                (m["content"][:60] for m in data["messages"] if m.get("role") == "user"),
                "(empty)",
            )
            created = data.get("created_at", "")
            marker = " [bold green](current)[/bold green]" if f.stem == state.current_id else ""
            console.print(f"  [cyan]{f.stem}[/cyan]{marker} [dim]{created}[/dim] turns={data['turns']} -- {preview}")
        return False
    if arg == "reset":
        if not Confirm.ask("[yellow]Delete all sessions?[/yellow]", console=console, default=False):
            console.print("[dim]cancelled[/dim]")
            return False
        n = reset_sessions()
        console.print(f"[dim]Deleted {n}.[/dim]")
        return False
    if not load_session(arg, agent, state):
        console.print(f"[red]Session not found:[/red] {arg}")
        return False
    console.print(f"[dim]Loaded session {arg} ({len(agent.messages)} messages)[/dim]")
    return False


@register("/set")
def _cmd_set(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    key, _, raw = arg.partition(" ")
    parser = PARAM_PARSERS.get(key)
    if parser is None:
        console.print(f"[red]Unknown param:[/red] {key}. Known: {', '.join(PARAM_PARSERS)}")
        return False
    if not raw:
        state.params.pop(key, None)
        console.print(f"[dim]{key} unset[/dim]")
        return False
    try:
        value = parser(raw.strip())
    except (ValueError, TypeError) as e:
        console.print(f"[red]Invalid value:[/red] {e}")
        return False
    state.params[key] = value
    console.print(f"[dim]{key} = {value}[/dim]")
    return False


@register("/params")
def _cmd_params(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if not state.params:
        console.print("[dim]No params set (defaults).[/dim]")
        return False
    for k, v in state.params.items():
        console.print(f"  {k}: {v}")
    return False


@register("/model")
async def _cmd_model(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    current = agent.config.model
    if not arg:
        preset_names: set[str] = set()
        prov_spec = agent.runtime.providers.get(agent.config.provider.name) if agent.runtime else None
        if prov_spec and prov_spec.models:
            console.print("[bold]config:[/bold]")
            for m in prov_spec.models:
                preset_names.add(m.name)
                tags: list[str] = []
                if m.name == prov_spec.default_model:
                    tags.append("[yellow]default[/yellow]")
                if m.name == current:
                    tags.append("[bold green]current[/bold green]")
                settings = []
                if m.temperature is not None:
                    settings.append(f"temp={m.temperature}")
                if m.max_tokens is not None:
                    settings.append(f"max_tokens={m.max_tokens}")
                if m.thinking is not None:
                    settings.append(f"thinking={m.thinking}")
                tag_str = " ".join(tags)
                set_str = f" [dim]{', '.join(settings)}[/dim]" if settings else ""
                console.print(f"  {m.name} {tag_str}{set_str}")
        try:
            api_models = await agent.models()
        except Exception:
            api_models = []
        if api_models:
            non_preset = [m for m in api_models if m not in preset_names]
            if non_preset or not preset_names:
                console.print("[bold]api:[/bold]")
                for mid in api_models:
                    if mid in preset_names:
                        continue
                    marker = " [bold green](current)[/bold green]" if mid == current else ""
                    console.print(f"  {mid}{marker}")
        if not preset_names and not api_models:
            console.print("[dim]No models available.[/dim]")
        return False
    agent.config.model = arg
    if agent.runtime:
        prov_spec = agent.runtime.providers.get(agent.config.provider.name)
        if prov_spec:
            preset = prov_spec.find_model(arg)
            if preset:
                if preset.temperature is not None:
                    agent.config.temperature = preset.temperature
                if preset.max_tokens is not None:
                    agent.config.max_tokens = preset.max_tokens
                if preset.thinking is not None:
                    state.params["reasoning_effort"] = Thinking(preset.thinking)
    console.print(f"[dim]model = {arg}[/dim]")
    return False


@register("/instructions")
def _cmd_instructions(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if not arg:
        current = agent.config.instructions
        if current:
            console.print(f"[dim]Current instructions:[/dim]\n{current}")
        else:
            console.print("[dim]No instructions set.[/dim]")
        return False
    agent.config.instructions = arg.strip()
    console.print("[dim]Instructions set.[/dim]")
    return False


@register("/compact")
async def _cmd_compact(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if arg.lower() == "undo":
        restored = agent.undo_compact()
        if restored is None:
            console.print("[dim]Nothing to undo.[/dim]")
        else:
            console.print(f"[dim]Restored {restored} messages from last compact.[/dim]")
        return False
    keep = int(arg) if arg.isdigit() else KEEP_LAST_DEFAULT
    with thinking_progress("Compacting...") as progress:
        progress.add_task("compacting", total=None)
        result = await agent.compact(keep)
    if result is None:
        console.print("[dim]Nothing to compact.[/dim]")
        return False
    console.print(f"[dim]Compacted {result.summarized} messages into summary ({result.tokens_used} tokens used).[/dim]")
    return False


@register("/retry")
def _cmd_retry(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    text = agent.pop_last_user()
    if text is None:
        console.print("[dim]Nothing to retry.[/dim]")
        return False
    state.pending_input = text
    preview = text if len(text) <= 80 else text[:77] + "..."
    console.print(f"[dim]Retrying: {preview}[/dim]")
    return False


@register("/edit")
def _cmd_edit(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    text = agent.pop_last_user()
    if text is None:
        console.print("[dim]Nothing to edit.[/dim]")
        return False
    state.prefill = text
    console.print("[dim]Edit previous input (Enter to submit).[/dim]")
    return False


@register("/history")
def _cmd_history(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    n = int(arg) if arg.isdigit() and int(arg) > 0 else HISTORY_PREVIEW_LIMIT
    msgs = agent.messages[-n:]
    if not msgs:
        console.print("[dim]No messages.[/dim]")
        return False
    for m in msgs:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if len(content) > CONTENT_PREVIEW:
            content = content[:CONTENT_PREVIEW] + "..."
        console.print(f"[cyan]{role}[/cyan]:")
        console.print(Markdown(content))
        console.print()
    return False


@register("/export")
def _cmd_export(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    dest = Path(arg).expanduser() if arg else None
    path = export_markdown(agent, state, dest)
    console.print(f"[dim]Exported to {path}[/dim]")
    return False


@register("/tools")
def _cmd_tools(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    sub, _, name = arg.partition(" ")
    sub = sub.lower()
    if sub in ("enable", "disable") and name.strip():
        name = name.strip()
        if sub == "disable":
            agent.disabled_tools.add(name)
        else:
            agent.disabled_tools.discard(name)
        console.print(f"[dim]{name} {sub}d[/dim]")
        return False
    entries = registry_list()
    if not entries:
        console.print("[dim]No tools registered.[/dim]")
        return False
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="dim")
    table.add_column(style="yellow", no_wrap=True)
    for e in entries:
        status = "disabled" if e["name"] in agent.disabled_tools else ""
        table.add_row(e["name"], e["desc"] or "", status)
    console.print(table)
    return False


@register("/memory")
def _cmd_memory(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    sub, _, rest = arg.partition(" ")
    sub = sub.lower()
    if not sub or sub == "list":
        console.print(memory_list())
        return False
    if sub == "recall":
        if not rest:
            console.print("[red]Usage:[/red] /memory recall <query>")
            return False
        console.print(mem_recall(rest.strip()))
        return False
    if sub == "add":
        if not rest:
            console.print("[red]Usage:[/red] /memory add <text>")
            return False
        result = mem_remember(rest.strip(), [], "medium")
        console.print(f"[dim]{result}[/dim]")
        return False
    if sub == "clear":
        if not Confirm.ask("[yellow]Clear all memory?[/yellow]", console=console, default=False):
            console.print("[dim]cancelled[/dim]")
            return False
        n = memory_clear()
        console.print(f"[dim]Cleared {n} memory file(s).[/dim]")
        return False
    if sub == "forget":
        if not rest:
            console.print("[red]Usage:[/red] /memory forget <id>")
            return False
        if forget(rest.strip()):
            console.print(f"[dim]Deleted entry {rest.strip()}.[/dim]")
        else:
            console.print(f"[red]Entry not found:[/red] {rest.strip()}")
        return False
    console.print(f"[red]Unknown subcommand:[/red] {sub}. Use: list, recall, add, clear, forget")
    return False


@register("/policy")
def _cmd_policy(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    sub, _, rest = arg.partition(" ")
    sub = sub.lower()
    if not sub or sub == "show":
        p = agent.policy
        console.print("[bold]Policy[/bold] (default=confirm for unknown)")
        console.print(f"  allow:   {sorted(p.allow) or '(none)'}")
        console.print(f"  confirm: {sorted(p.confirm) or '(none)'}")
        console.print(f"  deny:    {sorted(p.deny) or '(none)'}")
        if p.conditions:
            console.print("[bold]Conditions[/bold] (substring match on args)")
            for tool_name, patterns in p.conditions.items():
                console.print(f"  {tool_name}: {patterns}")
        return False
    if sub in ("allow", "confirm", "deny"):
        tool_name = rest.strip()
        if not tool_name:
            console.print(f"[red]Usage:[/red] /policy {sub} <tool>")
            return False
        new_policy = agent.policy.with_verdict(tool_name, sub)  # type: ignore[arg-type]
        agent.set_policy(new_policy)
        save_policy(new_policy)
        console.print(f"[dim]{tool_name} -> {sub}[/dim]")
        return False
    if sub == "condition":
        tool_name, _, pattern = rest.partition(" ")
        tool_name = tool_name.strip()
        pattern = pattern.strip()
        if not tool_name or not pattern:
            console.print("[red]Usage:[/red] /policy condition <tool> <substring>")
            return False
        new_policy = agent.policy.with_condition(tool_name, pattern)
        agent.set_policy(new_policy)
        save_policy(new_policy)
        console.print(f"[dim]condition added: {tool_name} -> {pattern!r}[/dim]")
        return False
    console.print(f"[red]Unknown subcommand:[/red] {sub}. Use: show, allow, confirm, deny, condition")
    return False


@register("/hooks")
def _cmd_hooks(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    rows = hooks_list()
    if agent._local_hooks:
        rows.extend(
            {"phase": phase, "name": getattr(fn, "__name__", repr(fn)), "tool": scope or "*"}
            for phase, fn, scope in agent.local_hooks
        )
    if not rows:
        console.print("[dim]No hooks registered.[/dim]")
        return False
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="yellow", no_wrap=True)
    table.add_column(style="dim")
    for r in rows:
        table.add_row(r["phase"], r["name"], r["tool"])
    console.print(table)
    return False


@register("/config")
def _cmd_config(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if agent.runtime is None:
        console.print("[dim]No TOML config loaded (single-provider mode).[/dim]")
        return False
    rt = agent.runtime
    console.print(f"[bold]default_role:[/bold] {rt.default_role}")
    console.print("[bold]providers:[/bold]")
    for name, spec in rt.providers.items():
        url_src = spec.base_url or f"env:{spec.base_url_env}"
        console.print(f"  {name}: {url_src} key_env={spec.api_key_env}")
    console.print("[bold]roles:[/bold]")
    for name, role in rt.roles.items():
        model_src = role.model or f"env:{role.model_env}"
        console.print(f"  {name}: provider={role.provider} model={model_src}")
    if rt.tools:
        console.print("[bold]tool scoping:[/bold]")
        for role, spec in rt.tools.items():
            console.print(f"  {role}: allow={spec.allow} deny={spec.deny}")
    return False


@register("/provider")
async def _cmd_provider(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if agent.runtime is None:
        console.print("[dim]No TOML config loaded (single-provider mode).[/dim]")
        return False
    available = list_providers()
    if not arg.strip():
        current = agent.config.provider.name
        for name in available:
            marker = " <--" if name == current else ""
            p = get_provider(name)
            console.print(f"  {name}: {p.base_url}{marker}")
        return False
    name = arg.strip()
    if name not in available:
        console.print(f"[red]Unknown provider:[/red] {name}. Available: {', '.join(available)}")
        return False
    new_prov = get_provider(name)
    agent.config.provider = new_prov
    prov_spec = agent.runtime.providers.get(name)
    new_model: str | None = None
    if prov_spec and prov_spec.default_model:
        new_model = prov_spec.default_model
    else:
        for _role, role_spec in agent.runtime.roles.items():
            if role_spec.provider == name:
                with contextlib.suppress(ValueError):
                    new_model = role_spec.resolve_model()
                break
    if new_model:
        agent.config.model = new_model
    await agent.client.aclose()
    agent.client = new_prov.build_client(agent.config.connect_timeout, agent.config.read_timeout)
    console.print(f"[dim]switched to provider={name} model={agent.config.model} base_url={new_prov.base_url}[/dim]")
    return False


@register("/role")
async def _cmd_role(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    if agent.runtime is None:
        console.print("[dim]No TOML config loaded (single-provider mode).[/dim]")
        return False
    if not arg.strip():
        # show current role info
        console.print(f"[dim]Current model: {agent.config.model} (provider: {agent.config.provider.name})[/dim]")
        console.print(f"[dim]Available roles: {', '.join(agent.runtime.roles)}[/dim]")
        return False
    role_name = arg.strip()
    if role_name not in agent.runtime.roles:
        console.print(f"[red]Unknown role:[/red] {role_name}. Available: {', '.join(agent.runtime.roles)}")
        return False
    new_cfg = agent.config_for_role(role_name)
    await agent.aclose()
    agent.config = new_cfg
    agent.client = agent.config.provider.build_client(agent.config.connect_timeout, agent.config.read_timeout)
    console.print(f"[dim]switched to role={role_name} model={new_cfg.model} provider={new_cfg.provider.name}[/dim]")
    return False


@register("/diagram")
async def _cmd_diagram(agent: Agent[Any], state: SessionState, arg: str) -> bool:
    which = arg.strip() or "all"
    if which not in ("flow", "lifecycle", "all"):
        console.print(f"[red]Unknown diagram:[/red] {which}. Use flow, lifecycle, or all.")
        return False
    render(which)
    return False
