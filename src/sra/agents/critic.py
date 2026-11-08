"""Critic agent: validates the Analyst's scores, sends them back or clamps them.

Two layers:
1. Deterministic consistency rules loaded from the `consistency-rules` skill
   (structure, justification length, tag-based bounds). Always on.
2. Optional LLM review (structured CriticReview), only when the rules pass.
The Analyst gets at most `max_revisions` second chances; after that, remaining
rule violations are clamped to their bound and the candidate is flagged for review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from sra.agents.analyst import evidence_lines
from sra.deps import Deps
from sra.grid import LLM_FACTORS
from sra.llm import invoke_structured
from sra.skills import render
from sra.state import CriticReview, CriticVerdict, FactorScore, PipelineState


@dataclass(frozen=True)
class Violation:
    rule_id: str
    factor: str
    message: str
    bound: tuple[str, int] | None = None  # ("max", 2) or ("min", 3)


def _condition_holds(rule: Mapping[str, Any], tags: Sequence[str], rule_factors: Mapping[str, FactorScore]) -> bool:
    top = set(tags[:15])
    if "when_tags_any" in rule and not top & set(rule["when_tags_any"]):
        return False
    for factor, ceiling in (rule.get("when_rule_factor_max") or {}).items():
        if factor not in rule_factors or rule_factors[factor].score > ceiling:
            return False
    return True


def check(
    llm_factors: Mapping[str, FactorScore],
    rule_factors: Mapping[str, FactorScore],
    tags: Sequence[str],
    rules: Mapping[str, Any],
) -> list[Violation]:
    out: list[Violation] = []
    for f in LLM_FACTORS:
        if f not in llm_factors:
            out.append(Violation("S1", f, f"{f} is missing from the answer"))
    min_chars = int(rules.get("min_justification_chars", 0))
    for f, fs in sorted(llm_factors.items()):
        if len(fs.justification.strip()) < min_chars:
            out.append(Violation("S2", f, f"{f} justification is too short (min {min_chars} characters)"))
    top = set(tags[:15])
    for rule in rules.get("bounds", []):
        f = rule["factor"]
        if f not in llm_factors or not _condition_holds(rule, tags, rule_factors):
            continue
        score = llm_factors[f].score
        matched = sorted(top & set(rule.get("when_tags_any", [])))
        ctx = f" (tags: {', '.join(matched)})" if matched else ""
        if "max" in rule and score > rule["max"]:
            out.append(
                Violation(
                    rule["id"], f, f"{f}={score} exceeds {rule['max']}: {rule['why']}{ctx}", ("max", int(rule["max"]))
                )
            )
        if "min" in rule and score < rule["min"]:
            out.append(
                Violation(
                    rule["id"], f, f"{f}={score} is below {rule['min']}: {rule['why']}{ctx}", ("min", int(rule["min"]))
                )
            )
    return out


def clamp(llm_factors: Mapping[str, FactorScore], violations: Sequence[Violation]) -> dict[str, FactorScore]:
    fixed = dict(llm_factors)
    for v in violations:
        if v.factor not in fixed:
            fixed[v.factor] = FactorScore(
                factor=v.factor,
                score=0,
                confidence="low",
                origin="critic-clamp",
                justification="missing after revisions; set to 0 (conservative)",
            )
            continue
        if v.bound is None:
            continue
        kind, value = v.bound
        cur = fixed[v.factor]
        new = min(cur.score, value) if kind == "max" else max(cur.score, value)
        if new != cur.score:
            fixed[v.factor] = cur.model_copy(
                update={
                    "score": new,
                    "origin": "critic-clamp",
                    "confidence": "low",
                    "justification": f"{cur.justification} [clamped {cur.score}->{new} by rule {v.rule_id}]",
                }
            )
    return fixed


def _llm_review(state: PipelineState, deps: Deps) -> CriticReview:
    skills = deps.skills.select("critic", "review")
    factors = "\n".join(
        f"  {k}={v.score} ({v.confidence}): {v.justification}" for k, v in sorted(state["llm_factors"].items())
    )
    system = (
        "ROLE: critic\nYou are the Critic agent. Check that each justification supports its score "
        "under the rubric, using the same evidence the Analyst saw. Be strict but concrete. Reject a "
        "score only if it contradicts the evidence or the rubric; a detail the data does not state "
        "(for example session length) may be inferred from the genre and is not by itself a reason to "
        f"reject.\n\n{render(skills)}"
    )
    human = "\n".join([*evidence_lines(state), "Analyst scores:", factors])
    review, _ = invoke_structured(deps.llm, CriticReview, [SystemMessage(system), HumanMessage(human)])
    return review


def critic(state: PipelineState, deps: Deps) -> dict[str, Any]:
    attempt = state["analyst_attempts"]
    skill = deps.skills.get("consistency-rules")
    violations = check(state["llm_factors"], state["rule_factors"], state["steam"].tags, skill.data)
    issues = [f"[{v.rule_id}] {v.message}" for v in violations]
    rule_ids = sorted({v.rule_id for v in violations})
    refs = [skill.ref]

    if not violations and deps.settings.critic_llm:
        review = _llm_review(state, deps)
        refs = [s.ref for s in deps.skills.select("critic", "review")]
        if not review.approved:
            issues += [f"[LLM] {i}" for i in review.issues] or ["[LLM] rejected without details"]
            rule_ids.append("LLM")

    approved = not issues
    verdict = CriticVerdict(attempt=attempt, approved=approved, issues=issues, rule_ids=rule_ids)
    low = sorted(f for f, fs in state["llm_factors"].items() if fs.confidence == "low")
    update: dict[str, Any] = {"critic_history": [verdict], "skills_used": refs}

    if approved:
        reasons = []
        if len(low) >= int(skill.data.get("low_confidence_review_threshold", 99)):
            reasons.append(f"low confidence on {', '.join(low)}")
        update |= {
            "validated": True,
            "critic_feedback": [],
            "needs_human_review": bool(reasons),
            "review_reasons": reasons,
        }
        event = "approved"
    elif attempt <= deps.settings.max_revisions:
        update |= {"validated": False, "critic_feedback": issues}
        event = "revision_requested"
    else:
        update |= {
            "validated": False,
            "critic_feedback": [],
            "llm_factors": clamp(state["llm_factors"], violations),
            "needs_human_review": True,
            "review_reasons": [f"critic rejected {attempt} answers; clamped: {', '.join(rule_ids)}"],
        }
        event = "clamped_after_max_revisions"

    deps.journal.log(
        "critic",
        event,
        appid=state["appid"],
        data={"attempt": attempt, "issues": issues, "rule_ids": rule_ids, "skills": refs},
    )
    return update


def route_after_critic(state: PipelineState, max_revisions: int) -> str:
    last = state["critic_history"][-1]
    if last.approved or state["analyst_attempts"] > max_revisions:
        return "scorer"
    return "analyst"
