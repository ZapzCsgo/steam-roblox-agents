from __future__ import annotations

from sra.agents.critic import Violation, check, clamp
from sra.state import FactorScore


def fs(
    factor: str, score: int, justification: str = "justification long enough here", confidence: str = "medium"
) -> FactorScore:
    return FactorScore(factor=factor, score=score, justification=justification, confidence=confidence, origin="llm")


def llm(**overrides: int) -> dict[str, FactorScore]:
    base = {"F2": 3, "F3": 3, "F4": 3, "F6": 3, "F8": 3, "F10": 3} | overrides
    return {k: fs(k, v) for k, v in base.items()}


RULES = {
    "min_justification_chars": 20,
    "bounds": [
        {"id": "C1", "factor": "F3", "max": 2, "when_tags_any": ["Open World"], "why": "complex"},
        {"id": "C4", "factor": "F4", "max": 3, "when_rule_factor_max": {"F1": 0}, "why": "solo"},
        {"id": "C5", "factor": "F2", "min": 3, "when_tags_any": ["Party Game"], "why": "native"},
    ],
}
RULE_F = {"F1": FactorScore(factor="F1", score=4, justification="co-op")}


def test_clean_answer_passes() -> None:
    assert check(llm(), RULE_F, ["Horror"], RULES) == []


def test_bounds_fire_only_when_condition_holds() -> None:
    v = check(llm(F3=4), RULE_F, ["Open World"], RULES)
    assert [x.rule_id for x in v] == ["C1"] and v[0].bound == ("max", 2) and "Open World" in v[0].message
    assert check(llm(F3=4), RULE_F, ["Casual"], RULES) == []


def test_rule_factor_condition() -> None:
    solo = {"F1": FactorScore(factor="F1", score=0, justification="solo")}
    assert [x.rule_id for x in check(llm(F4=5), solo, [], RULES)] == ["C4"]
    assert check(llm(F4=5), RULE_F, [], RULES) == []


def test_min_bound() -> None:
    v = check(llm(F2=1), RULE_F, ["Party Game"], RULES)
    assert v[0].rule_id == "C5" and v[0].bound == ("min", 3)


def test_structure_checks() -> None:
    answer = llm()
    del answer["F8"]
    answer["F2"] = fs("F2", 3, justification="short")
    ids = sorted(x.rule_id for x in check(answer, RULE_F, [], RULES))
    assert ids == ["S1", "S2"]


def test_clamp() -> None:
    answer = llm(F3=5, F2=1)
    del answer["F10"]
    fixed = clamp(
        answer,
        [
            Violation("C1", "F3", "m", ("max", 2)),
            Violation("C5", "F2", "m", ("min", 3)),
            Violation("S1", "F10", "missing"),
            Violation("S2", "F4", "short"),
        ],
    )
    assert fixed["F3"].score == 2 and fixed["F3"].origin == "critic-clamp" and "C1" in fixed["F3"].justification
    assert fixed["F2"].score == 3
    assert fixed["F10"].score == 0 and fixed["F10"].confidence == "low"
    assert fixed["F4"].origin == "llm"
