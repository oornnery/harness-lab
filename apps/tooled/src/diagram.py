from typing import Any

import networkx as nx
from netext import ArrowTip, ConsoleGraph, EdgeRoutingMode, EdgeSegmentDrawingMode
from netext.layout_engines import LayoutDirection, SugiyamaLayout
from rich import box
from rich.style import Style
from rich.text import Text

from .core.utils import console


def _render_label(_node_str: str, data: dict[str, Any], content_style: Style) -> Text:
    return Text(data.get("label", _node_str), style=content_style, justify="center")


FLOW_NODES: dict[str, tuple[str, str]] = {
    "User": ("green", "User"),
    "prompt.py": ("cyan", "prompt.py\nreadline + multi-line"),
    "commands.py": ("cyan", "commands.py\nslash dispatch\n+ Confirm gates"),
    "main.py": ("cyan", "main.py\nREPL + streaming\n+ --role override"),
    "SessionState": ("white", "SessionState\nparams, stream, current_id"),
    "config.py": ("yellow", "config.py\nRuntimeConfig TOML\nenv fallback"),
    "providers.py": ("yellow", "providers.py\nProvider registry\nOpenAI-compat"),
    "agent.py": ("magenta", "agent.py\ntool loop + compact\n+ ModelRetry + RunContext"),
    "Provider": ("yellow", "Provider\nOpenAI-compatible"),
    "policy.py": ("red", "policy.py\nallow/confirm/deny\n+ conditions"),
    "hooks.py": ("red", "hooks.py\npre/post hooks\nasync + per-agent"),
    "tools/": ("magenta", "tools/\n@tool registry + Toolset\nfs/shell/web/agent"),
    "memory.py": ("blue", "memory.py\n3-tier + memory agent\nremember/recall"),
    "session.py": ("blue", "session.py\nautosave + transcript\n+ export"),
    "./.tooled/": ("blue", "./.tooled/\nconfig / sessions\nmemory / policy"),
    "utils.py": ("grey50", "utils.py\nconsole + logger\n+ thinking_progress"),
}

FLOW_EDGES: list[tuple[str, str, str, dict]] = [
    ("User", "prompt.py", "stdin", {}),
    ("prompt.py", "commands.py", "/cmd", {}),
    ("prompt.py", "main.py", "text", {}),
    ("main.py", "config.py", "", {}),
    ("config.py", "providers.py", "register", {}),
    ("main.py", "agent.py", "", {}),
    ("agent.py", "Provider", "POST /chat/completions", {}),
    ("Provider", "agent.py", "SSE / JSON", {}),
    ("agent.py", "policy.py", "gate()", {}),
    ("policy.py", "hooks.py", "allow", {}),
    ("hooks.py", "tools/", "pre", {}),
    ("tools/", "memory.py", "remember/recall", {}),
    ("tools/", "hooks.py", "post", {}),
    ("hooks.py", "agent.py", "results", {}),
    ("agent.py", "session.py", "", {}),
    ("session.py", "./.tooled/", "", {}),
    ("agent.py", "memory.py", "async post-turn", {"$style": Style(color="grey50", dim=True)}),
]

UTILS_TARGETS: tuple[str, ...] = ("main.py", "agent.py", "session.py", "memory.py", "tools/")

LIFECYCLE_NODES: dict[str, tuple[str, str]] = {
    "startup": ("green", "startup\nparse argv + load config"),
    "session?": ("yellow", "session?"),
    "load_session": ("cyan", "load_session\nread JSON"),
    "latest_session_id + load_session": ("cyan", "latest_session_id\n+ load_session"),
    "ensure_session_id": ("cyan", "ensure_session_id\nnew uuid"),
    "compact?": ("yellow", "compact?"),
    "agent.compact": ("magenta", "agent.compact\nsummarize + keep N\nuses role=compact"),
    "load_medium_memory": ("blue", "load_medium_memory\ninject into instructions"),
    "REPL": ("cyan", "REPL\nprompt / dispatch / turn"),
    "tool loop": ("magenta", "tool loop\npolicy -> hooks -> dispatch\nparallel + confirm"),
    "memory agent": ("blue", "memory agent\nrole=memory\nfire-and-forget"),
    "autosave overwrite": ("blue", "autosave\noverwrite sessions/<id>.json"),
    "final autosave": ("blue", "final autosave\nsave + history"),
}

LIFECYCLE_LABEL_NODES: tuple[str, ...] = (
    "--session id",
    "--continue",
    "none",
    "yes",
    "no",
    "each turn",
    "tool_calls",
    "plain reply",
    "quit",
)

