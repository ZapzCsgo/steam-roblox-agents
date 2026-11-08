"""LangGraph wiring: Collector -> Roblox Scout -> rules -> Analyst <-> Critic -> Scorer -> Reporter."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sra.agents.analyst import analyst
from sra.agents.collectors import collector, roblox_scout
from sra.agents.critic import critic, route_after_critic
from sra.agents.output import reporter, scorer
from sra.agents.rules import rule_factors
from sra.config import Settings
from sra.deps import Deps
from sra.llm import ScriptedChatModel, build_chat_model
from sra.skills import SkillRegistry
from sra.sources.http import HttpClient
from sra.state import PipelineState
from sra.trace import Journal

NodeFn = Callable[[PipelineState, Deps], dict[str, Any]]
NODES: dict[str, NodeFn] = {
    "collector": collector,
    "roblox_scout": roblox_scout,
    "rule_factors": rule_factors,
    "analyst": analyst,
    "critic": critic,
    "scorer": scorer,
    "reporter": reporter,
}


def _bind(fn: NodeFn, deps: Deps) -> RunnableLambda[PipelineState, dict[str, Any]]:
    """Close a node function over its dependencies (no globals, one Deps per run)."""

    def node(state: PipelineState) -> dict[str, Any]:
        return fn(state, deps)

    return RunnableLambda(node, name=fn.__name__)


def build_graph(deps: Deps) -> CompiledStateGraph:
    g = StateGraph(PipelineState)
    for name, fn in NODES.items():
        g.add_node(name, _bind(fn, deps))
    g.add_edge(START, "collector")
    g.add_edge("collector", "roblox_scout")
    g.add_edge("roblox_scout", "rule_factors")
    g.add_edge("rule_factors", "analyst")
    g.add_edge("analyst", "critic")
    g.add_conditional_edges(
        "critic",
        partial(route_after_critic, max_revisions=deps.settings.max_revisions),
        {"analyst": "analyst", "scorer": "scorer"},
    )
    g.add_edge("scorer", "reporter")
    g.add_edge("reporter", END)
    return g.compile()


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def make_deps(
    settings: Settings, run_id: str, *, journal_path: Path | None = None, reports_dir: Path | None = None
) -> Deps:
    http = HttpClient(mode=settings.http_mode, fixtures_dir=settings.fixtures_dir / "http")  # type: ignore[arg-type]
    return Deps(
        settings=settings,
        http=http,
        llm=build_chat_model(settings),
        skills=SkillRegistry.from_dir(settings.skills_dir),
        journal=Journal(journal_path, run_id),
        reports_dir=reports_dir,
    )


def run_candidate(
    graph: CompiledStateGraph, deps: Deps, appid: int, preset: dict[str, Any] | None = None
) -> PipelineState:
    """Run the full graph for one Steam app. preset can pre-fill steam/roblox snapshots (evaluation)."""
    if isinstance(deps.llm, ScriptedChatModel):
        deps.llm.reset()
    state: dict[str, Any] = {"run_id": deps.journal.run_id, "appid": appid, **(preset or {})}
    deps.journal.log("graph", "start", appid=appid, data={"preset": sorted(preset or {})})
    result: PipelineState = graph.invoke(state, {"recursion_limit": 25})  # type: ignore[assignment]
    return result


def mermaid() -> str:
    """Mermaid source of the compiled graph (used in the README)."""
    settings = Settings()
    deps = Deps(
        settings=settings,
        http=HttpClient(),
        llm=ScriptedChatModel(),
        skills=SkillRegistry([]),
        journal=Journal(None, "graph"),
    )
    return build_graph(deps).get_graph().draw_mermaid()
