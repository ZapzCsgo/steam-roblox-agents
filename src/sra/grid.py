"""Deterministic Steam -> Roblox scoring grid, version 1.1.

The grid turns ten factor scores (0..5) into a 0..100 score and a verdict.
It is pure Python on purpose: the LLM only proposes factor scores, the
arithmetic, caps and timing decay are never delegated to a model.

Calibration history (see docs/grid.md):
  v1.0 -> v1.1  F9 (window / competition) 7 -> 10 points, F7 (Steam momentum) 8 -> 5,
               -10 malus when a Roblox leader already has >= 20 000 players,
               +6 bonus for durable Roblox genres (tycoon, co-op horror, co-op survival).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal

GRID_VERSION = "1.1"

FACTORS: tuple[str, ...] = ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10")
WEIGHTS: dict[str, int] = {
    "F1": 15,
    "F2": 15,
    "F3": 12,
    "F4": 12,
    "F5": 10,
    "F6": 8,
    "F7": 5,
    "F8": 8,
    "F9": 10,
    "F10": 5,
}
NAMES: dict[str, str] = {
    "F1": "co-op / social play",
    "F2": "native Roblox genre",
    "F3": "ease of cloning",
    "F4": "video virality",
    "F5": "age 9-16 / Roblox rules",
    "F6": "short sessions",
    "F7": "Steam momentum + price",
    "F8": "natural monetisation",
    "F9": "window / competition",
    "F10": "IP risk (5 = low)",
}
# Factors computed by rules from API data vs. factors judged by the Analyst agent.
RULE_FACTORS: tuple[str, ...] = ("F1", "F5", "F7", "F9")
LLM_FACTORS: tuple[str, ...] = ("F2", "F3", "F4", "F6", "F8", "F10")

DURABLE_BONUS = 6
SATURATION_MALUS = 10
SATURATION_PLAYERS = 20_000
THRESHOLDS = {"prototype": 80, "watch": 65}

Verdict = Literal["prototype", "watch", "pass"]
VERDICTS: tuple[Verdict, ...] = ("prototype", "watch", "pass")


@dataclass(frozen=True)
class GridResult:
    score: int
    raw: float
    durable_bonus: int
    saturation_malus: int
    cap: str | None
    decay: float
    verdict: Verdict
    grid_version: str = GRID_VERSION

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verdict_for(score: float) -> Verdict:
    if score >= THRESHOLDS["prototype"]:
        return "prototype"
    if score >= THRESHOLDS["watch"]:
        return "watch"
    return "pass"


def timing_decay(f2: float, days_since_peak: int | None) -> float:
    """Hype without a host genre fades: -15 % per 30 days if F2 <= 2, -7.5 % if F2 == 3."""
    days = max(0, days_since_peak or 0)
    if f2 <= 2:
        return 0.85 ** (days / 30)
    if f2 < 4:
        return 0.925 ** (days / 30)
    return 1.0


def compute(
    scores: Mapping[str, float],
    days_since_peak: int | None = 0,
    durable_genre: bool = False,
    saturated: bool = False,
) -> GridResult:
    """Combine ten factor scores into the final 0..100 score.

    Raises ValueError if a factor is missing. Out-of-range scores are clamped to 0..5.
    """
    missing = [f for f in FACTORS if f not in scores]
    if missing:
        raise ValueError(f"missing factor scores: {missing}")
    n = {f: max(0.0, min(5.0, float(scores[f]))) for f in FACTORS}
    raw = sum(WEIGHTS[f] * n[f] / 5 for f in FACTORS)
    bonus = DURABLE_BONUS if (durable_genre and n["F2"] >= 4 and n["F8"] >= 4) else 0
    malus = SATURATION_MALUS if saturated else 0
    total = raw + bonus - malus

    caps: list[tuple[str, int]] = []
    if n["F5"] == 0:
        caps.append(("F5=0 forbidden theme", 30))
    if n["F1"] == 0:
        caps.append(("F1=0 solo only", 40))
    if n["F3"] == 0:
        caps.append(("F3=0 not feasible on Roblox", 35))
    cap = min(caps, key=lambda c: c[1]) if caps else None
    if cap:
        total = min(total, cap[1])

    decay = timing_decay(n["F2"], days_since_peak)
    score = round(max(0.0, min(100.0, total * decay)))
    return GridResult(
        score=score,
        raw=round(raw, 1),
        durable_bonus=bonus,
        saturation_malus=malus,
        cap=f"{cap[0]} -> {cap[1]}" if cap else None,
        decay=round(decay, 3),
        verdict=verdict_for(score),
    )
