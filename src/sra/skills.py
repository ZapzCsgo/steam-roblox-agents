"""Skill library: Markdown files with YAML front matter, loaded on demand by agents.

A skill bundles instructions (the Markdown body, injected into prompts) and
optional machine-readable data (front matter `data:` block, used by code, for
example the Critic's consistency rules). Agents ask the registry for the skills
matching their role and task; the content hash of each skill used is written to
the journal so a decision can be tied to the exact rubric version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sra.trace import sha256_text


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    version: str
    description: str
    agents: tuple[str, ...]
    tasks: tuple[str, ...]
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    path: str = ""

    @property
    def digest(self) -> str:
        return sha256_text(self.body + repr(sorted(self.data.items())))

    @property
    def ref(self) -> str:
        """Short identifier written to the journal: name@version#hash."""
        return f"{self.name}@{self.version}#{self.digest[:8]}"


def parse_skill(text: str, path: str = "") -> Skill:
    if not text.startswith("---"):
        raise SkillError(f"{path}: missing YAML front matter")
    try:
        _, front, body = text.split("---", 2)
    except ValueError as exc:
        raise SkillError(f"{path}: unterminated front matter") from exc
    meta = yaml.safe_load(front) or {}
    for key in ("name", "version", "description", "agents"):
        if key not in meta:
            raise SkillError(f"{path}: front matter needs '{key}'")
    return Skill(
        name=str(meta["name"]),
        version=str(meta["version"]),
        description=str(meta["description"]),
        agents=tuple(meta["agents"]),
        tasks=tuple(meta.get("tasks", [])),
        body=body.strip(),
        data=dict(meta.get("data") or {}),
        path=path,
    )


class SkillRegistry:
    def __init__(self, skills: list[Skill]):
        names = [s.name for s in skills]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise SkillError(f"duplicate skill names: {sorted(dupes)}")
        self._skills = {s.name: s for s in skills}

    @classmethod
    def from_dir(cls, directory: Path) -> SkillRegistry:
        files = sorted(directory.glob("*.md"))
        if not files:
            raise SkillError(f"no skills found in {directory}")
        return cls([parse_skill(f.read_text(encoding="utf-8"), f.name) for f in files])

    def __len__(self) -> int:
        return len(self._skills)

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillError(f"unknown skill '{name}'") from exc

    def select(self, agent: str, task: str | None = None) -> list[Skill]:
        """Skills for an agent, optionally narrowed to a task tag. Sorted by name for stable prompts."""
        out = [s for s in self._skills.values() if agent in s.agents and (task is None or task in s.tasks)]
        return sorted(out, key=lambda s: s.name)

    def catalog(self) -> list[dict[str, str]]:
        return [
            {"name": s.name, "version": s.version, "agents": ", ".join(s.agents), "description": s.description}
            for s in sorted(self._skills.values(), key=lambda s: s.name)
        ]


def render(skills: list[Skill]) -> str:
    return "\n\n".join(f"## Skill: {s.name} (v{s.version})\n\n{s.body}" for s in skills)
