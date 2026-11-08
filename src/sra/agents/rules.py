"""Rule-factor node: F1, F5, F7, F9, durable genre and saturation, all deterministic."""

from __future__ import annotations

from typing import Any

from sra import features
from sra.deps import Deps
from sra.state import FactorScore, PipelineState, SourceRef


def _by_tool(sources: list[SourceRef], *prefixes: str) -> list[SourceRef]:
    return [s for s in sources if s.tool.startswith(prefixes)] or list(sources)


def rule_factors(state: PipelineState, deps: Deps) -> dict[str, Any]:
    steam, rob = state["steam"], state["roblox"]
    store_src = _by_tool(steam.sources, "steam.store_item", "case")
    momentum_src = _by_tool(steam.sources, "steamcharts", "steam.review", "case")

    f1, r1 = features.f1_social(steam.categories, steam.tags)
    f5, r5 = features.f5_age_rules(steam.tags, steam.content_descriptors, steam.content_notes)
    f7, r7 = features.f7_momentum(
        steam.peak_ccu, steam.pct_change_7d, steam.review_acceleration, steam.is_free, steam.reviews_7d
    )
    f9, r9 = features.f9_window(steam.days_since_peak, rob.best_clone_players, rob.active_clones, rob.leader_players)
    durable, kind = features.durable_genre(steam.tags, steam.categories)
    saturated = features.is_saturated(rob.best_clone_players, rob.leader_players)

    factors = {
        "F1": FactorScore(factor="F1", score=f1, justification=r1, confidence="high", sources=store_src),
        "F5": FactorScore(factor="F5", score=f5, justification=r5, confidence="high", sources=store_src),
        "F7": FactorScore(factor="F7", score=f7, justification=r7, confidence="high", sources=momentum_src),
        "F9": FactorScore(
            factor="F9", score=f9, justification=r9, confidence="medium", sources=momentum_src + rob.sources
        ),
    }
    hints = features.llm_hints(steam.tags)
    deps.journal.log(
        "rule_factors",
        "computed",
        appid=state["appid"],
        data={
            "factors": {k: (v.score, v.justification) for k, v in factors.items()},
            "durable_genre": durable,
            "durable_genre_kind": kind,
            "saturated": saturated,
            "hints": hints,
        },
        sources=store_src + momentum_src + rob.sources,
    )
    return {
        "rule_factors": factors,
        "durable_genre": durable,
        "durable_genre_kind": kind,
        "saturated": saturated,
        "hints": hints,
    }
