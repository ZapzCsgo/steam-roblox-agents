from __future__ import annotations

from pathlib import Path

from sra.cli import main
from sra.evaluation import acceptable, load_cases, reference_baseline, run_evaluation, stability


def test_cases_file(root: Path) -> None:
    cases, study_date = load_cases(root / "eval" / "cases.yaml")
    assert len(cases) == 21 and study_date.startswith("2026")
    assert {c.expected for c in cases} == {"prototype", "watch", "pass"}
    assert len({c.appid for c in cases}) == 21


def test_acceptable() -> None:
    assert acceptable("watch", "success") and not acceptable("pass", "success")
    assert acceptable("pass", "failure") and not acceptable("watch", "failure")
    assert acceptable("pass", "mixed") and not acceptable("prototype", "mixed")


def test_reference_baseline(root: Path) -> None:
    cases, _ = load_cases(root / "eval" / "cases.yaml")
    ref = reference_baseline(cases)
    assert ref["lenient_accuracy"] == 1.0 and ref["score_mae_vs_reference"] == 0


def test_stability_metric() -> None:
    runs = [[{"id": "a", "verdict": v, "score": s}] for v, s in (("pass", 60), ("pass", 62), ("watch", 66))]
    st = stability(runs)
    assert st["per_case"][0]["agreement"] == round(2 / 3, 3) and st["fully_stable_cases"] == 0


def test_run_evaluation(root: Path, tmp_path: Path) -> None:
    m = run_evaluation(root, runs=2, noise=0.2, out_dir=tmp_path)
    assert m["cases"] == 21 and m["deterministic"]["identical_runs"] is True
    acc = m["deterministic"]["accuracy"]
    assert sum(sum(row.values()) for row in acc["confusion"].values()) == 21
    assert 0 <= acc["strict_accuracy"] <= acc["lenient_accuracy"] <= 1
    assert len(m["noisy"]["strict_accuracy_per_run"]) == 2
    assert (tmp_path / "report.md").read_text().startswith("# Evaluation report")
    assert (tmp_path / "metrics.json").exists()


def test_cli_smoke(capsys, monkeypatch, tmp_path: Path, root: Path) -> None:
    monkeypatch.setenv("SRA_ROOT", str(root))
    assert main(["graph"]) == 0
    assert "analyst" in capsys.readouterr().out
    assert main(["skills"]) == 0
    assert "scoring-rubric" in capsys.readouterr().out
    monkeypatch.setenv("SRA_LLM_PROVIDER", "nope")
    assert main(["run", "5335510"]) == 2
