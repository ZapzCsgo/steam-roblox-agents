from __future__ import annotations

import pytest

from sra import grid

F = grid.FACTORS

# Backtest of the original study (grid v1.1): notes F1..F10, durable genre, saturated, expected score.
BACKTEST = {
    "Meccha Chameleon": ([5, 5, 5, 5, 5, 5, 5, 4, 4, 4], False, False, 95),
    "Chained Together": ([5, 5, 5, 5, 5, 3, 4, 3, 5, 4], False, False, 92),
    "R.E.P.O.": ([5, 5, 4, 5, 4, 4, 4, 5, 4, 4], True, False, 96),
    "Among Us": ([5, 4, 5, 5, 5, 5, 5, 3, 2, 4], False, True, 77),
    "Lethal Company": ([5, 5, 4, 5, 4, 4, 4, 4, 4, 4], True, False, 94),
    "Phasmophobia": ([5, 5, 3, 5, 4, 3, 4, 3, 4, 4], True, False, 83),
    "PEAK": ([5, 4, 3, 5, 5, 3, 4, 4, 3, 3], False, False, 80),
    "Content Warning": ([5, 5, 3, 5, 4, 4, 3, 3, 3, 4], True, False, 81),
    "Supermarket Simulator": ([3, 5, 4, 3, 5, 3, 3, 5, 3, 5], True, False, 84),
    "Liar's Bar": ([4, 3, 5, 4, 3, 5, 3, 2, 4, 4], False, False, 75),
    "Fall Guys": ([3, 3, 2, 5, 5, 5, 4, 3, 1, 2], False, True, 56),
    "Only Up!": ([1, 5, 5, 4, 5, 2, 2, 3, 1, 5], False, True, 57),
    "Buckshot Roulette": ([3, 3, 5, 4, 2, 5, 2, 2, 3, 4], False, False, 67),
    "Mage Arena": ([4, 3, 3, 4, 2, 4, 1, 3, 4, 4], False, False, 66),
    "Helldivers 2": ([5, 2, 1, 4, 3, 2, 5, 3, 3, 3], False, False, 61),
    "Escape from Tarkov": ([3, 2, 1, 2, 2, 1, 3, 3, 3, 4], False, False, 46),
    "Vampire Survivors": ([0, 2, 3, 3, 4, 4, 3, 3, 2, 4], False, False, 40),
    "Balatro": ([0, 1, 3, 2, 2, 3, 3, 2, 3, 3], False, False, 39),
    "Palworld": ([3, 3, 0, 5, 3, 1, 5, 4, 2, 1], True, False, 35),
    "Schedule I": ([3, 3, 3, 4, 0, 2, 5, 4, 3, 4], True, False, 30),
    "Dispatch": ([0, 0, 1, 3, 2, 1, 4, 1, 3, 2], False, False, 29),
}


def scores(values: list[int]) -> dict[str, int]:
    return dict(zip(F, values, strict=True))


def test_weights_sum_to_100() -> None:
    assert sum(grid.WEIGHTS.values()) == 100
    assert set(grid.RULE_FACTORS) | set(grid.LLM_FACTORS) == set(F)
    assert not set(grid.RULE_FACTORS) & set(grid.LLM_FACTORS)


@pytest.mark.parametrize("name", sorted(BACKTEST))
def test_port_reproduces_original_backtest(name: str) -> None:
    notes, durable, saturated, expected = BACKTEST[name]
    assert grid.compute(scores(notes), 0, durable, saturated).score == expected


def test_perfect_and_zero() -> None:
    assert grid.compute(scores([5] * 10)).score == 100
    res = grid.compute(scores([0] * 10))
    assert res.score == 0 and res.verdict == "pass"


def test_missing_factor_raises() -> None:
    with pytest.raises(ValueError, match="missing"):
        grid.compute({"F1": 5})


def test_out_of_range_is_clamped() -> None:
    assert grid.compute(scores([9] * 10)).score == 100


@pytest.mark.parametrize(("idx", "cap"), [(4, 30), (0, 40), (2, 35)])
def test_caps(idx: int, cap: int) -> None:
    values = [5] * 10
    values[idx] = 0
    res = grid.compute(scores(values))
    assert res.score == cap and res.cap is not None


def test_lowest_cap_wins() -> None:
    values = [0, 5, 0, 5, 0, 5, 5, 5, 5, 5]
    assert grid.compute(scores(values)).score == 30


def test_durable_bonus_needs_f2_and_f8() -> None:
    base = [3, 4, 3, 3, 3, 3, 3, 4, 3, 3]
    with_bonus = grid.compute(scores(base), durable_genre=True)
    assert with_bonus.durable_bonus == grid.DURABLE_BONUS
    base[7] = 3
    assert grid.compute(scores(base), durable_genre=True).durable_bonus == 0


def test_saturation_malus() -> None:
    res = grid.compute(scores([4] * 10), saturated=True)
    assert res.saturation_malus == 10 and res.score == 70


def test_timing_decay() -> None:
    assert grid.timing_decay(5, 300) == 1.0
    assert grid.timing_decay(2, 30) == pytest.approx(0.85)
    assert grid.timing_decay(3, 60) == pytest.approx(0.925**2)
    assert grid.timing_decay(1, None) == 1.0
    late = grid.compute(scores([4, 2, 4, 4, 4, 4, 4, 4, 4, 4]), days_since_peak=90)
    assert late.decay < 1 and late.score < grid.compute(scores([4, 2, 4, 4, 4, 4, 4, 4, 4, 4])).score


@pytest.mark.parametrize(("score", "verdict"), [(80, "prototype"), (79, "watch"), (65, "watch"), (64, "pass")])
def test_verdict_thresholds(score: int, verdict: str) -> None:
    assert grid.verdict_for(score) == verdict


def test_result_serialises() -> None:
    d = grid.compute(scores([3] * 10)).to_dict()
    assert d["grid_version"] == grid.GRID_VERSION and d["verdict"] == "pass"
