"""Runtime settings, read from environment variables (12-factor style)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _repo_root() -> Path:
    here = Path(__file__).resolve().parents[2]
    return here if (here / "skills").is_dir() else Path.cwd()


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "fake"
    llm_model: str | None = None
    http_mode: str = "replay"
    root: Path = field(default_factory=_repo_root)
    max_revisions: int = 2
    critic_llm: bool = True
    fake_script: str = "fixtures/llm/demo.json"
    fake_noise: float = 0.0
    fake_seed: int = 0

    @property
    def fixtures_dir(self) -> Path:
        return self.root / "fixtures"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def output_dir(self) -> Path:
        return self.root / "runs"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_provider=os.getenv("SRA_LLM_PROVIDER", "fake").strip().lower(),
            llm_model=os.getenv("SRA_LLM_MODEL") or None,
            http_mode=os.getenv("SRA_HTTP_MODE", "replay").strip().lower(),
            root=Path(os.getenv("SRA_ROOT") or _repo_root()),
            max_revisions=int(os.getenv("SRA_MAX_REVISIONS", "2")),
            critic_llm=_flag("SRA_CRITIC_LLM", True),
            fake_script=os.getenv("SRA_FAKE_SCRIPT", "fixtures/llm/demo.json"),
            fake_noise=float(os.getenv("SRA_FAKE_NOISE", "0")),
            fake_seed=int(os.getenv("SRA_FAKE_SEED", "0")),
        )
