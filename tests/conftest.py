from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from sra.config import Settings
from sra.deps import Deps
from sra.llm import ScriptedChatModel
from sra.skills import SkillRegistry
from sra.sources.http import HttpClient
from sra.trace import Journal

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def settings() -> Settings:
    return dataclasses.replace(Settings(root=ROOT), llm_provider="fake", http_mode="replay")


@pytest.fixture
def skills() -> SkillRegistry:
    return SkillRegistry.from_dir(ROOT / "skills")


@pytest.fixture
def http() -> HttpClient:
    return HttpClient(mode="replay", fixtures_dir=ROOT / "fixtures" / "http")


@pytest.fixture
def make_deps(settings: Settings, skills: SkillRegistry, http: HttpClient, tmp_path: Path):
    def _make(scripts: dict | None = None, **overrides) -> Deps:
        llm = (
            ScriptedChatModel(scripts=scripts)
            if scripts is not None
            else ScriptedChatModel.from_file(ROOT / "fixtures" / "llm" / "demo.json")
        )
        return Deps(
            settings=dataclasses.replace(settings, **overrides),
            http=http,
            llm=llm,
            skills=skills,
            journal=Journal(tmp_path / "journal.jsonl", "test-run", clock=lambda: "2026-01-01T00:00:00Z"),
            reports_dir=tmp_path / "reports",
        )

    return _make
