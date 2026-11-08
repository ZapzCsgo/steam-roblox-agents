"""HTTP client with three modes: live, record (live + save) and replay (fixtures only).

Replay makes the whole pipeline reproducible offline: tests, CI, the demo and the
evaluation never touch the network. Every response comes back with a SourceRef
so the agents can cite where each number came from.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

import requests

from sra.state import SourceRef
from sra.trace import utc_now

Mode = Literal["live", "record", "replay"]
VOLATILE_PARAMS = {"sessionId"}
USER_AGENT = "steam-roblox-agents/0.1 (+research prototype; polite rate limits)"
MIN_INTERVAL_S = {
    "api.steampowered.com": 1.0,
    "store.steampowered.com": 1.5,
    "steamcharts.com": 2.0,
    "apis.roproxy.com": 1.0,
    "games.roproxy.com": 1.0,
    "games.roblox.com": 1.0,
}


class FixtureMissingError(LookupError):
    pass


class HttpError(RuntimeError):
    pass


@dataclass
class Response:
    data: Any
    source: SourceRef
    status: int = 200


def fixture_key(url: str, params: dict[str, Any] | None) -> str:
    stable = {k: v for k, v in sorted((params or {}).items()) if k not in VOLATILE_PARAMS}
    raw = f"GET {url}?{urlencode(stable, doseq=True)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def display_url(url: str, params: dict[str, Any] | None) -> str:
    stable = {k: v for k, v in sorted((params or {}).items()) if k not in VOLATILE_PARAMS}
    return f"{url}?{urlencode(stable, doseq=True)}" if stable else url


@dataclass
class HttpClient:
    mode: Mode = "replay"
    fixtures_dir: Path = Path("fixtures/http")
    timeout_s: float = 25.0
    session: requests.Session = field(default_factory=requests.Session)
    _last_call: dict[str, float] = field(default_factory=dict)
    calls: int = 0

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        host = urlparse(url).netloc
        return self.fixtures_dir / host / f"{fixture_key(url, params)}.json"

    def _throttle(self, host: str) -> None:
        wait = self._last_call.get(host, 0.0) + MIN_INTERVAL_S.get(host, 1.0) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call[host] = time.monotonic()

    def get_json(self, url: str, params: dict[str, Any] | None = None, *, tool: str) -> Response:
        """GET a JSON document. tool names the agent tool, kept in the SourceRef."""
        path = self._path(url, params)
        shown = display_url(url, params)
        if self.mode == "replay":
            if not path.exists():
                raise FixtureMissingError(f"no fixture for {shown} ({path.name}); record it with --http record")
            stored = json.loads(path.read_text(encoding="utf-8"))
            src = SourceRef(tool=tool, url=shown, fetched_at=stored["fetched_at"], origin="fixture")
            return Response(stored["json"], src, stored.get("status", 200))

        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(3):
            self._throttle(host)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_s)
            except requests.RequestException as exc:  # pragma: no cover - network only
                last_exc = exc
                time.sleep(2 * (attempt + 1))
                continue
            self.calls += 1
            if resp.status_code == 429 or resp.status_code >= 500:  # pragma: no cover - network only
                last_exc = HttpError(f"{host} HTTP {resp.status_code}")
                time.sleep(5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise HttpError(f"{host} HTTP {resp.status_code}")
            fetched_at = utc_now()
            data = resp.json()
            if self.mode == "record":
                path.parent.mkdir(parents=True, exist_ok=True)
                stored = {
                    "request": {"url": url, "params": params or {}},
                    "fetched_at": fetched_at,
                    "status": resp.status_code,
                    "json": data,
                }
                path.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
            return Response(data, SourceRef(tool=tool, url=shown, fetched_at=fetched_at, origin="live"))
        raise HttpError(f"{host}: giving up after retries ({last_exc})")  # pragma: no cover
