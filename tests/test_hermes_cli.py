from __future__ import annotations

import dataclasses
import subprocess
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from sra.config import Settings
from sra.hermes_cli import CLICallError, HermesCLIChatModel, clean_output
from sra.llm import ConfigError, build_chat_model, extract_json, invoke_structured, model_name, transcript
from sra.state import CriticReview


class FakeRunner:
    """Stands in for subprocess.run: returns queued outcomes, records the calls."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"argv": argv, **kwargs})
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        code, stdout = out
        return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="boom" if code else "")


def model(runner: FakeRunner, **kw: Any) -> HermesCLIChatModel:
    m = HermesCLIChatModel(provider="openai-codex", model="gpt-x", **kw)
    m._runner = runner
    m._sleep = lambda _s: None
    return m


MSGS = [SystemMessage("ROLE: critic"), HumanMessage("APPID: 1")]


def test_clean_output_drops_session_footer() -> None:
    raw = '{"approved": true}\n\nsession_id: local-abc123\n'
    assert clean_output(raw) == '{"approved": true}'


def test_prompt_goes_to_stdin_with_quiet_flags() -> None:
    runner = FakeRunner((0, '{"approved": true, "issues": []}\nsession_id: x\n'))
    m = model(runner)
    assert m.invoke(MSGS).content == '{"approved": true, "issues": []}'
    call = runner.calls[0]
    assert call["input"] == transcript(MSGS) and "ROLE: critic" in call["input"]
    argv = call["argv"]
    assert argv[:4] == ["hermes", "chat", "--query-file", "-"] and "-Q" in argv
    assert argv[argv.index("--provider") + 1] == "openai-codex" and argv[argv.index("-m") + 1] == "gpt-x"
    assert "--ignore-rules" in argv and call["timeout"] == m.timeout_s
    assert m.last_attempts == 1 and model_name(m) == "gpt-x"


@pytest.mark.parametrize(
    "first",
    [subprocess.TimeoutExpired("hermes", 1), (1, ""), (0, "session_id: x\n"), (0, "Sure, here you go.")],
    ids=["timeout", "exit-code", "empty", "no-json"],
)
def test_retries_once_on_transport_failure(first: Any) -> None:
    runner = FakeRunner(first, (0, 'Here: {"approved": false, "issues": ["F2"]}'))
    m = model(runner)
    review, _ = invoke_structured(m, CriticReview, MSGS)
    assert review.issues == ["F2"] and m.last_attempts == 2 and len(runner.calls) == 2


def test_gives_up_after_two_attempts() -> None:
    m = model(FakeRunner((1, ""), subprocess.TimeoutExpired("hermes", 1)))
    with pytest.raises(CLICallError, match=r"exit 1.*timeout"):
        m.invoke(MSGS)


def test_missing_binary() -> None:
    m = model(FakeRunner(FileNotFoundError("hermes")))
    with pytest.raises(CLICallError, match="cannot run"):
        m.invoke(MSGS)


def test_schema_errors_are_retried_by_invoke_structured() -> None:
    runner = FakeRunner((0, '{"approved": "maybe"}'), (0, '{"approved": true}'))
    review, _ = invoke_structured(model(runner), CriticReview, MSGS)
    assert review.approved is True
    assert "Invalid answer" in runner.calls[1]["input"]


def test_extract_json_ignores_prose_and_stray_braces() -> None:
    text = 'Note {not json} first.\n```json\n{"a": {"b": 1}}\n```\nThanks {bye}'
    assert extract_json(text) == '{"a": {"b": 1}}'


def test_build_chat_model_hermes(root) -> None:
    base = dataclasses.replace(Settings(root=root), llm_provider="hermes", llm_model="anthropic/claude-x")
    m = build_chat_model(base)
    assert isinstance(m, HermesCLIChatModel) and (m.provider, m.model) == ("anthropic", "claude-x")
    with pytest.raises(ConfigError, match="provider"):
        build_chat_model(dataclasses.replace(base, llm_model="no-slash"))
