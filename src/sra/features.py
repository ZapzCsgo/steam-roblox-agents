"""Rule-based factors (F1, F5, F7, F9) and hints computed from Steam/Roblox data.

Ported from the production cron of the original project. No LLM here: these
functions are pure and unit-tested, and each returns (score, reason).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from sra.grid import SATURATION_PLAYERS

COOP_CATEGORIES = {9, 38, 39, 48}  # Co-op, Online Co-op, Shared/Split Screen Co-op, LAN Co-op
MULTI_CATEGORIES = {1, 20, 27, 36, 37, 47, 49}
COOP_TAGS = {"Co-op", "Online Co-Op", "Local Co-Op", "Co-op Campaign"}
PARTY_TAGS = {"Party Game", "Party", "Social Deduction", "Funny", "Comedy", "Local Multiplayer"}
MULTI_TAGS = {"Multiplayer", "PvP", "Online PvP", "Massively Multiplayer", "Team-Based", "Battle Royale"}
SEXUAL_TAGS = {"Sexual Content", "Nudity", "NSFW", "Hentai"}
# Drugs disqualify only when they are the theme, not when notes mention fictional medicine.
ILLEGAL_DRUGS = re.compile(
    r"illegal drugs?|narcotics?|drug (?:dealing|trafficking|production|distribution|sale)"
    r"|(?:dealing|trafficking|production|distribution|sale|selling) of (?:illegal )?drugs"
)
GAMBLING_TAGS = {"Gambling", "Casino", "Poker"}
HORROR_TAGS = {"Horror", "Psychological Horror", "Survival Horror"}
TYCOON_TAGS = {
    "Management",
    "Economy",
    "Idler",
    "Farming Sim",
    "Job Simulator",
    "Shop Keeper",
    "Life Sim",
    "City Builder",
    "Capitalism",
    "Clicker",
    "Automation",
}
NATIVE_TAGS = {
    "Horror",
    "Survival Horror",
    "Psychological Horror",
    "Parkour",
    "3D Platformer",
    "Platformer",
    "Survival",
    "Social Deduction",
    "Party Game",
    "Management",
    "Simulation",
    "Idler",
    "Clicker",
    "Tower Defense",
    "Hidden Object",
    "Physics",
    "Funny",
    "Comedy",
    "Cute",
    "Casual",
    "Fishing",
    "Farming Sim",
    "Job Simulator",
    "Racing",
    "Escape Room",
}
DISTANT_TAGS = {
    "Story Rich",
    "Visual Novel",
    "Grand Strategy",
    "Turn-Based Strategy",
    "4X",
    "RTS",
    "Narrative",
    "Interactive Fiction",
    "Choices Matter",
    "JRPG",
    "CRPG",
    "Soulslike",
    "Immersive Sim",
}
COMPLEX_TAGS = {
    "Open World",
    "MMORPG",
    "Massively Multiplayer",
    "Open World Survival Craft",
    "Vehicular Combat",
    "Automobile Sim",
    "Flight",
    "Simulation Racing",
    "RPG",
    "Colony Sim",
    "Grand Strategy",
    "Souls-like",
    "Extraction Shooter",
    "Immersive Sim",
    "Creature Collector",
}
SIMPLE_TAGS = {
    "Casual",
    "Party Game",
    "Funny",
    "Physics",
    "Arcade",
    "Minimalist",
    "Short",
    "Clicker",
    "Idler",
    "Hidden Object",
    "Escape Room",
    "Social Deduction",
}
LONG_TAGS = {"Open World", "Grand Strategy", "Story Rich", "RPG", "4X", "MMORPG", "Colony Sim", "Base Building"}
SHORT_TAGS = {
    "Party Game",
    "Arcade",
    "Roguelike",
    "Action Roguelike",
    "Short",
    "Social Deduction",
    "Battle Royale",
    "Card Game",
    "Score Attack",
}


def _top(tags: Sequence[str], n: int) -> set[str]:
    return set(tags[:n])


def is_coop(categories: Iterable[int], tags: Sequence[str]) -> bool:
    return bool(COOP_CATEGORIES & set(categories)) or bool(COOP_TAGS & _top(tags, 15))


def f1_social(categories: Iterable[int], tags: Sequence[str]) -> tuple[int, str]:
    cats = set(categories)
    t = _top(tags, 15)
    coop = is_coop(cats, tags)
    party = bool(PARTY_TAGS & t)
    multi = coop or bool(MULTI_CATEGORIES & cats) or bool(MULTI_TAGS & t)
    if coop and party:
        return 5, "co-op plus party/comedy tags"
    if coop:
        return 4, "co-op (Steam categories/tags)"
    if multi and party:
        return 3, "multiplayer plus party tags"
    if multi:
        return 2, "multiplayer without co-op"
    return 0, "single-player only according to Steam"


def f5_age_rules(tags: Sequence[str], descriptors: Iterable[int] | None, content_notes: str | None) -> tuple[int, str]:
    t = _top(tags, 20)
    notes = (content_notes or "").lower()
    d = set(descriptors or [])
    if d & {3, 4} or t & SEXUAL_TAGS or "sexual" in notes or "nudity" in notes:
        return 0, "sexual content (Steam descriptors/tags)"
    if "Drugs" in t or ILLEGAL_DRUGS.search(notes):
        return 0, "illegal drugs are central to the game (content notes/tags)"
    score, reasons = 5, []
    if t & GAMBLING_TAGS or "gambl" in notes:
        score = min(score, 1)
        reasons.append("gambling")
    if "Mature" in t:
        score = min(score, 2)
        reasons.append("Mature tag")
    if 2 in d or t & {"Gore", "Blood"} or "gore" in notes or "blood" in notes:
        score = min(score, 2)
        reasons.append("frequent gore/violence")
    if t & {"Military", "Realistic", "Tactical", "War"} and t & {"Shooter", "FPS"}:
        score = min(score, 2)
        reasons.append("realistic firearms")
    if 5 in d or "violence" in notes:
        score = min(score, 3)
        reasons.append("general mature content")
    if t & HORROR_TAGS:
        score = min(score, 4)
        reasons.append("horror")
    return score, ", ".join(reasons) or "nothing sensitive detected"


def f7_momentum(
    peak_ccu: int | None,
    pct_change_7d: float | None,
    review_acceleration: float | None,
    is_free: bool,
    reviews_7d: int | None,
) -> tuple[int, str]:
    peak = peak_ccu or 0
    if peak < 10_000:
        base = 1
    elif peak < 50_000:
        base = 2
    elif peak < 150_000:
        base = 3
    elif peak < 200_000:
        base = 4
    else:
        base = 5
    reasons = [f"peak {peak:,} CCU"]
    if (reviews_7d or 0) >= 3000 and base < 3:
        base = 3
        reasons.append(f"{reviews_7d} reviews in 7 days")
    if (pct_change_7d or 0) >= 100 or (review_acceleration or 0) >= 2:
        base += 1
        reasons.append("vertical growth")
    if not is_free:
        base += 1
        reasons.append("paid on Steam (free Roblox clone is an arbitrage)")
    return min(5, base), ", ".join(reasons)


def f9_window(
    days_since_peak: int | None, best_clone_players: int, active_clones: int, leader_players: int
) -> tuple[int, str]:
    days = days_since_peak if days_since_peak is not None else 999
    if days <= 14:
        score = 5
    elif days <= 30:
        score = 4
    elif days <= 60:
        score = 3
    elif days <= 90:
        score = 2
    else:
        score = 1
    reasons = [f"{days} days since Steam peak"]
    top = max(best_clone_players, leader_players)
    if top >= SATURATION_PLAYERS:
        score = 1
        reasons.append(f"Roblox leader already at {top:,} players")
    elif best_clone_players >= 5000 or active_clones >= 5:
        score -= 2
        reasons.append(f"{active_clones} active clones, best at {best_clone_players:,}")
    elif active_clones >= 1 or leader_players >= 5000:
        score -= 1
        reasons.append(f"{active_clones} active clone(s), established game at {leader_players:,}")
    else:
        reasons.append("no active clone")
    return max(1, score), ", ".join(reasons)


def durable_genre(tags: Sequence[str], categories: Iterable[int]) -> tuple[bool, str | None]:
    t = _top(tags, 15)
    coop = is_coop(categories, tags)
    if t & TYCOON_TAGS:
        return True, "tycoon/management"
    if coop and t & HORROR_TAGS:
        return True, "co-op horror"
    if coop and "Survival" in t:
        return True, "co-op survival"
    return False, None


def is_saturated(best_clone_players: int, leader_players: int) -> bool:
    return max(best_clone_players, leader_players) >= SATURATION_PLAYERS


def llm_hints(tags: Sequence[str]) -> dict[str, str]:
    """Tag-based hints handed to the Analyst (and reused by the Critic bounds)."""
    t = _top(tags, 15)

    def fmt(s: set[str]) -> str:
        return ", ".join(sorted(s)) or "none"

    return {
        "F2": f"native Roblox tags: {fmt(t & NATIVE_TAGS)}; distant tags: {fmt(t & DISTANT_TAGS)}",
        "F3": f"complex tags: {fmt(t & COMPLEX_TAGS)}; simple tags: {fmt(t & SIMPLE_TAGS)}",
        "F6": f"long-session tags: {fmt(t & LONG_TAGS)}; short-session tags: {fmt(t & SHORT_TAGS)}",
    }
