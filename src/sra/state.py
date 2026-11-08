"""Typed data contracts shared by the agents and the LangGraph state."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
LLMFactor = Literal["F2", "F3", "F4", "F6", "F8", "F10"]


class SourceRef(BaseModel):
    """Where a piece of evidence came from. Every decision keeps a list of these."""

    model_config = ConfigDict(frozen=True)

    tool: str
    url: str
    fetched_at: str
    origin: Literal["live", "fixture", "case-file"] = "live"


class SteamSnapshot(BaseModel):
    appid: int
    name: str
    tags: list[str] = Field(default_factory=list)
    categories: list[int] = Field(default_factory=list)
    content_descriptors: list[int] = Field(default_factory=list)
    content_notes: str | None = None
    is_free: bool = False
    price_cents: int | None = None
    description: str = ""
    release_ts: int | None = None
    peak_ccu: int | None = None
    peak_date: str | None = None
    days_since_peak: int | None = None
    current_players: int | None = None
    pct_change_7d: float | None = None
    reviews_7d: int | None = None
    review_acceleration: float | None = None
    sources: list[SourceRef] = Field(default_factory=list)

    @property
    def store_url(self) -> str:
        return f"https://store.steampowered.com/app/{self.appid}/"


class RobloxGame(BaseModel):
    name: str
    universe_id: int | None = None
    players: int = 0
    visits: int | None = None
    created: str | None = None
    url: str | None = None


class RobloxSnapshot(BaseModel):
    queries: list[str] = Field(default_factory=list)
    raw_results: int = 0
    clones: list[RobloxGame] = Field(default_factory=list)
    active_clones: int = 0
    best_clone_players: int = 0
    leader: RobloxGame | None = None
    sources: list[SourceRef] = Field(default_factory=list)

    @property
    def leader_players(self) -> int:
        return self.leader.players if self.leader else 0


class FactorScore(BaseModel):
    factor: str
    score: int = Field(ge=0, le=5)
    justification: str
    confidence: Confidence = "medium"
    origin: Literal["rule", "llm", "critic-clamp"] = "rule"
    sources: list[SourceRef] = Field(default_factory=list)


class LLMAssessment(BaseModel):
    """One factor judged by the Analyst. This is the structured output schema sent to the LLM."""

    factor: LLMFactor
    score: int = Field(ge=0, le=5, description="Integer score from 0 to 5 following the rubric.")
    justification: str = Field(description="One sentence citing the evidence used.")
    confidence: Confidence


class AnalystOutput(BaseModel):
    assessments: list[LLMAssessment] = Field(description="Exactly one assessment for each of F2, F3, F4, F6, F8, F10.")


class CriticReview(BaseModel):
    """Optional LLM second opinion produced by the Critic."""

    approved: bool
    issues: list[str] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    attempt: int
    approved: bool
    issues: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    score: int
    verdict: Literal["prototype", "watch", "pass"]
    raw: float
    durable_bonus: int
    saturation_malus: int
    cap: str | None
    decay: float
    grid_version: str
    factors: dict[str, int]


class PipelineState(TypedDict, total=False):
    """LangGraph state. Lists annotated with operator.add are append-only (audit trail)."""

    run_id: str
    appid: int
    steam: SteamSnapshot
    roblox: RobloxSnapshot
    durable_genre: bool
    durable_genre_kind: str | None
    saturated: bool
    hints: dict[str, str]
    rule_factors: dict[str, FactorScore]
    llm_factors: dict[str, FactorScore]
    analyst_attempts: int
    critic_feedback: list[str]
    critic_history: Annotated[list[CriticVerdict], operator.add]
    validated: bool
    needs_human_review: bool
    review_reasons: list[str]
    score: ScoreBreakdown
    report_markdown: str
    report_path: str
    skills_used: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    meta: dict[str, Any]
