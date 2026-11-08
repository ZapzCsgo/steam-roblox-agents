from __future__ import annotations

import json

from sra.cli import DEMO_APPIDS
from sra.graph import build_graph, mermaid, run_candidate
from sra.trace import read_journal

BAD = {
    "assessments": [
        {"factor": "F2", "score": 4, "justification": "co-op job simulator, native genre", "confidence": "high"},
        {"factor": "F3", "score": 5, "justification": "trivial to clone in a weekend", "confidence": "high"},
        {"factor": "F4", "score": 4, "justification": "funny clips with friends", "confidence": "medium"},
        {"factor": "F6", "score": 5, "justification": "very short rounds in practice", "confidence": "low"},
        {"factor": "F8", "score": 5, "justification": "upgrades and boosters fit well", "confidence": "low"},
        {"factor": "F10", "score": 5, "justification": "generic mechanic, no protected IP", "confidence": "low"},
    ]
}


def test_demo_end_to_end(make_deps, tmp_path) -> None:
    deps = make_deps()
    graph = build_graph(deps)
    results = {a: run_candidate(graph, deps, a) for a in DEMO_APPIDS}

    moon = results[5335510]
    assert [v.approved for v in moon["critic_history"]] == [False, True]
    assert "C1" in moon["critic_history"][0].rule_ids
    assert moon["analyst_attempts"] == 2 and moon["validated"] and not moon["needs_human_review"]
    assert moon["llm_factors"]["F3"].score == 2
    assert moon["score"].verdict == "prototype" and moon["score"].durable_bonus == 6

    zed = results[1211600]
    assert len(zed["critic_history"]) == 1 and zed["validated"]

    for st in results.values():
        assert set(st["score"].factors) == {f"F{i}" for i in range(1, 11)}
        assert (tmp_path / "reports" / f"{st['appid']}.md").exists()
        assert "## Sources" in st["report_markdown"] and "fixture" in st["report_markdown"]

    events = read_journal(tmp_path / "journal.jsonl")
    nodes = {e["node"] for e in events}
    assert nodes >= {"collector", "roblox_scout", "rule_factors", "analyst", "critic", "scorer", "reporter"}
    collected = [e for e in events if e["event"] == "steam_snapshot"]
    assert collected and all(
        s["url"].startswith("https://") and s["fetched_at"] for e in collected for s in e["sources"]
    )
    analyst_events = [e for e in events if e["node"] == "analyst"]
    assert all(any(s.startswith("scoring-rubric@") for s in e["data"]["skills"]) for e in analyst_events)


def test_demo_is_deterministic(make_deps) -> None:
    def once() -> str:
        deps = make_deps()
        st = run_candidate(build_graph(deps), deps, 5335510)
        return json.dumps(st["score"].model_dump(), sort_keys=True) + st["report_markdown"]

    assert once() == once()


def test_max_two_revisions_then_clamp(make_deps) -> None:
    deps = make_deps(scripts={"analyst:5335510": [BAD]})
    st = run_candidate(build_graph(deps), deps, 5335510)
    assert st["analyst_attempts"] == 3 and len(st["critic_history"]) == 3
    assert not any(v.approved for v in st["critic_history"])
    assert st["needs_human_review"] and not st["validated"]
    assert st["llm_factors"]["F3"].score == 2 and st["llm_factors"]["F3"].origin == "critic-clamp"
    assert st["llm_factors"]["F6"].score == 3
    assert "critic-clamp" in st["report_markdown"]


def test_max_revisions_setting(make_deps) -> None:
    deps = make_deps(scripts={"analyst:5335510": [BAD]}, max_revisions=0)
    st = run_candidate(build_graph(deps), deps, 5335510)
    assert st["analyst_attempts"] == 1 and st["needs_human_review"]


def test_llm_critic_rejection_loops_back(make_deps) -> None:
    demo = json.loads((make_deps().settings.root / "fixtures/llm/demo.json").read_text())
    scripts = {
        "analyst:1211600": demo["analyst:1211600"] * 2,
        "critic:1211600": [
            {"approved": False, "issues": ["F4=2 ignores zombie horde clips"]},
            {"approved": True, "issues": []},
        ],
    }
    deps = make_deps(scripts=scripts)
    st = run_candidate(build_graph(deps), deps, 1211600)
    assert [v.rule_ids for v in st["critic_history"]] == [["LLM"], []]
    assert st["validated"]


def test_critic_llm_can_be_disabled(make_deps) -> None:
    scripts = {
        "analyst:3219030": json.loads((make_deps().settings.root / "fixtures/llm/demo.json").read_text())[
            "analyst:3219030"
        ],
        "critic:3219030": [{"approved": False, "issues": ["should not be called"]}],
    }
    deps = make_deps(scripts=scripts, critic_llm=False)
    st = run_candidate(build_graph(deps), deps, 3219030)
    assert st["validated"] and len(st["critic_history"]) == 1


def test_low_confidence_flags_human_review(make_deps) -> None:
    answer = json.loads(json.dumps(BAD))
    for a in answer["assessments"]:
        a["score"] = min(a["score"], 2)
    deps = make_deps(scripts={"analyst:5335510": [answer]})
    st = run_candidate(build_graph(deps), deps, 5335510)
    assert st["validated"] and st["needs_human_review"] and "low confidence" in st["review_reasons"][0]


def test_mermaid_lists_all_nodes() -> None:
    text = mermaid()
    for node in ("collector", "roblox_scout", "rule_factors", "analyst", "critic", "scorer", "reporter"):
        assert node in text
