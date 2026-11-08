"""Record real model answers once, replay them offline.

RecordingChatModel wraps a live model and logs every exchange of the current candidate
(prompt hash + raw answer). RecordedChatModel replays those answers: it looks each prompt up
by its hash, so the graph runs exactly as it did with the live model, deterministically and
without network. If the prompt-building code changes, the hashes no longer match and replay
fails loudly with RecordingMismatchError: the recordings must then be refreshed.

File layout: eval/recordings/<model>/<case id>.json
    {"model": ..., "provider": ..., "case": ..., "appid": ...,
     "runs": [{"run": 0, "recorded_at": ..., "error": null,
               "exchanges": [{"role": "analyst", "prompt_sha": ..., "response": ..., ...}]}]}
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from sra.llm import ROLE_RE, TextChatModel, model_name, prompt_sha, transcript
from sra.trace import utc_now


class RecordingMismatchError(RuntimeError):
    pass


def _role(messages: list[BaseMessage]) -> str:
    m = ROLE_RE.search(transcript(messages))
    return m.group(1) if m else "unknown"


class RecordingChatModel(TextChatModel):
    """Pass-through to a live text model that keeps the exchanges of the current candidate."""

    inner: TextChatModel
    exchanges: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return f"recording:{self.inner._llm_type}"

    @property
    def model(self) -> str:
        return model_name(self.inner)

    def reset(self) -> None:
        self.exchanges = []
        self.inner.reset()

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        result = self.inner._generate(messages, stop=stop, **kwargs)
        text = str(result.generations[0].message.content)
        self.exchanges.append(
            {
                "role": _role(messages),
                "prompt_sha": prompt_sha(messages),
                "prompt_chars": len(transcript(messages)),
                "response": text,
                "attempts": int(getattr(self.inner, "last_attempts", 1) or 1),
                "seconds": float(getattr(self.inner, "last_seconds", 0.0) or 0.0),
            }
        )
        return result


class RecordedChatModel(TextChatModel):
    """Offline replay of recorded answers, keyed by prompt hash."""

    answers: dict[str, list[str]] = Field(default_factory=dict)
    label: str = "recorded"
    _used: dict[str, int] = PrivateAttr(default_factory=lambda: defaultdict(int))

    @property
    def _llm_type(self) -> str:
        return "recorded"

    @property
    def model(self) -> str:
        return self.label

    def reset(self) -> None:
        self._used.clear()

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        sha = prompt_sha(messages)
        queue = self.answers.get(sha)
        idx = self._used[sha]
        if not queue or idx >= len(queue):
            raise RecordingMismatchError(
                f"no recorded answer for {_role(messages)} prompt {sha} ({self.label}); "
                "the prompt changed since recording, re-record with scripts/record_llm.py"
            )
        self._used[sha] = idx + 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=queue[idx]))])


def answers_from_exchanges(exchanges: list[dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for ex in exchanges:
        out[ex["prompt_sha"]].append(ex["response"])
    return dict(out)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def case_path(root: Path, model: str, case_id: str) -> Path:
    return root / slug(model) / f"{case_id}.json"


def load_case(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_run(
    path: Path,
    *,
    model: str,
    provider: str,
    case_id: str,
    critic_llm: bool = False,
    appid: int,
    exchanges: list[dict[str, Any]],
    error: str | None = None,
    failed_calls: int = 0,
    run: int | None = None,
) -> dict[str, Any]:
    """Append one recorded run of a case to its file (created if needed). Returns the run entry."""
    data: dict[str, Any] = (
        load_case(path)
        if path.exists()
        else {
            "model": model,
            "provider": provider,
            "critic_llm": critic_llm,
            "case": case_id,
            "appid": appid,
            "runs": [],
        }
    )
    entry = {
        "run": len(data["runs"]) if run is None else run,
        "recorded_at": utc_now(),
        "error": error,
        "failed_calls": failed_calls,
        "exchanges": exchanges,
    }
    data["runs"].append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return entry


def load_model_dir(model_dir: Path) -> dict[str, dict[str, Any]]:
    """case id -> recorded file content, for one model directory."""
    return {p.stem: load_case(p) for p in sorted(model_dir.glob("*.json"))}


def replay_model(cases: dict[str, dict[str, Any]], run: int, label: str) -> RecordedChatModel:
    """One replay model holding run `run` of every case (prompts embed the app id, so hashes don't collide)."""
    answers: dict[str, list[str]] = defaultdict(list)
    for data in cases.values():
        runs = [r for r in data["runs"] if r["run"] == run]
        if runs:
            for sha, texts in answers_from_exchanges(runs[0]["exchanges"]).items():
                answers[sha] += texts
    return RecordedChatModel(answers=dict(answers), label=label)


def calls_used(root: Path) -> int:
    """Number of live model calls already spent, counting retries, over all recordings."""
    total = 0
    for path in root.glob("*/*.json"):
        for run in load_case(path)["runs"]:
            total += sum(int(ex.get("attempts", 1)) for ex in run["exchanges"])
            total += int(run.get("failed_calls", 0))
    return total
