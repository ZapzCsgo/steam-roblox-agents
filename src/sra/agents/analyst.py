"""Analyst agent: scores the six qualitative factors with an LLM (structured output)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from sra.deps import Deps
from sra.grid import LLM_FACTORS, NAMES
from sra.llm import invoke_structured, model_name
from sra.skills import render
from sra.state import AnalystOutput, FactorScore, PipelineState
from sra.trace import sha256_text

SYSTEM = """ROLE: analyst
You are the Analyst agent of a pipeline that decides whether a trending Steam game is worth
adapting on Roblox. You score qualitative factors only; rule-based factors and the final
score are computed by code. Follow the skills below."""


def evidence_lines(state: PipelineState) -> list[str]:
    """The evidence block shown to the Analyst (and to the Critic's LLM review)."""
    steam, rob = state["steam"], state["roblox"]
    return [
        f"APPID: {steam.appid}",
        f"Game: {steam.name}",
        f"Steam tags (by votes): {', '.join(steam.tags[:15]) or 'none'}",
        f"Description: {steam.description or 'n/a'}",
        f"Price: {'free' if steam.is_free else 'paid'}; peak CCU: {steam.peak_ccu}; "
        f"days since peak: {steam.days_since_peak}",
        f"Roblox search: queries {rob.queries}; {len(rob.clones)} clone(s), {rob.active_clones} active, "
        f"best clone {rob.best_clone_players} players; established leader: "
        f"{rob.leader.name + ' (' + str(rob.leader.players) + ' players)' if rob.leader else 'none'}",
        "Rule-based factors (context, do not rescore):",
        *[f"  {k} {NAMES[k]} = {v.score} ({v.justification})" for k, v in sorted(state["rule_factors"].items())],
        "Hints from tags:",
        *[f"  {k}: {v}" for k, v in sorted(state.get("hints", {}).items())],
    ]


def build_prompt(state: PipelineState) -> str:
    lines = [
        *evidence_lines(state),
        f"Score these factors: {', '.join(f'{f} ({NAMES[f]})' for f in LLM_FACTORS)}.",
    ]
    feedback = state.get("critic_feedback") or []
    if feedback:
        lines += ["The Critic rejected your previous answer. Fix these issues:", *[f"  - {i}" for i in feedback]]
    return "\n".join(lines)


def analyst(state: PipelineState, deps: Deps) -> dict[str, Any]:
    attempt = state.get("analyst_attempts", 0) + 1
    skills = deps.skills.select("analyst", "score-factors")
    system = f"{SYSTEM}\n\n{render(skills)}"
    prompt = build_prompt(state)
    out, raw = invoke_structured(deps.llm, AnalystOutput, [SystemMessage(system), HumanMessage(prompt)])

    evidence = [s for s in state["steam"].sources if s.tool in {"steam.store_item", "case-file"}]
    evidence += state["roblox"].sources
    factors = {
        a.factor: FactorScore(
            factor=a.factor,
            score=a.score,
            justification=a.justification,
            confidence=a.confidence,
            origin="llm",
            sources=evidence,
        )
        for a in out.assessments
    }
    refs = [s.ref for s in skills]
    deps.journal.log(
        "analyst",
        "assessment",
        appid=state["appid"],
        data={
            "attempt": attempt,
            "model": model_name(deps.llm),
            "skills": refs,
            "prompt_sha": sha256_text(system + prompt),
            "raw_sha": sha256_text(raw),
            "factors": {
                k: {"score": v.score, "confidence": v.confidence, "justification": v.justification}
                for k, v in factors.items()
            },
        },
        sources=evidence,
    )
    return {"llm_factors": factors, "analyst_attempts": attempt, "skills_used": refs}
