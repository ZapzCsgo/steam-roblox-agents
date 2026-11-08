from __future__ import annotations

import dataclasses
import json

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from sra.config import Settings
from sra.llm import (
    ConfigError,
    ScriptedChatModel,
    StructuredOutputError,
    build_chat_model,
    extract_json,
    invoke_structured,
    model_name,
)
from sra.state import AnalystOutput, CriticReview

ANSWER = {
    "assessments": [
        {"factor": f, "score": 3, "justification": "a long enough justification", "confidence": "medium"}
        for f in ("F2", "F3", "F4", "F6", "F8", "F10")
    ]
}


def msgs(role: str, appid: int = 1) -> list:
    return [SystemMessage(f"ROLE: {role}"), HumanMessage(f"APPID: {appid}")]


def test_scripted_sequence_and_last_repeats() -> None:
    m = ScriptedChatModel(scripts={"analyst:1": ["first", "second"]})
    out = [m.invoke(msgs("analyst")).content for _ in range(3)]
    assert out == ["first", "second", "second"]
    m.reset()
    assert m.invoke(msgs("analyst")).content == "first"


def test_scripted_default_critic_and_missing_analyst() -> None:
    m = ScriptedChatModel()
    assert json.loads(str(m.invoke(msgs("critic")).content))["approved"] is True
    with pytest.raises(StructuredOutputError):
        m.invoke(msgs("analyst"))


def test_noise_is_seeded() -> None:
    def scores(seed: int) -> list[int]:
        m = ScriptedChatModel(scripts={"analyst:1": [ANSWER]}, noise=0.5, seed=seed)
        out, _ = invoke_structured(m, AnalystOutput, msgs("analyst"))
        return [a.score for a in out.assessments]

    assert scores(1) == scores(1)
    assert any(scores(s) != [3] * 6 for s in range(5))


def test_invoke_structured_retries_invalid_json() -> None:
    m = ScriptedChatModel(scripts={"critic:1": ["not json", {"approved": False, "issues": ["F2 too high"]}]})
    review, raw = invoke_structured(m, CriticReview, msgs("critic"))
    assert review.issues == ["F2 too high"] and "approved" in raw


def test_invoke_structured_gives_up() -> None:
    m = ScriptedChatModel(scripts={"critic:1": ["nope"]})
    with pytest.raises(StructuredOutputError):
        invoke_structured(m, CriticReview, msgs("critic"))


def test_extract_json() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    with pytest.raises(StructuredOutputError):
        extract_json("no braces")


def test_build_chat_model(monkeypatch: pytest.MonkeyPatch, root) -> None:
    base = Settings(root=root)
    fake = build_chat_model(base)
    assert isinstance(fake, ScriptedChatModel) and model_name(fake) == "scripted"
    with pytest.raises(ConfigError, match="unknown"):
        build_chat_model(dataclasses.replace(base, llm_provider="nope"))
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="GOOGLE_API_KEY"):
        build_chat_model(dataclasses.replace(base, llm_provider="google"))


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRA_LLM_PROVIDER", "Ollama")
    monkeypatch.setenv("SRA_CRITIC_LLM", "no")
    monkeypatch.setenv("SRA_MAX_REVISIONS", "1")
    s = Settings.from_env()
    assert s.llm_provider == "ollama" and s.critic_llm is False and s.max_revisions == 1
    assert s.skills_dir.name == "skills" and s.fixtures_dir.name == "fixtures"
