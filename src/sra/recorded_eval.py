"""Evaluation on recorded real-model answers (offline, deterministic).

The answers in eval/recordings/<model>/ were produced once by real LLMs (scripts/record_llm.py)
and are replayed here by prompt hash, so the full graph (Analyst, Critic rules, Critic LLM
review, revisions, scorer) runs exactly as it did live. Measured per model:
- verdict accuracy, confusion matrix, score gap vs the reference score;
- per-factor gap between the model's scores and the study's hand-assigned scores;
- Critic statistics (first-pass approval, validation, revisions, LLM rejections);
- real sampling stability: a few cases recorded several times, verdict agreement and score spread;
- replay determinism: two replays must give identical rows.
The scripted model (hand-assigned scores) is the comparison baseline.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from sra import grid
from sra.evaluation import (
    Case,
    accuracy,
    comparable,
    critic_stats,
    load_cases,
    naive_baseline,
    run_once,
    run_with_model,
    stability,
)
from sra.recording import load_model_dir, replay_model

ERROR = "error"


def replay_rows(
    root: Path, cases: list[Case], study_date: str, recs: dict[str, dict[str, Any]], run: int, label: str
) -> list[dict[str, Any]]:
    """Replay run `run` of every recorded case. Cases whose live run failed give an 'error' row."""
    present = [c for c in cases if any(r["run"] == run for r in recs.get(c.id, {}).get("runs", []))]
    failed = {
        c.id: next(r for r in recs[c.id]["runs"] if r["run"] == run)["error"]
        for c in present
        if next(r for r in recs[c.id]["runs"] if r["run"] == run).get("error")
    }
    ok = [c for c in present if c.id not in failed]
    critic_llm = bool(next(iter(recs.values()), {}).get("critic_llm", False))
    llm = replay_model(recs, run, label)
    rows = {r["id"]: r for r in run_with_model(root, ok, study_date, llm, label, critic_llm)}
    out = []
    for c in present:
        if c.id in rows:
            out.append(rows[c.id])
            continue
        out.append(
            {
                "id": c.id,
                "name": c.name,
                "outcome": c.outcome,
                "expected": c.expected,
                "verdict": ERROR,
                "score": 0,
                "ref_score": c.ref_score,
                "factors": {},
                "first_pass_approved": False,
                "revisions": 0,
                "validated": False,
                "clamped": False,
                "human_review": True,
                "critic_rules": [],
                "error": failed[c.id],
            }
        )
    return out


def factor_gaps(rows: list[dict[str, Any]], cases: list[Case]) -> dict[str, dict[str, float]]:
    """Per LLM factor: mean signed gap and mean absolute gap vs the study's hand-assigned score."""
    ref = {c.id: c.reference for c in cases}
    out = {}
    for f in grid.LLM_FACTORS:
        gaps = [r["factors"][f] - ref[r["id"]][f] for r in rows if f in r.get("factors", {})]
        if gaps:
            out[f] = {"bias": round(statistics.mean(gaps), 2), "mae": round(statistics.mean(abs(g) for g in gaps), 2)}
    return out


def usage(recs: dict[str, dict[str, Any]], run: int = 0) -> dict[str, Any]:
    runs = [r for data in recs.values() for r in data["runs"] if r["run"] == run]
    exchanges = [ex for r in runs for ex in r["exchanges"]]
    by_role: dict[str, int] = {}
    for ex in exchanges:
        by_role[ex["role"]] = by_role.get(ex["role"], 0) + 1
    all_runs = [r for data in recs.values() for r in data["runs"]]
    return {
        "cases": len(runs),
        "calls": len(exchanges),
        "calls_per_case": round(len(exchanges) / len(runs), 2) if runs else 0,
        "calls_by_role": dict(sorted(by_role.items())),
        "transport_retries": sum(int(ex.get("attempts", 1)) - 1 for ex in exchanges),
        "failed_runs": sum(bool(r.get("error")) for r in runs),
        "median_seconds_per_call": round(statistics.median(ex.get("seconds", 0.0) for ex in exchanges), 1)
        if exchanges
        else 0,
        "total_live_calls_all_runs": sum(
            sum(int(ex.get("attempts", 1)) for ex in r["exchanges"]) + int(r.get("failed_calls", 0)) for r in all_runs
        ),
        "recorded_between": [min(r["recorded_at"] for r in all_runs), max(r["recorded_at"] for r in all_runs)]
        if all_runs
        else [],
    }


