from __future__ import annotations

import pytest

from sra import features as fx


@pytest.mark.parametrize(
    ("cats", "tags", "expected"),
    [
        ([9], ["Funny"], 5),
        ([38], ["Action"], 4),
        ([], ["Online Co-Op"], 4),
        ([1], ["Party Game"], 3),
        ([1], ["Shooter"], 2),
        ([2], ["Singleplayer"], 0),
    ],
)
def test_f1_social(cats: list[int], tags: list[str], expected: int) -> None:
    assert fx.f1_social(cats, tags)[0] == expected


def test_f1_ignores_tags_beyond_top_15() -> None:
    tags = [f"t{i}" for i in range(15)] + ["Co-op"]
    assert fx.f1_social([], tags)[0] == 0


@pytest.mark.parametrize(
    ("tags", "descriptors", "notes", "expected"),
    [
        ([], [], None, 5),
        (["Horror"], [], None, 4),
        ([], [5], "", 3),
        (["Gore"], [], None, 2),
        (["Military", "FPS"], [], None, 2),
        (["Mature"], [], None, 2),
        (["Casino"], [], None, 1),
        (["Nudity"], [], None, 0),
        ([], [3], None, 0),
        (["Drugs"], [], None, 0),
    ],
)
def test_f5_age_rules(tags: list[str], descriptors: list[int], notes: str | None, expected: int) -> None:
    assert fx.f5_age_rules(tags, descriptors, notes)[0] == expected


@pytest.mark.parametrize(
    ("peak", "pct", "accel", "free", "reviews", "expected"),
    [
        (5_000, None, None, True, None, 1),
        (5_000, None, None, False, None, 2),
        (40_000, None, None, True, 4000, 3),
        (100_000, 150.0, None, True, None, 4),
        (180_000, None, 2.5, False, None, 5),
        (900_000, None, None, False, None, 5),
        (None, None, None, True, None, 1),
    ],
)
def test_f7_momentum(peak, pct, accel, free, reviews, expected) -> None:
    assert fx.f7_momentum(peak, pct, accel, free, reviews)[0] == expected


@pytest.mark.parametrize(
    ("days", "best", "active", "leader", "expected"),
    [
        (3, 0, 0, 0, 5),
        (20, 0, 0, 0, 4),
        (45, 0, 0, 0, 3),
        (80, 0, 0, 0, 2),
        (200, 0, 0, 0, 1),
        (None, 0, 0, 0, 1),
        (3, 0, 1, 0, 4),
        (3, 6000, 2, 0, 3),
        (3, 0, 0, 25_000, 1),
        (3, 21_000, 9, 0, 1),
    ],
)
def test_f9_window(days, best, active, leader, expected) -> None:
    assert fx.f9_window(days, best, active, leader)[0] == expected


@pytest.mark.parametrize(
    ("tags", "cats", "expected"),
    [
        (["Management"], [], "tycoon/management"),
        (["Horror"], [9], "co-op horror"),
        (["Survival"], [38], "co-op survival"),
        (["Horror"], [], None),
    ],
)
def test_durable_genre(tags: list[str], cats: list[int], expected: str | None) -> None:
    assert fx.durable_genre(tags, cats)[1] == expected


def test_saturation_and_hints() -> None:
    assert fx.is_saturated(0, 20_000) and not fx.is_saturated(19_999, 0)
    hints = fx.llm_hints(["Horror", "Open World", "Party Game"])
    assert set(hints) == {"F2", "F3", "F6"}
    assert "Open World" in hints["F3"] and "Party Game" in hints["F6"]