LIFECYCLE_EDGES: list[tuple[str, str, str, dict]] = [
    ("startup", "session?", "", {}),
    ("session?", "--session id", "", {}),
    ("--session id", "load_session", "", {}),
    ("session?", "--continue", "", {}),
    ("--continue", "latest_session_id + load_session", "", {}),
    ("session?", "none", "", {}),
    ("none", "ensure_session_id", "", {}),
    ("load_session", "compact?", "", {}),
    ("latest_session_id + load_session", "compact?", "", {}),
    ("compact?", "yes", "", {}),
    ("yes", "agent.compact", "", {}),
    ("compact?", "no", "", {}),
    ("no", "load_medium_memory", "", {}),
    ("agent.compact", "load_medium_memory", "", {}),
    ("ensure_session_id", "load_medium_memory", "", {}),
    ("load_medium_memory", "REPL", "", {}),
    ("REPL", "tool loop", "", {}),
    ("tool loop", "tool_calls", "", {}),
    ("tool_calls", "tool loop", "loop", {}),
    ("tool loop", "plain reply", "", {}),
    ("plain reply", "memory agent", "async", {}),
    ("memory agent", "autosave overwrite", "", {}),
    ("autosave overwrite", "REPL", "", {}),
    ("REPL", "quit", "", {}),
    ("quit", "final autosave", "", {}),
]


def _build(nodes: dict[str, tuple[str, str]], edges: list[tuple[str, str, str, dict]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for name in nodes:
        g.add_node(name)
    for src, dst, label, extra in edges:
        attrs: dict = {"$end-arrow-tip": ArrowTip.ARROW}
        if label:
            attrs["$label"] = label
        attrs.update(extra)
        g.add_edge(src, dst, **attrs)
    return g


def _build_flow() -> nx.DiGraph:
    g = _build(FLOW_NODES, FLOW_EDGES)
    for target in UTILS_TARGETS:
        g.add_edge(
            "utils.py",
            target,
            **{
                "$style": Style(color="grey30", dim=True),
                "$dash-pattern": [2, 2],
                "$end-arrow-tip": ArrowTip.NONE,
            },
        )
    return g


def _style(g: nx.DiGraph, nodes: dict[str, tuple[str, str]], label_nodes: tuple[str, ...] = ()) -> None:
    nx.set_node_attributes(g, "box", "$shape")
    nx.set_node_attributes(g, box.ROUNDED, "$box-type")
    nx.set_node_attributes(g, _render_label, "$content-renderer")
    nx.set_node_attributes(g, 1, "$margin")
    for node, (color, content) in nodes.items():
        g.nodes[node]["label"] = content
        g.nodes[node]["$style"] = Style(color=color, bold=True)
        g.nodes[node]["$content-style"] = Style(color=color)
    for node in label_nodes:
        if node not in g:
            continue
        g.nodes[node]["label"] = node
        g.nodes[node]["$style"] = Style(color="grey70")
        g.nodes[node]["$content-style"] = Style(color="grey70", italic=True)
        g.nodes[node]["$box-type"] = box.MINIMAL
        g.nodes[node]["$margin"] = 0

    nx.set_edge_attributes(g, EdgeRoutingMode.ORTHOGONAL, "$edge-routing-mode")
    nx.set_edge_attributes(g, EdgeSegmentDrawingMode.BOX_ROUNDED, "$edge-segment-drawing-mode")


def _render_graph(g: nx.DiGraph, title: str, direction: LayoutDirection) -> None:
    console.print(f"\n[bold]{title}[/bold]\n")
    console.print(ConsoleGraph(g, layout_engine=SugiyamaLayout(direction=direction)))


def render(which: str = "all") -> None:
    if which in ("flow", "all"):
        flow = _build_flow()
        _style(flow, FLOW_NODES)
        _render_graph(flow, "Module flow", LayoutDirection.TOP_DOWN)
    if which in ("lifecycle", "all"):
        nodes = dict(LIFECYCLE_NODES)
        for name in LIFECYCLE_LABEL_NODES:
            nodes.setdefault(name, ("grey70", name))
        lifecycle = _build(nodes, LIFECYCLE_EDGES)
        _style(lifecycle, LIFECYCLE_NODES, LIFECYCLE_LABEL_NODES)
        _render_graph(lifecycle, "Session lifecycle", LayoutDirection.TOP_DOWN)