def evaluate_model(root: Path, cases: list[Case], study_date: str, model_dir: Path) -> dict[str, Any]:
    recs = load_model_dir(model_dir)
    first = next(iter(recs.values()), {})
    label = str(first.get("model", model_dir.name)) + (" + LLM critic" if first.get("critic_llm") else "")
    rows = replay_rows(root, cases, study_date, recs, 0, label)
    again = replay_rows(root, cases, study_date, recs, 0, label)
    stable_ids = sorted(cid for cid, d in recs.items() if len({r["run"] for r in d["runs"]}) >= 3)
    stab: dict[str, Any] | None = None
    if stable_ids:
        n_runs = min(len({r["run"] for r in recs[cid]["runs"]}) for cid in stable_ids)
        sub = [c for c in cases if c.id in stable_ids]
        sub_recs = {cid: recs[cid] for cid in stable_ids}
        per_run = [replay_rows(root, sub, study_date, sub_recs, k, label) for k in range(n_runs)]
        stab = stability(per_run)
        stab["strict_accuracy_per_run"] = [accuracy(r)["strict_accuracy"] for r in per_run]
    scored = [r for r in rows if r["verdict"] != ERROR]
    return {
        "model": label,
        "provider": first.get("provider"),
        "critic_llm": bool(first.get("critic_llm", False)),
        "missing_cases": sorted(c.id for c in cases if c.id not in recs),
        "rows": rows,
        "accuracy": accuracy(rows),
        "critic": critic_stats(rows)
        | {"llm_rejection_rate": round(sum("LLM" in r["critic_rules"] for r in rows) / len(rows), 3)},
        "factor_gaps": factor_gaps(scored, cases),
        "usage": usage(recs),
        "replay_identical": comparable(rows) == comparable(again),
        "stability": stab,
    }


