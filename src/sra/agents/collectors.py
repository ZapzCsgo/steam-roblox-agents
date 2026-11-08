"""Collector and Roblox Scout agents: tool executors with a fixed plan.

These two agents call external tools but make no judgement call, so they run a
deterministic plan instead of letting an LLM pick tools. That keeps the
non-reproducible part of the pipeline small (Analyst and Critic only).
When the state already holds a snapshot (evaluation on historical cases), the
agent records that it reused it, with the case-file source.
"""

from __future__ import annotations

from typing import Any

from sra.deps import Deps
from sra.sources.roblox import RobloxTools
from sra.sources.steam import SteamTools
from sra.state import PipelineState


def collector(state: PipelineState, deps: Deps) -> dict[str, Any]:
    appid = state["appid"]
    if "steam" in state:
        snap = state["steam"]
        deps.journal.log(
            "collector",
            "snapshot_provided",
            appid=appid,
            data={"name": snap.name, "peak_ccu": snap.peak_ccu},
            sources=snap.sources,
        )
        return {}
    snap = SteamTools(deps.http).snapshot(appid)
    deps.journal.log(
        "collector",
        "steam_snapshot",
        appid=appid,
        data={
            "name": snap.name,
            "tags": snap.tags[:12],
            "peak_ccu": snap.peak_ccu,
            "peak_date": snap.peak_date,
            "days_since_peak": snap.days_since_peak,
            "is_free": snap.is_free,
            "current_players": snap.current_players,
        },
        sources=snap.sources,
    )
    return {"steam": snap}


def roblox_scout(state: PipelineState, deps: Deps) -> dict[str, Any]:
    appid = state["appid"]
    if "roblox" in state:
        rob = state["roblox"]
        deps.journal.log(
            "roblox_scout",
            "snapshot_provided",
            appid=appid,
            data={"best_clone_players": rob.best_clone_players, "leader_players": rob.leader_players},
            sources=rob.sources,
        )
        return {}
    skill = deps.skills.get("clone-search")
    steam = state["steam"]
    rob = RobloxTools(deps.http).snapshot(
        steam.name,
        steam.tags,
        steam.description,
        steam.release_ts,
        tag_keywords=skill.data["tag_keywords"],
        description_keywords=skill.data["description_keywords"],
    )
    deps.journal.log(
        "roblox_scout",
        "roblox_snapshot",
        appid=appid,
        data={
            "queries": rob.queries,
            "raw_results": rob.raw_results,
            "clones": len(rob.clones),
            "active_clones": rob.active_clones,
            "best_clone_players": rob.best_clone_players,
            "leader": rob.leader,
            "skill": skill.ref,
        },
        sources=rob.sources,
    )
    return {"roblox": rob, "skills_used": [skill.ref]}
