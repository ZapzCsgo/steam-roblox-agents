from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sra.sources import roblox as rb
from sra.sources.http import FixtureMissingError, HttpClient, display_url, fixture_key
from sra.sources.steam import SteamTools, peak_from_history, reviews_momentum


def test_fixture_key_ignores_volatile_params() -> None:
    url = "https://apis.roproxy.com/search-api/omni-search"
    assert fixture_key(url, {"searchQuery": "x", "sessionId": "a"}) == fixture_key(url, {"searchQuery": "x"})
    assert fixture_key(url, {"searchQuery": "x"}) != fixture_key(url, {"searchQuery": "y"})
    assert display_url(url, {"sessionId": "a"}) == url


def test_replay_missing_fixture_raises(tmp_path: Path) -> None:
    client = HttpClient(mode="replay", fixtures_dir=tmp_path)
    with pytest.raises(FixtureMissingError):
        client.get_json("https://example.org/x", tool="t")


class _FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {"ok": True}


class _FakeSession:
    def __init__(self) -> None:
        self.headers: dict = {}
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResponse()


def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    session = _FakeSession()
    rec = HttpClient(mode="record", fixtures_dir=tmp_path, session=session)  # type: ignore[arg-type]
    live = rec.get_json("https://api.steampowered.com/x", {"a": 1}, tool="steam.x")
    assert live.data == {"ok": True} and live.source.origin == "live" and session.calls == 1
    replay = HttpClient(mode="replay", fixtures_dir=tmp_path).get_json(
        "https://api.steampowered.com/x", {"a": 1}, tool="steam.x"
    )
    assert replay.data == {"ok": True}
    assert replay.source.origin == "fixture" and replay.source.fetched_at == live.source.fetched_at
    stored = json.loads(next(tmp_path.rglob("*.json")).read_text())
    assert stored["request"]["params"] == {"a": 1}


def test_steam_snapshot_from_fixtures(http: HttpClient) -> None:
    snap = SteamTools(http).snapshot(5335510)
    assert snap.name == "Moon Sweepers"
    assert "Co-op" in snap.tags and 9 in snap.categories
    assert snap.peak_ccu and snap.peak_ccu > 10_000 and snap.days_since_peak == 0
    assert not snap.is_free and snap.price_cents == 899
    assert {s.tool for s in snap.sources} >= {"steam.store_item", "steamcharts.ccu_history"}
    assert all(s.origin == "fixture" for s in snap.sources)


def test_steam_unknown_app_raises(http: HttpClient, tmp_path: Path) -> None:
    with pytest.raises(FixtureMissingError):
        SteamTools(HttpClient(mode="replay", fixtures_dir=tmp_path)).snapshot(1)


def test_peak_from_history() -> None:
    day = 86400 * 1000
    pts = [[0, 100], [10 * day, 900], [20 * day, 200], [27 * day, 300]]
    res = peak_from_history(pts, datetime.fromtimestamp(30 * 86400, UTC))
    assert res["peak_ccu"] == 900 and res["days_since_peak"] == 20 and res["pct_change_7d"] == 50.0
    assert peak_from_history([], datetime.now(UTC))["peak_ccu"] is None


def test_reviews_momentum() -> None:
    day = 86400
    rollups = [
        {"date": i * day, "recommendations_up": n, "recommendations_down": 0} for i, n in enumerate([1] * 7 + [2] * 7)
    ]
    recent, accel = reviews_momentum({"results": {"rollups": rollups}})
    assert recent == 14 and accel == 2.0
    assert reviews_momentum({}) == (None, None)


def test_title_and_queries() -> None:
    assert rb.split_title("Retro Rewind - Video Store Simulator") == ("Retro Rewind", "Video Store Simulator")
    assert rb.normalize("Él Café!") == "el cafe"
    kws = rb.concept_keywords(
        ["Horror", "Co-op"],
        "Collect scrap and meet a monster.",
        {"Horror": "horror"},
        {"monster": "monster", "scrap": "scrap"},
    )
    assert kws == ["scrap", "monster", "horror"]
    assert rb.build_queries("Lethal Company", kws) == ["Lethal Company", "Lethal Company scrap", "scrap monster"]
    assert rb.build_queries("A: B", []) == ["A", "B"]


def test_classify_clones_and_established() -> None:
    found = {
        1: {"name": "PEAK climb"},
        2: {"name": "Climb Together Obby"},
        3: {"name": "Pizza Peak"},
        4: {"name": "Unrelated"},
    }
    details = {
        1: {"name": "PEAK climb", "playing": 900, "created": "2025-07-01T00:00:00Z", "rootPlaceId": 11},
        2: {"name": "Climb Together Obby", "playing": 3000, "created": "2020-01-01T00:00:00Z", "genre_l1": "obby"},
        3: {"name": "Pizza Peak", "playing": 50, "created": "2025-07-01T00:00:00Z"},
        4: {"name": "Unrelated", "playing": 10, "created": "2025-07-01T00:00:00Z"},
    }
    release = int(datetime(2025, 6, 16, tzinfo=UTC).timestamp())
    clones, established = rb.classify("PEAK", ["climb", "obby"], release, found, details)
    assert [c.name for c in clones] == ["PEAK climb"]
    assert clones[0].url == "https://www.roblox.com/games/11"
    assert [e.name for e in established] == ["Climb Together Obby"]


def test_roblox_snapshot_from_fixtures(http: HttpClient, skills) -> None:
    data = skills.get("clone-search").data
    snap = SteamTools(http).snapshot(3219030)
    rob = rb.RobloxTools(http).snapshot(
        snap.name, snap.tags, snap.description, snap.release_ts, data["tag_keywords"], data["description_keywords"]
    )
    assert rob.queries[0] == "Planet Zoo 2"
    assert rob.leader is not None and rob.leader_players >= 1000
    assert rob.sources and all(s.tool.startswith("roblox.") for s in rob.sources)
