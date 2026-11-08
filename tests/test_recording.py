from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from sra.cli import main
from sra.evaluation import load_cases, naive_baseline, naive_verdict, run_with_model, scripted_answers
from sra.llm import ScriptedChatModel, prompt_sha
from sra.recorded_eval import replay_rows, run_recorded_evaluation
from sra.recording import (
    RecordedChatModel,
    RecordingChatModel,
    RecordingMismatchError,
    calls_used,
    case_path,
    load_model_dir,
    save_run,
)

IDS = ("repo", "fall-guys", "balatro")


def record(root: Path, out: Path, model: str = "m-1", runs: int = 1, critic_llm: bool = True) -> list[dict]:
    """Record the scripted model as if it were a live one; return the rows of the live run 0."""
    cases, study_date = load_cases(root / "eval" / "cases.yaml")
    cases = [c for c in cases if c.id in IDS]
    rec = RecordingChatModel(inner=ScriptedChatModel(scripts=scripted_answers(cases)))
    live_rows = []
    for run in range(runs):
        for c in cases if run == 0 else cases[:1]:
            row = run_with_model(root, [c], study_date, rec, "live", critic_llm)[0]
            if run == 0:
                live_rows.append(row)
            save_run(
                case_path(out, model, c.id),
                model=model,
                provider="test",
                case_id=c.id,
                appid=c.appid,
                exchanges=rec.exchanges,
                critic_llm=critic_llm,
                run=run,
            )
    return live_rows


def test_recording_then_replay_reproduces_the_live_run(root: Path, tmp_path: Path) -> None:
    live = record(root, tmp_path)
    recs = load_model_dir(tmp_path / "m-1")
    assert set(recs) == set(IDS)
    ex = recs["repo"]["runs"][0]["exchanges"]
    assert [e["role"] for e in ex] == ["analyst", "critic"] and all(len(e["prompt_sha"]) == 16 for e in ex)
    cases, study_date = load_cases(root / "eval" / "cases.yaml")
    replayed = replay_rows(root, cases, study_date, recs, 0, "m-1")
    assert {r["id"]: r for r in replayed} == {r["id"]: r for r in live}
    assert calls_used(tmp_path) == 6


def test_replay_fails_loudly_when_the_prompt_changed() -> None:
    msgs = [SystemMessage("ROLE: analyst"), HumanMessage("APPID: 1")]
    m = RecordedChatModel(answers={prompt_sha(msgs): ["{}"]}, label="x")
    assert m.invoke(msgs).content == "{}"
    with pytest.raises(RecordingMismatchError, match="re-record"):
        m.invoke(msgs)  # recorded once, asked twice
    m.reset()
    assert m.invoke(msgs).content == "{}"
    with pytest.raises(RecordingMismatchError):
        m.invoke([SystemMessage("ROLE: analyst"), HumanMessage("APPID: 2")])


def test_failed_live_run_is_an_error_row(root: Path, tmp_path: Path) -> None:
    cases, study_date = load_cases(root / "eval" / "cases.yaml")
    c = next(c for c in cases if c.id == "repo")
    save_run(
        case_path(tmp_path, "m", c.id),
        model="m",
        provider="t",
        case_id=c.id,
        appid=c.appid,
        exchanges=[],
        error="CLICallError: timeout",
        failed_calls=2,
    )
    rows = replay_rows(root, cases, study_date, load_model_dir(tmp_path / "m"), 0, "m")
    assert len(rows) == 1 and rows[0]["verdict"] == "error" and rows[0]["error"].startswith("CLICallError")
    assert calls_used(tmp_path) == 2


def test_recorded_evaluation_end_to_end(root: Path, tmp_path: Path, capsys) -> None:
    rec_dir, out = tmp_path / "rec", tmp_path / "out"
    record(root, rec_dir, "m-1", runs=3)
    record(root, rec_dir, "m-2", critic_llm=False)
    m = run_recorded_evaluation(root, rec_dir=rec_dir, out_dir=out)
    a, b = m["models"]
    assert a["model"] == "m-1 + LLM critic" and b["model"] == "m-2" and not b["critic_llm"]
    assert a["accuracy"]["n"] == 3 and a["replay_identical"] and len(a["missing_cases"]) == 18
    assert a["stability"]["runs"] == 3 and a["stability"]["mean_verdict_agreement"] == 1.0
    assert a["factor_gaps"]["F2"] == {"bias": 0, "mae": 0}  # scripted answers are the reference scores
    assert b["usage"]["calls_by_role"] == {"analyst": 3}
    assert "naive (peak)" in (out / "report.md").read_text()
    assert main(["eval", "--recorded", "--recordings", str(rec_dir), "--out", str(tmp_path / "cli")]) == 0
    assert (tmp_path / "cli" / "metrics.json").read_text() == (out / "metrics.json").read_text()


def test_naive_baseline(root: Path) -> None:
    assert naive_verdict(None) == "pass" and naive_verdict(200_000) == "prototype" and naive_verdict(60_000) == "watch"
    cases, _ = load_cases(root / "eval" / "cases.yaml")
    nb = naive_baseline(cases)
    assert len(nb["verdicts"]) == 21 and 0 <= nb["strict_accuracy"] <= nb["hindsight_best"]["strict_accuracy"] <= 1
