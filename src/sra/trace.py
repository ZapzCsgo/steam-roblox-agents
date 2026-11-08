"""Append-only JSONL decision journal.

Each line is one event: which node did what, with which inputs, and from which
sources (URL / endpoint / fetch time / live or fixture). The journal is the
audit trail used to reconstruct any score after the fact.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sra.state import SourceRef


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(v) for v in value]
    return value


class Journal:
    """Thread-safe JSONL writer. path=None keeps events in memory only (tests, eval)."""

    def __init__(self, path: Path | None, run_id: str, clock: Callable[[], str] = utc_now):
        self.path = path
        self.run_id = run_id
        self.clock = clock
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        node: str,
        event: str,
        *,
        appid: int | None = None,
        data: dict[str, Any] | None = None,
        sources: Iterable[SourceRef] = (),
    ) -> dict[str, Any]:
        entry = {
            "ts": self.clock(),
            "run_id": self.run_id,
            "node": node,
            "event": event,
            "appid": appid,
            "data": _jsonable(data or {}),
            "sources": [s.model_dump() for s in sources],
        }
        with self._lock:
            self.events.append(entry)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry


def read_journal(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
