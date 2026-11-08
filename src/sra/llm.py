"""LLM layer: interchangeable providers and a deterministic scripted model.

Real providers go through langchain's init_chat_model (Google Gemini, Anthropic,
OpenAI, Ollama). Tests, CI, the demo and the evaluation use ScriptedChatModel,
which replays recorded answers and can inject seeded noise to simulate sampling.
Text-only models (scripted, recorded, CLI) subclass TextChatModel: invoke_structured
asks them for JSON and validates it with Pydantic instead of using tool calling.
"""

from __future__ import annotations

import json
import os
import random
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from sra.config import Settings
from sra.trace import sha256_text

T = TypeVar("T", bound=BaseModel)

# provider alias -> (init_chat_model provider, default model, required env var)
PROVIDERS: dict[str, tuple[str, str, str | None]] = {
    "google": ("google_genai", "gemini-2.5-flash", "GOOGLE_API_KEY"),
    "anthropic": ("anthropic", "claude-haiku-4-5", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
    "ollama": ("ollama", "qwen2.5:7b", None),
}

ROLE_RE = re.compile(r"ROLE: (\w+)")
APPID_RE = re.compile(r"APPID: (\d+)")


class ConfigError(RuntimeError):
    pass


class StructuredOutputError(RuntimeError):
    pass


class TextChatModel(BaseChatModel):
    """Base for models that answer in plain text (no native structured output)."""

    def reset(self) -> None:
        """Called before each candidate; stateful models (scripts, recordings) rewind here."""


def transcript(messages: Sequence[BaseMessage]) -> str:
    """Flatten a message list into one text prompt (used by CLI models and prompt hashes)."""
    return "\n\n".join(f"### {m.type.upper()}\n{m.content}" for m in messages)


def prompt_sha(messages: Sequence[BaseMessage]) -> str:
    return sha256_text(transcript(messages))


class ScriptedChatModel(TextChatModel):
    """Deterministic chat model for offline runs.

    Answers are looked up by "<role>:<appid>", both read from the prompt markers
    `ROLE:` and `APPID:`. Successive calls for the same key return successive
    scripted answers (the last one repeats). With noise > 0, each Analyst score
    moves by +-1 with that probability, using a RNG seeded by (seed, key, call).
    """

    scripts: dict[str, list[Any]] = Field(default_factory=dict)
    default_critic: dict[str, Any] = Field(default_factory=lambda: {"approved": True, "issues": []})
    noise: float = 0.0
    seed: int = 0
    _calls: dict[str, int] = PrivateAttr(default_factory=dict)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    @classmethod
    def from_file(cls, path: Path, noise: float = 0.0, seed: int = 0) -> ScriptedChatModel:
        return cls(scripts=json.loads(path.read_text(encoding="utf-8")), noise=noise, seed=seed)

    def reset(self) -> None:
        self._calls.clear()

    def _answer(self, text: str) -> str:
        role_m, appid_m = ROLE_RE.search(text), APPID_RE.search(text)
        role = role_m.group(1) if role_m else "unknown"
        key = f"{role}:{appid_m.group(1) if appid_m else '?'}"
        idx = self._calls.get(key, 0)
        self._calls[key] = idx + 1
        answers = self.scripts.get(key)
        if not answers:
            if role == "critic":
                return json.dumps(self.default_critic)
            raise StructuredOutputError(f"no scripted answer for {key}")
        answer = answers[min(idx, len(answers) - 1)]
        if role == "analyst" and self.noise > 0:
            answer = self._perturb(answer, key, idx)
        return answer if isinstance(answer, str) else json.dumps(answer)

    def _perturb(self, answer: Any, key: str, idx: int) -> Any:
        data = json.loads(answer) if isinstance(answer, str) else json.loads(json.dumps(answer))
        for item in data.get("assessments", []):
            rng = random.Random(f"{self.seed}:{key}:{idx}:{item['factor']}")
            if rng.random() < self.noise:
                item["score"] = max(0, min(5, int(item["score"]) + rng.choice((-1, 1))))
        return data

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        text = "\n".join(str(m.content) for m in messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._answer(text)))])


def build_chat_model(settings: Settings) -> BaseChatModel:
    """Return the configured chat model. 'fake' (default) never touches the network."""
    provider = settings.llm_provider
    if provider == "fake":
        return ScriptedChatModel.from_file(
            settings.root / settings.fake_script, noise=settings.fake_noise, seed=settings.fake_seed
        )
    if provider == "hermes":
        from sra.hermes_cli import HermesCLIChatModel

        return HermesCLIChatModel.from_spec(settings.llm_model or "")
    if provider not in PROVIDERS:
        raise ConfigError(f"unknown SRA_LLM_PROVIDER '{provider}', expected fake, hermes or one of {sorted(PROVIDERS)}")
    lc_provider, default_model, env_key = PROVIDERS[provider]
    if env_key and not os.getenv(env_key):
        raise ConfigError(f"provider '{provider}' needs the {env_key} environment variable")
    from langchain.chat_models import init_chat_model

    return init_chat_model(settings.llm_model or default_model, model_provider=lc_provider, temperature=0)


def model_name(model: BaseChatModel) -> str:
    for attr in ("model_name", "model"):
        value = getattr(model, attr, None)
        if isinstance(value, str):
            return value
    return model._llm_type


def extract_json(text: str) -> str:
    """Return the first decodable JSON object in the text.

    Tolerates Markdown fences, prose before or after the object and stray braces in that prose.
    Falls back to the outermost {...} span so Pydantic can report a precise error.
    """
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, end = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return text[m.start() : end]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise StructuredOutputError("no JSON object in model output")
    return text[start : end + 1]


def invoke_structured(model: BaseChatModel, schema: type[T], messages: Sequence[BaseMessage]) -> tuple[T, str]:
    """Call the model and return (validated object, raw text).

    Native structured output (tool calling) is used when the provider supports it;
    text-only models are asked for JSON, validated by Pydantic, and given one retry
    with the validation error.
    """
    if not isinstance(model, TextChatModel):
        runnable = model.with_structured_output(schema, include_raw=True)
        out = runnable.invoke(list(messages))
        result: dict[str, Any] = out if isinstance(out, dict) else {"parsed": out}
        parsed = result.get("parsed")
        if isinstance(parsed, schema):
            raw = result.get("raw")
            return parsed, str(getattr(raw, "content", "") or parsed.model_dump_json())
        raise StructuredOutputError(f"structured output failed: {result.get('parsing_error')}")

    msgs = [
        *messages,
        HumanMessage(
            "Answer with one JSON object matching this JSON schema, nothing else:\n"
            + json.dumps(schema.model_json_schema())
        ),
    ]
    last_error: Exception | None = None
    for _ in range(2):
        ai = model.invoke(msgs)
        text = str(ai.content)
        try:
            return schema.model_validate_json(extract_json(text)), text
        except (ValidationError, StructuredOutputError) as exc:
            last_error = exc
            msgs += [ai, HumanMessage(f"Invalid answer: {exc}. Return only the corrected JSON object.")]
    raise StructuredOutputError(f"invalid structured output after retry: {last_error}")
