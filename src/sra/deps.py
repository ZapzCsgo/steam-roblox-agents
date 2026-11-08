"""Shared dependencies injected into every node (no globals, easy to fake in tests)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain_core.language_models import BaseChatModel

from sra.config import Settings
from sra.skills import SkillRegistry
from sra.sources.http import HttpClient
from sra.trace import Journal


@dataclass
class Deps:
    settings: Settings
    http: HttpClient
    llm: BaseChatModel
    skills: SkillRegistry
    journal: Journal
    reports_dir: Path | None = None
