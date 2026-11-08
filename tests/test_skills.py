from __future__ import annotations

from pathlib import Path

import pytest

from sra.skills import SkillError, SkillRegistry, parse_skill, render

GOOD = """---
name: demo
version: "2"
description: a demo skill
agents: [analyst]
tasks: [score-factors]
data:
  answer: 42
---
Body text.
"""


def test_parse_skill() -> None:
    s = parse_skill(GOOD, "demo.md")
    assert s.name == "demo" and s.version == "2" and s.data == {"answer": 42}
    assert s.body == "Body text." and s.ref.startswith("demo@2#")


def test_digest_changes_with_content() -> None:
    assert parse_skill(GOOD).digest != parse_skill(GOOD.replace("Body", "Other")).digest


@pytest.mark.parametrize("text", ["no front matter", "---\nname: x\n", "---\nname: x\n---\nbody"])
def test_invalid_skills(text: str) -> None:
    with pytest.raises(SkillError):
        parse_skill(text)


def test_registry_selection(skills: SkillRegistry) -> None:
    assert len(skills) >= 5
    analyst = [s.name for s in skills.select("analyst", "score-factors")]
    assert analyst == ["roblox-platform-rules", "scoring-rubric"]
    assert [s.name for s in skills.select("critic", "review")] == ["consistency-rules", "scoring-rubric"]
    assert skills.select("nobody") == []
    assert "## Skill: scoring-rubric" in render(skills.select("analyst"))
    assert {c["name"] for c in skills.catalog()} >= {"clone-search", "report-template"}


def test_registry_errors(tmp_path: Path, skills: SkillRegistry) -> None:
    with pytest.raises(SkillError):
        skills.get("missing")
    with pytest.raises(SkillError):
        SkillRegistry([parse_skill(GOOD), parse_skill(GOOD)])
    with pytest.raises(SkillError):
        SkillRegistry.from_dir(tmp_path)


def test_consistency_rules_are_well_formed(skills: SkillRegistry) -> None:
    rules = skills.get("consistency-rules").data
    ids = [b["id"] for b in rules["bounds"]]
    assert len(ids) == len(set(ids))
    for b in rules["bounds"]:
        assert b["factor"] in {"F2", "F3", "F4", "F6", "F8", "F10"}
        assert ("max" in b) != ("min" in b) and b["why"]