def run_recorded_evaluation(root: Path, rec_dir: Path | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    cases, study_date = load_cases(root / "eval" / "cases.yaml")
    rec_dir = rec_dir or root / "eval" / "recordings"
    scripted = run_once(root, cases, study_date, 0.0, 0)
    models = [evaluate_model(root, cases, study_date, d) for d in sorted(p for p in rec_dir.iterdir() if p.is_dir())]
    metrics = {
        "grid_version": grid.GRID_VERSION,
        "cases": len(cases),
        "scripted": {
            "model": "scripted (hand-assigned reference scores)",
            "rows": scripted,
            "accuracy": accuracy(scripted),
            "critic": critic_stats(scripted)
            | {"llm_rejection_rate": round(sum("LLM" in r["critic_rules"] for r in scripted) / len(scripted), 3)},
        },
        "naive_baseline": naive_baseline(cases),
        "models": models,
    }
    out = out_dir or root / "eval" / "results" / "recorded"
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    report = build_recorded_report(metrics)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return metrics


def _pct(x: float) -> str:
    return f"{x:.1%}"


def build_recorded_report(m: dict[str, Any]) -> str:
    sc, models = m["scripted"], m["models"]
    cols = [sc, *models]
    names = ["scripted"] + [x["model"] for x in models]
    head = "| Metric | " + " | ".join(names) + " |"
    sep = "|---|" + "---|" * len(names)

    def line(label: str, fn: Any) -> str:
        return f"| {label} | " + " | ".join(fn(c) for c in cols) + " |"

    lines = [
        "# Evaluation on recorded real-model answers",
        "",
        f"Generated by `sra eval --recorded` on {m['cases']} historical cases, grid v{m['grid_version']}.",
        "Each model's answers were recorded once through a chat subscription (Hermes CLI) and are",
        "replayed offline by prompt hash. The scripted column replays the study's hand-assigned scores.",
        "The cases were used to calibrate the grid: this is not a blind test.",
        "",
        "## Summary",
        "",
        f"Naive baseline (verdict from the Steam peak alone: prototype if peak >= "
        f"{m['naive_baseline']['thresholds']['prototype_min_peak']:,}, watch if >= "
        f"{m['naive_baseline']['thresholds']['watch_min_peak']:,}, else pass): strict accuracy "
        f"**{_pct(m['naive_baseline']['strict_accuracy'])}**, lenient "
        f"{_pct(m['naive_baseline']['lenient_accuracy'])}. Best thresholds chosen in hindsight on these "
        f"same cases: {_pct(m['naive_baseline']['hindsight_best']['strict_accuracy'])} strict (overfit bound).",
        "",
        head,
        sep,
        line("Cases scored", lambda c: str(c["accuracy"]["n"])),
        line("Strict accuracy (verdict == expected)", lambda c: _pct(c["accuracy"]["strict_accuracy"])),
        line("Lenient accuracy", lambda c: _pct(c["accuracy"]["lenient_accuracy"])),
        line("Score MAE vs reference score", lambda c: str(c["accuracy"]["score_mae_vs_reference"])),
        line("Score bias vs reference score", lambda c: f"{c['accuracy']['score_bias_vs_reference']:+}"),
        line("Critic first-pass approval", lambda c: _pct(c["critic"]["first_pass_approval"])),
        line("Critic final validation", lambda c: _pct(c["critic"]["final_validation"])),
        line("Mean revisions (send-backs) per case", lambda c: str(c["critic"]["mean_revisions"])),
        line("Cases rejected at least once by the LLM Critic", lambda c: _pct(c["critic"]["llm_rejection_rate"])),
        line("Clamp rate", lambda c: _pct(c["critic"]["clamp_rate"])),
        line("Human-review rate", lambda c: _pct(c["critic"]["human_review_rate"])),
        line("Model calls per case", lambda c: str(c["usage"]["calls_per_case"]) if "usage" in c else "-"),
        line("Replay identical twice", lambda c: ("yes" if c["replay_identical"] else "no") if "usage" in c else "-"),
        "",
    ]
    for x in models:
        acc = x["accuracy"]
        verdicts = list(grid.VERDICTS) + (["error"] if any("error" in row for row in acc["confusion"].values()) else [])
        lines += [
            f"## {x['model']}",
            "",
            f"Recorded {x['usage']['recorded_between'][0]} to {x['usage']['recorded_between'][-1]} "
            f"via provider `{x['provider']}`; {x['usage']['calls']} calls for the main run "
            f"({x['usage']['calls_by_role']}), {x['usage']['transport_retries']} transport retries, "
            f"{x['usage']['failed_runs']} failed runs, median {x['usage']['median_seconds_per_call']} s per call.",
            "",
            "Confusion matrix (rows = expected, columns = predicted):",
            "",
            "| expected \\\\ predicted | " + " | ".join(verdicts) + " |",
            "|---|" + "---|" * len(verdicts),
            *[
                f"| {e} | " + " | ".join(str(acc["confusion"][e].get(p, 0)) for p in verdicts) + " |"
                for e in grid.VERDICTS
            ],
            "",
            "Factor gap vs the study's hand-assigned scores (final scores, after the Critic):",
            "",
            "| Factor | " + " | ".join(x["factor_gaps"]) + " |",
            "|---|" + "---|" * len(x["factor_gaps"]),
            "| bias | " + " | ".join(f"{g['bias']:+}" for g in x["factor_gaps"].values()) + " |",
            "| MAE | " + " | ".join(str(g["mae"]) for g in x["factor_gaps"].values()) + " |",
            "",
        ]
        st = x["stability"]
        if st:
            lines += [
                f"Real sampling stability ({len(st['per_case'])} cases recorded {st['runs']} times): "
                f"mean verdict agreement {_pct(st['mean_verdict_agreement'])}, "
                f"{st['fully_stable_cases']}/{len(st['per_case'])} fully stable, mean score std "
                f"{st['mean_score_std']} (max {st['max_score_std']}).",
                "",
                "| Case | Verdicts | Score mean | Score std |",
                "|---|---|---|---|",
                *[
                    f"| {c['id']} | {', '.join(f'{k} x{v}' for k, v in sorted(c['verdicts'].items()))} | "
                    f"{c['score_mean']} | {c['score_std']} |"
                    for c in st["per_case"]
                ],
                "",
            ]
    lines += [
        "## Per case (main run)",
        "",
        "| Case | Outcome | Expected | Ref. score | naive (peak) | " + " | ".join(names) + " |",
        "|---|---|---|---|---|" + "---|" * len(names),
    ]
    by_model = [{r["id"]: r for r in c["rows"]} for c in cols]
    for r in sc["rows"]:
        cells = []
        for rows in by_model:
            row = rows.get(r["id"])
            if row is None:
                cells.append("-")
                continue
            miss = "" if row["verdict"] == row["expected"] else " (miss)"
            rules = f" [{', '.join(row['critic_rules'])}]" if row["critic_rules"] else ""
            cells.append(f"{row['verdict']} {row['score']}{miss}{rules}")
        naive = m["naive_baseline"]["verdicts"][r["id"]]
        naive += "" if naive == r["expected"] else " (miss)"
        lines.append(
            f"| {r['name']} | {r['outcome']} | {r['expected']} | {r['ref_score']} | {naive} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    return "\n".join(lines)
