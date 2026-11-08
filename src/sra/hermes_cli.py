"""Chat model backed by the Hermes agent CLI (`hermes chat`), one subprocess per call.

Used to record real model answers through a chat subscription instead of a paid API key:
the prompt is sent on stdin (`--query-file -`), the final answer is read from stdout and the
trailing `session_id: ...` line is dropped. Transport failures (timeout, non-zero exit, empty
answer, no JSON object at all) are retried once; schema validation stays in invoke_structured.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from sra.llm import ConfigError, StructuredOutputError, TextChatModel, extract_json, transcript

SESSION_LINE = re.compile(r"^\s*session_id:\s*\S+\s*$", re.MULTILINE)

Runner = Callable[..., subprocess.CompletedProcess[str]]


class CLICallError(RuntimeError):
    pass


def clean_output(stdout: str) -> str:
    """Drop the `session_id: ...` footer and surrounding blank lines."""
    return SESSION_LINE.sub("", stdout).strip()


class HermesCLIChatModel(TextChatModel):
    provider: str
    model: str
    command: str = "hermes"
    timeout_s: float = 300.0
    attempts: int = 2
    pause_s: float = 2.0
    require_json: bool = True
    # isolate the call from the user's memory/rules and keep the model from using tools
    extra_args: list[str] = Field(
        default_factory=lambda: ["--ignore-rules", "--source", "tool", "-t", "", "--max-turns", "3"]
    )
    # injectable for tests (None = subprocess.run / time.sleep)
    _runner: Runner | None = PrivateAttr(default=None)
    _sleep: Callable[[float], None] | None = PrivateAttr(default=None)
    last_attempts: int = 0
    last_seconds: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "hermes-cli"

    @classmethod
    def from_spec(cls, spec: str, **kwargs: Any) -> HermesCLIChatModel:
        """Build from "provider/model", e.g. "openai-codex/gpt-6-sol"."""
        provider, sep, model = spec.partition("/")
        if not sep or not provider or not model:
            raise ConfigError("provider 'hermes' needs SRA_LLM_MODEL=<hermes provider>/<model>")
        return cls(provider=provider, model=model, **kwargs)

    def argv(self) -> list[str]:
        return [
            self.command,
            "chat",
            "--query-file",
            "-",
            "--provider",
            self.provider,
            "-m",
            self.model,
            "-Q",
            *self.extra_args,
        ]

    def call(self, prompt: str) -> str:
        run = self._runner or subprocess.run
        sleep = self._sleep or time.sleep
        errors: list[str] = []
        start = time.monotonic()
        for attempt in range(1, self.attempts + 1):
            self.last_attempts = attempt
            if attempt > 1:
                sleep(self.pause_s)
            try:
                proc = run(
                    self.argv(), input=prompt, capture_output=True, text=True, timeout=self.timeout_s, check=False
                )
            except subprocess.TimeoutExpired:
                errors.append(f"attempt {attempt}: timeout after {self.timeout_s:.0f}s")
                continue
            except OSError as exc:
                raise CLICallError(f"cannot run {self.command}: {exc}") from exc
            text = clean_output(proc.stdout or "")
            if proc.returncode != 0:
                errors.append(f"attempt {attempt}: exit {proc.returncode}: {(proc.stderr or '').strip()[-200:]}")
                continue
            if not text:
                errors.append(f"attempt {attempt}: empty answer")
                continue
            if self.require_json:
                try:
                    extract_json(text)
                except StructuredOutputError:
                    errors.append(f"attempt {attempt}: no JSON object in answer")
                    continue
            self.last_seconds = round(time.monotonic() - start, 1)
            sleep(self.pause_s)  # spacing between calls
            return text
        self.last_seconds = round(time.monotonic() - start, 1)
        raise CLICallError("; ".join(errors))

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        text = self.call(transcript(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])
