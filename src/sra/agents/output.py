"""Scorer (deterministic grid) and Reporter (Markdown decision record) nodes."""

from __future__ import annotations

from typing import Any

from sra import grid
from sra.deps import Deps
from sra.state import FactorScore, PipelineState, ScoreBreakdown, SourceRef


def all_factors(state: PipelineState) -> dict[str, FactorScore]:
    return {**state["rule_factors"], **state["llm_factors"]}


def scorer(state: PipelineState, deps: Deps) -> dict[str, Any]:
    factors = all_factors(state)
    res = grid.compute(
        {k: v.score for k, v in factors.items()},
        state["steam"].days_since_peak,
        durable_genre=state.get("durable_genre", False),
        saturated=state.get("saturated", False),
    )
    breakdown = ScoreBreakdown.model_validate({**res.to_dict(), "factors": {f: factors[f].score for f in grid.FACTORS}})
    deps.journal.log("scorer", "scored", appid=state["appid"], data=breakdown.model_dump())
    return {"score": breakdown}


def _sources(state: PipelineState) -> list[SourceRef]:
    seen: dict[tuple[str, str], SourceRef] = {}
    for s in [*state["steam"].sources, *state["roblox"].sources]:
        seen.setdefault((s.tool, s.url), s)
    return list(seen.values())


def render_report(state: PipelineState, next_steps: dict[str, str]) -> str:
    steam, rob, sc = state["steam"], state["roblox"], state["score"]
    factors = all_factors(state)
    ranked = sorted(factors.values(), key=lambda f: (-f.score * grid.WEIGHTS[f.factor], f.factor))
    strengths = ", ".join(f"{f.factor} {grid.NAMES[f.factor]} ({f.score})" for f in ranked[:3])
    blockers = [f"{f.factor} {grid.NAMES[f.factor]} ({f.score})" for f in factors.values() if f.score <= 1]
    review = "no"
    if state.get("needs_human_review"):
        review = f"yes ({'; '.join(state.get('review_reasons', []))})"
    lines = [
        f"# {steam.name} (Steam {steam.appid})",
        "",
        f"- Verdict: **{sc.verdict}** (score {sc.score}/100, grid v{sc.grid_version})",
        f"- Run: `{state.get('run_id', '?')}`",
        f"- Critic: {'validated' if state.get('validated') else 'not validated'}; human review: {review}",
        "",
        "## Decision summary",
        "",
        f"Main strengths: {strengths}. Blockers: {', '.join(blockers) or 'none'}.",
        f"Next step: {next_steps.get(sc.verdict, '')}",
        "",
        "## Factors",
        "",
        "| Factor | Score | Weight | Origin | Confidence | Justification |",
        "|---|---|---|---|---|---|",
        *[
            f"| {f} {grid.NAMES[f]} | {factors[f].score} | {grid.WEIGHTS[f]} | {factors[f].origin} | "
            f"{factors[f].confidence} | {factors[f].justification.replace('|', '/')} |"
            for f in grid.FACTORS
        ],
        "",
        "## Score arithmetic",
        "",
        f"raw {sc.raw} + durable bonus {sc.durable_bonus} - saturation malus {sc.saturation_malus}"
        f"{', cap ' + sc.cap if sc.cap else ''}, timing decay x{sc.decay} -> **{sc.score}**",
        "",
        "## Critic log",
        "",
        *[
            f"- attempt {v.attempt}: {'approved' if v.approved else 'rejected'}"
            f"{' - ' + '; '.join(v.issues) if v.issues else ''}"
            for v in state.get("critic_history", [])
        ],
        "",
        "## Roblox evidence",
        "",
        f"Queries: {', '.join(rob.queries) or 'n/a'}. Clones: {len(rob.clones)} ({rob.active_clones} active), "
        f"best clone {rob.best_clone_players} players. Established leader: "
        f"{(rob.leader.name + ' ' + str(rob.leader.players) + ' players') if rob.leader else 'none'}.",
        *[f"- {c.name}: {c.players} players, created {c.created or '?'} ({c.url or 'no url'})" for c in rob.clones[:5]],
        "",
        "## Sources",
        "",
        *[f"- `{s.tool}` {s.url} (fetched {s.fetched_at}, {s.origin})" for s in _sources(state)],
        "",
        "## Skills used",
        "",
        *[f"- {ref}" for ref in sorted(set(state.get("skills_used", [])))],
        "",
    ]
    return "\n".join(lines)


def reporter(state: PipelineState, deps: Deps) -> dict[str, Any]:
    skill = deps.skills.get("report-template")
    state_with_skill: PipelineState = {**state, "skills_used": [*state.get("skills_used", []), skill.ref]}
    text = render_report(state_with_skill, skill.data.get("next_steps", {}))
    update: dict[str, Any] = {"report_markdown": text, "skills_used": [skill.ref]}
    if deps.reports_dir is not None:
        path = deps.reports_dir / f"{state['appid']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        update["report_path"] = str(path)
    deps.journal.log(
        "reporter",
        "report_written",
        appid=state["appid"],
        data={"verdict": state["score"].verdict, "score": state["score"].score, "path": update.get("report_path")},
    )
    return update
