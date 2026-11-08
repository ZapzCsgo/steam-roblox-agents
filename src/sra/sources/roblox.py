"""Roblox tools used by the Roblox Scout agent: search for existing clones of a concept.

Endpoints: omni-search and games/v1/games through the public roproxy mirror
(the direct apis.roblox.com search is limited to about 1 request per minute).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sra.sources.http import FixtureMissingError, HttpClient, HttpError
from sra.state import RobloxGame, RobloxSnapshot, SourceRef

SEARCH_URL = "https://apis.roproxy.com/search-api/omni-search"
GAMES_URL = "https://games.roproxy.com/v1/games"
GAMES_URL_DIRECT = "https://games.roblox.com/v1/games"
SUFFIXES = r"(?:s|es|ing|ed|er|ers|y)?\b"
STOP_WORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "simulator",
    "game",
    "games",
    "online",
    "edition",
    "remastered",
    "definitive",
    "deluxe",
    "ultimate",
    "early",
    "access",
    "demo",
    "2",
    "3",
    "ii",
    "iii",
    "vr",
    "for",
    "to",
    "in",
    "on",
    "with",
    "my",
    "your",
}
ACTIVE_PLAYERS = 50
ESTABLISHED_PLAYERS = 1000


def normalize(text: str | None) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def split_title(name: str) -> tuple[str, str]:
    """'Retro Rewind - Video Store Simulator' -> ('Retro Rewind', 'Video Store Simulator')."""
    name = re.sub(r"[™®©]", "", name or "").strip()
    parts = re.split(r"\s+[-–|]\s+|:\s+", name, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def concept_keywords(
    tags: Sequence[str],
    description: str,
    tag_keywords: Mapping[str, str],
    description_keywords: Mapping[str, str],
    max_words: int = 3,
) -> list[str]:
    """Concept keywords: description stems first (more specific), then Steam tags."""
    out: list[str] = []
    desc = (description or "").lower()
    found = sorted(
        (m.start(), kw)
        for stem, kw in description_keywords.items()
        if (m := re.search(rf"\b{re.escape(stem)}{SUFFIXES}", desc))
    )
    for _, kw in found:
        if kw not in out:
            out.append(kw)
        if len(out) >= 2:
            break
    for tag in tags:
        tag_kw = tag_keywords.get(tag)
        if tag_kw and tag_kw not in out:
            out.append(tag_kw)
        if len(out) >= max_words:
            break
    return out


def build_queries(name: str, keywords: Sequence[str]) -> list[str]:
    title, subtitle = split_title(name)
    queries = [title]
    if subtitle:
        queries.append(subtitle)
    elif keywords and keywords[0] not in normalize(title):
        queries.append(f"{title} {keywords[0]}")
    others = [k for k in keywords[1:] if k not in keywords[0] and keywords[0] not in k]
    if others:
        queries.append(f"{keywords[0]} {others[0]}")
    seen: set[str] = set()
    unique = []
    for q in queries:
        if normalize(q) and normalize(q) not in seen:
            seen.add(normalize(q))
            unique.append(q)
    return unique


def _iso_ts(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify(
    name: str,
    keywords: Sequence[str],
    release_ts: int | None,
    found: Mapping[int, Mapping[str, Any]],
    details: Mapping[int, Mapping[str, Any]],
) -> tuple[list[RobloxGame], list[RobloxGame]]:
    """Split relevant search hits into clones (created after release - 30 days) and established games."""
    title, subtitle = split_title(name)
    nt = normalize(title)
    name_tokens = [w for w in nt.split() if w not in STOP_WORDS and len(w) >= 3]
    concept = set(keywords) | {w for w in normalize(subtitle).split() if w not in STOP_WORDS and len(w) >= 3}
    limit = (release_ts or 0) - 30 * 86400
    clones: list[RobloxGame] = []
    established: list[RobloxGame] = []
    for uid, hit in found.items():
        d = details.get(uid, {})
        n = normalize(d.get("name") or hit.get("name"))
        genre = normalize(f"{d.get('genre_l1') or ''} {d.get('genre_l2') or ''}")
        name_match = bool(nt) and (
            re.search(rf"\b{re.escape(nt)}\b", n) is not None
            or (len(name_tokens) >= 2 and all(w in n.split() for w in name_tokens))
        )
        hits = sum(1 for w in concept if re.search(rf"\b{re.escape(w)}{SUFFIXES}", f"{n} | {genre}"))
        if name_match and len(name_tokens) <= 1 and hits == 0 and not n.startswith(nt):
            name_match = False  # one common word ("PEAK") also needs a concept keyword
        if not (name_match or hits >= 2):
            continue
        place = d.get("rootPlaceId") or hit.get("rootPlaceId")
        game = RobloxGame(
            name=d.get("name") or hit.get("name") or "?",
            universe_id=int(uid),
            players=int(d.get("playing", hit.get("playerCount", 0)) or 0),
            visits=d.get("visits"),
            created=(d.get("created") or "")[:10] or None,
            url=f"https://www.roblox.com/games/{place}" if place else None,
        )
        created = _iso_ts(d.get("created"))
        if created is None or created >= limit:
            clones.append(game)
        elif game.players >= ESTABLISHED_PLAYERS:
            established.append(game)
    clones.sort(key=lambda g: -g.players)
    established.sort(key=lambda g: -g.players)
    return clones, established


@dataclass
class RobloxTools:
    http: HttpClient

    def search_games(self, query: str) -> tuple[list[dict[str, Any]], SourceRef]:
        r = self.http.get_json(
            SEARCH_URL, {"searchQuery": query, "pageType": "all", "sessionId": "sra"}, tool="roblox.search_games"
        )
        games: list[dict[str, Any]] = []
        for group in r.data.get("searchResults", []) or []:
            if group.get("contentGroupType") == "Game":
                games += [c for c in group.get("contents", []) if not c.get("isSponsored")]
        return games, r.source

    def game_details(self, universe_ids: Sequence[int]) -> tuple[dict[int, dict[str, Any]], list[SourceRef]]:
        out: dict[int, dict[str, Any]] = {}
        sources: list[SourceRef] = []
        ids = sorted(set(universe_ids))
        for i in range(0, len(ids), 50):
            params = {"universeIds": ",".join(map(str, ids[i : i + 50]))}
            try:
                r = self.http.get_json(GAMES_URL, params, tool="roblox.game_details")
            except (HttpError, FixtureMissingError):  # mirror rate-limited: use the official endpoint
                r = self.http.get_json(GAMES_URL_DIRECT, params, tool="roblox.game_details")
            sources.append(r.source)
            for g in r.data.get("data", []):
                out[int(g["id"])] = g
        return out, sources

    def snapshot(
        self,
        name: str,
        tags: Sequence[str],
        description: str,
        release_ts: int | None,
        tag_keywords: Mapping[str, str],
        description_keywords: Mapping[str, str],
    ) -> RobloxSnapshot:
        keywords = concept_keywords(tags, description, tag_keywords, description_keywords)
        queries = build_queries(name, keywords)
        found: dict[int, dict[str, Any]] = {}
        sources: list[SourceRef] = []
        for q in queries:
            try:
                games, src = self.search_games(q)
            except HttpError:
                continue
            sources.append(src)
            for g in games:
                found.setdefault(int(g["universeId"]), g)
        details, det_sources = self.game_details(list(found)) if found else ({}, [])
        sources += det_sources
        clones, established = classify(name, keywords, release_ts, found, details)
        return RobloxSnapshot(
            queries=queries,
            raw_results=len(found),
            clones=clones[:10],
            active_clones=sum(1 for c in clones if c.players >= ACTIVE_PLAYERS),
            best_clone_players=clones[0].players if clones else 0,
            leader=established[0] if established else None,
            sources=sources,
        )
