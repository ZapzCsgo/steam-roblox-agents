"""Steam tools used by the Collector agent (public endpoints, no API key)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sra.sources.http import HttpClient, Response
from sra.state import SourceRef, SteamSnapshot

API = "https://api.steampowered.com"
STORE = "https://store.steampowered.com"


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass
class SteamTools:
    http: HttpClient

    def tag_names(self) -> tuple[dict[int, str], SourceRef]:
        r = self.http.get_json(f"{API}/IStoreService/GetTagList/v1/", {"language": "english"}, tool="steam.tag_list")
        return {t["tagid"]: t["name"] for t in r.data["response"]["tags"]}, r.source

    def store_item(self, appid: int) -> Response:
        payload = {
            "ids": [{"appid": appid}],
            "context": {"language": "english", "country_code": "US"},
            "data_request": {
                "include_basic_info": True,
                "include_release": True,
                "include_reviews": True,
                "include_tag_count": 20,
                "include_ratings": True,
            },
        }
        return self.http.get_json(
            f"{API}/IStoreBrowseService/GetItems/v1/",
            {"input_json": json.dumps(payload, sort_keys=True)},
            tool="steam.store_item",
        )

    def current_players(self, appid: int) -> Response:
        return self.http.get_json(
            f"{API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", {"appid": appid}, tool="steam.current_players"
        )

    def ccu_history(self, appid: int) -> Response:
        return self.http.get_json(
            f"https://steamcharts.com/app/{appid}/chart-data.json", None, tool="steamcharts.ccu_history"
        )

    def review_histogram(self, appid: int) -> Response:
        return self.http.get_json(
            f"{STORE}/appreviewhistogram/{appid}",
            {"l": "english", "review_score_preference": 0},
            tool="steam.review_histogram",
        )

    def snapshot(self, appid: int) -> SteamSnapshot:
        """Run every Steam tool for one app and assemble a typed snapshot with its sources."""
        names, tag_src = self.tag_names()
        item_r = self.store_item(appid)
        items = item_r.data.get("response", {}).get("store_items", [])
        item = next((i for i in items if i.get("success") == 1), None)
        if item is None:
            raise LookupError(f"Steam app {appid} not found")
        snap = snapshot_from_item(item, names)
        sources = [tag_src, item_r.source]

        players_r = self.current_players(appid)
        snap.current_players = players_r.data.get("response", {}).get("player_count")
        sources.append(players_r.source)

        hist_r = self.ccu_history(appid)
        as_of = _parse_ts(hist_r.source.fetched_at)
        peak = peak_from_history(hist_r.data, as_of)
        if peak["peak_ccu"] is None:  # too new for steamcharts: the current count is the peak so far
            peak |= {"peak_ccu": snap.current_players, "peak_date": as_of.strftime("%Y-%m-%d"), "days_since_peak": 0}
        snap.peak_ccu = peak["peak_ccu"]
        snap.peak_date = peak["peak_date"]
        snap.days_since_peak = peak["days_since_peak"]
        snap.pct_change_7d = peak["pct_change_7d"]
        sources.append(hist_r.source)

        rev_r = self.review_histogram(appid)
        snap.reviews_7d, snap.review_acceleration = reviews_momentum(rev_r.data)
        sources.append(rev_r.source)
        snap.sources = sources
        return snap


def snapshot_from_item(item: dict[str, Any], tag_names: dict[int, str]) -> SteamSnapshot:
    release = item.get("release") or {}
    cats = item.get("categories") or {}
    best = item.get("best_purchase_option") or {}
    price = best.get("original_price_in_cents") or best.get("final_price_in_cents")
    return SteamSnapshot(
        appid=int(item["appid"]),
        name=item.get("name") or str(item["appid"]),
        tags=[tag_names.get(t, str(t)) for t in item.get("tagids", [])],
        categories=sorted(set(cats.get("supported_player_categoryids", []) + cats.get("feature_categoryids", []))),
        content_descriptors=list(item.get("content_descriptorids", [])),
        content_notes=(item.get("game_rating") or {}).get("content_survey_notes"),
        is_free=bool(item.get("is_free")) or (not best and not price),
        price_cents=int(price) if price else None,
        description=((item.get("basic_info") or {}).get("short_description") or "")[:400],
        release_ts=release.get("original_steam_release_date") or release.get("steam_release_date"),
    )


def peak_from_history(points: list[list[float]], as_of: datetime) -> dict[str, Any]:
    """steamcharts chart-data ([ms, ccu] pairs) -> peak, peak date, days since peak, 7-day change."""
    pts = [(int(t / 1000), int(v)) for t, v in points if v is not None]
    if not pts:
        return {"peak_ccu": None, "peak_date": None, "days_since_peak": None, "pct_change_7d": None}
    ts, ccu = max(pts, key=lambda p: p[1])
    peak_dt = datetime.fromtimestamp(ts, UTC)
    last_ts, last_ccu = pts[-1]
    week_ago = [v for t, v in pts if t <= last_ts - 7 * 86400]
    pct = round(100 * (last_ccu - week_ago[-1]) / week_ago[-1], 1) if week_ago and week_ago[-1] else None
    return {
        "peak_ccu": ccu,
        "peak_date": peak_dt.strftime("%Y-%m-%d"),
        "days_since_peak": max(0, (as_of - peak_dt).days),
        "pct_change_7d": pct,
    }


def reviews_momentum(data: dict[str, Any]) -> tuple[int | None, float | None]:
    """Review histogram -> (reviews in the last full week, ratio vs the week before)."""
    rollups = (data.get("results") or {}).get("rollups") or []
    if len(rollups) < 2:
        return None, None
    counts = [r.get("recommendations_up", 0) + r.get("recommendations_down", 0) for r in rollups]
    step = rollups[1]["date"] - rollups[0]["date"]
    if step >= 7 * 86400 and len(counts) > 2:
        counts = counts[:-1]  # last weekly bucket is partial
    k = max(1, round(7 * 86400 / step)) if step else 1
    recent = sum(counts[-k:])
    before = sum(counts[-2 * k : -k]) if len(counts) >= 2 * k else 0
    return recent, (round(recent / before, 2) if before else None)
