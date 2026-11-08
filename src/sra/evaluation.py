"""Offline evaluation on the 21 historical cases, with the scripted model.

What is measured (offline, recorded Steam store data):
- verdict accuracy (strict and lenient) and confusion matrix vs. the expected verdicts;
- score gap vs. the reference backtest score;
- determinism: N noise-free runs must produce identical outputs;
- stability under simulated sampling noise (each Analyst score moves +-1 with probability p);
- Critic statistics: first-pass approval, revisions, clamps, human-review flags.

The scripted Analyst answers are the hand-assigned reference scores from the study.
The rule-based factors (F1, F5, F7, F9) are recomputed by the pipeline from real Steam
store data and the reconstructed history, so the pipeline score can differ from the
reference score. The same harness replays real-model answers in recorded_eval.py, and
naive_baseline() gives a peak-only reference point that uses no agent at all.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from sra import grid
from sra.config import Settings
from sra.deps import Deps
from sra.graph import build_graph, run_candidate
from sra.llm import ScriptedChatModel, TextChatModel
from sra.skills import SkillRegistry
from sra.sources.http import FixtureMissingError, HttpClient
from sra.sources.steam import SteamTools, snapshot_from_item
from sra.state import RobloxGame, RobloxSnapshot, SourceRef, SteamSnapshot
from sra.trace import Journal

VERDICTS = grid.VERDICTS


@dataclass
class Case:
    id: str
    name: str
    appid: int
    outcome: str
    expected: str
    reference: dict[str, int]
    ref_score: int
    history: dict[str, Any]
    tags: list[str] = field(default_factory=list)


def load_cases(path: Path) -> tuple[list[Case], str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for c in raw["cases"]:
        ref = dict(zip(grid.FACTORS, c["reference"], strict=True))
        cases.append(
            Case(
                id=c["id"],
                name=c["name"],
                appid=int(c["appid"]),
                outcome=c["outcome"],
                expected=c["expected"],
                reference=ref,
                ref_score=int(c["ref_score"]),
                history=c["history"],
                tags=list(c.get("tags", [])),
            )
        )
    return cases, str(raw["study_date"])


def acceptable(verdict: str, outcome: str) -> bool:
    """Lenient check: a success must not be dropped, a failure must be dropped, a mixed case not rushed."""
    if outcome == "success":
        return verdict != "pass"
    if outcome == "failure":
        return verdict == "pass"
    return verdict != "prototype"


def scripted_answers(cases: list[Case]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for c in cases:
        out[f"analyst:{c.appid}"] = [
            {
                "assessments": [
                    {
                        "factor": f,
                        "score": c.reference[f],
                        "confidence": "medium",
                        "justification": f"{c.name}: {grid.NAMES[f]} scored {c.reference[f]} in the historical study "
                        "(scripted answer, not model output).",
                    }
                    for f in grid.LLM_FACTORS
                ]
            }
        ]
    return out


def case_preset(case: Case, http: HttpClient, study_date: str) -> dict[str, Any]:
    """Steam snapshot from the recorded store item + reconstructed history; Roblox context from the case."""
    case_src = SourceRef(tool="case-file", url=f"eval/cases.yaml#{case.id}", fetched_at=study_date, origin="case-file")
    tools = SteamTools(http)
    try:
        names, tag_src = tools.tag_names()
        item_r = tools.store_item(case.appid)
        items = [i for i in item_r.data.get("response", {}).get("store_items", []) if i.get("success") == 1]
    except FixtureMissingError:
        items = []
    if items and items[0].get("tagids"):
        steam = snapshot_from_item(items[0], names)
        sources = [tag_src, item_r.source, case_src]
    else:
        steam = SteamSnapshot(appid=case.appid, name=case.name, tags=case.tags)
        sources = [case_src]
    steam = steam.model_copy(
        update={
            "name": case.name,
            "peak_ccu": case.history.get("peak_ccu"),
            "days_since_peak": 0,
            "is_free": not case.history.get("paid", True),
            "sources": sources,
        }
    )
    leader_players = int(case.history.get("roblox_leader_players", 0))
    leader = (
        RobloxGame(name=case.history.get("roblox_leader_name", "established genre leader"), players=leader_players)
        if leader_players
        else None
    )
    roblox = RobloxSnapshot(queries=[], leader=leader, sources=[case_src])
    return {"steam": steam, "roblox": roblox}


def run_with_model(
    root: Path, cases: list[Case], study_date: str, llm: TextChatModel, label: str, critic_llm: bool = True
) -> list[dict[str, Any]]:
    """Run the full graph on each case with the given model and collect one row per case."""
    settings = replace(Settings(root=root), llm_provider="fake", http_mode="replay", critic_llm=critic_llm)
    deps = Deps(
        settings=settings,
        http=HttpClient(mode="replay", fixtures_dir=root / "fixtures" / "http"),
        llm=llm,
        skills=SkillRegistry.from_dir(root / "skills"),
        journal=Journal(None, f"eval-{label}"),
    )
    graph = build_graph(deps)
    rows = []
    for c in cases:
        st = run_candidate(graph, deps, c.appid, case_preset(c, deps.http, study_date))
        hist = st.get("critic_history", [])
        rows.append(
            {
                "id": c.id,
                "name": c.name,
                "outcome": c.outcome,
                "expected": c.expected,
                "verdict": st["score"].verdict,
                "score": st["score"].score,
                "ref_score": c.ref_score,
                "factors": st["score"].factors,
                "first_pass_approved": bool(hist and hist[0].approved),
                "revisions": max(0, len(hist) - 1),
                "validated": bool(st.get("validated")),
                "clamped": any(f.origin == "critic-clamp" for f in st["llm_factors"].values()),
                "human_review": bool(st.get("needs_human_review")),
                "critic_rules": sorted({r for v in hist for r in v.rule_ids}),
            }
        )
    return rows


def run_once(root: Path, cases: list[Case], study_date: str, noise: float, seed: int) -> list[dict[str, Any]]:
    llm = ScriptedChatModel(scripts=scripted_answers(cases), noise=noise, seed=seed)
    return run_with_model(root, cases, study_date, llm, f"seed{seed}")


def critic_stats(rows: list[dict[str, Any]]) -> dict[str, float]:
    n = len(rows)
    return {
        "first_pass_approval": round(sum(r["first_pass_approved"] for r in rows) / n, 3),
        "final_validation": round(sum(r["validated"] for r in rows) / n, 3),
        "mean_revisions": round(sum(r["revisions"] for r in rows) / n, 3),
        "clamp_rate": round(sum(r["clamped"] for r in rows) / n, 3),
        "human_review_rate": round(sum(r["human_review"] for r in rows) / n, 3),
    }


def accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    matrix = {e: dict.fromkeys(VERDICTS, 0) for e in VERDICTS}
    for r in rows:
        matrix[r["expected"]][r["verdict"]] = matrix[r["expected"]].get(r["verdict"], 0) + 1
    gaps = [r["score"] - r["ref_score"] for r in rows]
    return {
        "n": n,
        "strict_accuracy": round(sum(r["verdict"] == r["expected"] for r in rows) / n, 3),
        "lenient_accuracy": round(sum(acceptable(r["verdict"], r["outcome"]) for r in rows) / n, 3),
        "confusion": matrix,
        "score_mae_vs_reference": round(sum(abs(g) for g in gaps) / n, 2),
        "score_bias_vs_reference": round(sum(gaps) / n, 2),
    }


def reference_baseline(cases: list[Case]) -> dict[str, Any]:
    """Accuracy of the study's own reference scores through the same grid (upper reference)."""
    rows = [
        {
            "verdict": grid.verdict_for(c.ref_score),
            "expected": c.expected,
            "outcome": c.outcome,
            "score": c.ref_score,
            "ref_score": c.ref_score,
        }
        for c in cases
    ]
    return accuracy(rows)


# Naive baseline: verdict from the Steam peak alone (no agents, no grid). Thresholds fixed a
# priori as round numbers, not tuned on the cases.
NAIVE_THRESHOLDS = (150_000, 50_000)


def naive_verdict(peak_ccu: int | None, thresholds: tuple[int, int] = NAIVE_THRESHOLDS) -> str:
    hi, lo = thresholds
    peak = peak_ccu or 0
    return "prototype" if peak >= hi else "watch" if peak >= lo else "pass"


def naive_rows(cases: list[Case], thresholds: tuple[int, int] = NAIVE_THRESHOLDS) -> list[dict[str, Any]]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "verdict": naive_verdict(c.history.get("peak_ccu"), thresholds),
            "expected": c.expected,
            "outcome": c.outcome,
            "score": 0,
            "ref_score": c.ref_score,
        }
        for c in cases
    ]


def naive_baseline(cases: list[Case]) -> dict[str, Any]:
    """Peak-only baseline with fixed thresholds, plus the best thresholds found in hindsight (overfit bound)."""
    fixed = accuracy(naive_rows(cases))
    peaks = sorted({int(c.history.get("peak_ccu") or 0) for c in cases} | {0})
    best: tuple[float, float, int, int] = (-1.0, -1.0, 0, 0)
    for lo in peaks:
        for hi in peaks:
            if hi <= lo:
                continue
            a = accuracy(naive_rows(cases, (hi, lo)))
            key = (a["strict_accuracy"], a["lenient_accuracy"], hi, lo)
            if key[:2] > best[:2]:
                best = key
    return {
        "thresholds": {"prototype_min_peak": NAIVE_THRESHOLDS[0], "watch_min_peak": NAIVE_THRESHOLDS[1]},
        "strict_accuracy": fixed["strict_accuracy"],
        "lenient_accuracy": fixed["lenient_accuracy"],
        "confusion": fixed["confusion"],
        "verdicts": {r["id"]: r["verdict"] for r in naive_rows(cases)},
        "hindsight_best": {
            "prototype_min_peak": best[2],
            "watch_min_peak": best[3],
            "strict_accuracy": best[0],
            "lenient_accuracy": best[1],
        },
    }


def stability(runs: list[list[dict[str, Any]]]) -> dict[str, Any]:
    per_case = []
    for i, base in enumerate(runs[0]):
        verdicts = [run[i]["verdict"] for run in runs]
        scores = [run[i]["score"] for run in runs]
        modal, count = Counter(verdicts).most_common(1)[0]
        per_case.append(
            {
                "id": base["id"],
                "modal_verdict": modal,
                "agreement": round(count / len(runs), 3),
                "score_mean": round(statistics.mean(scores), 1),
                "score_std": round(statistics.pstdev(scores), 2),
                "verdicts": dict(Counter(verdicts)),
            }
        )
    return {
        "runs": len(runs),
        "mean_verdict_agreement": round(statistics.mean(c["agreement"] for c in per_case), 3),
        "fully_stable_cases": sum(c["agreement"] == 1.0 for c in per_case),
        "mean_score_std": round(statistics.mean(c["score_std"] for c in per_case), 2),
        "max_score_std": max(c["score_std"] for c in per_case),
        "per_case": per_case,
    }


def comparable(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, sort_keys=True)


def build_report(m: dict[str, Any]) -> str:
    det, noisy, ref = m["deterministic"], m["noisy"], m["reference_baseline"]
    acc = det["accuracy"]
    lines = [
        "# Evaluation report",
        "",
        f"Generated by `sra eval` on {m['cases']} historical cases, grid v{m['grid_version']}, "
        f"{m['runs']} runs per setting, noise p={m['noise']}.",
        "",
        "These cases were used to calibrate the grid. The numbers below check that the pipeline",
        "reproduces the study and behaves predictably; they are not a blind measure of predictive power.",
        "",
        "## Verdict accuracy (noise-free run)",
        "",
        "| Metric | Pipeline | Reference scores through the grid |",
        "|---|---|---|",
        f"| Strict accuracy (verdict == expected) | {acc['strict_accuracy']:.1%} | {ref['strict_accuracy']:.1%} |",
        f"| Lenient accuracy (no success dropped, no failure kept) | {acc['lenient_accuracy']:.1%} | "
        f"{ref['lenient_accuracy']:.1%} |",
        f"| Score MAE vs reference score | {acc['score_mae_vs_reference']} | 0 |",
        f"| Score bias vs reference score | {acc['score_bias_vs_reference']:+} | 0 |",
        "",
        "Confusion matrix (rows = expected, columns = predicted):",
        "",
        "| expected \\ predicted | prototype | watch | pass |",
        "|---|---|---|---|",
        *[f"| {e} | " + " | ".join(str(acc["confusion"][e][p]) for p in VERDICTS) + " |" for e in VERDICTS],
        "",
        "## Reproducibility",
        "",
        f"- Noise-free: {m['runs']} runs, outputs identical: **{'yes' if det['identical_runs'] else 'no'}**.",
        f"- With simulated sampling noise (each Analyst score +-1 with p={m['noise']}, seeds 0..{m['runs'] - 1}): "
        f"mean verdict agreement **{noisy['stability']['mean_verdict_agreement']:.1%}**, "
        f"{noisy['stability']['fully_stable_cases']}/{m['cases']} cases fully stable, "
        f"mean score std {noisy['stability']['mean_score_std']} (max {noisy['stability']['max_score_std']}).",
        f"- Strict accuracy under noise, per run: {', '.join(f'{a:.1%}' for a in noisy['strict_accuracy_per_run'])}.",
        "",
        "## Critic",
        "",
        "| Setting | First-pass approval | Final validation | Mean revisions | Clamp rate | Human review |",
        "|---|---|---|---|---|---|",
        *[
            f"| {name} | {s['first_pass_approval']:.1%} | {s['final_validation']:.1%} | {s['mean_revisions']} | "
            f"{s['clamp_rate']:.1%} | {s['human_review_rate']:.1%} |"
            for name, s in (("noise-free", det["critic"]), (f"noise p={m['noise']}", noisy["critic"]))
        ],
        "",
        "## Per case (noise-free run)",
        "",
        "| Case | Outcome | Expected | Verdict | Score | Ref. score | Critic rules fired | Stability under noise |",
        "|---|---|---|---|---|---|---|---|",
    ]
    stab = {c["id"]: c for c in noisy["stability"]["per_case"]}
    for r in det["rows"]:
        s = stab[r["id"]]
        mark = "" if r["verdict"] == r["expected"] else " (miss)"
        lines.append(
            f"| {r['name']} | {r['outcome']} | {r['expected']} | {r['verdict']}{mark} | {r['score']} | "
            f"{r['ref_score']} | {', '.join(r['critic_rules']) or '-'} | {s['agreement']:.0%} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_evaluation(root: Path, runs: int = 5, noise: float = 0.2, out_dir: Path | None = None) -> dict[str, Any]:
    cases, study_date = load_cases(root / "eval" / "cases.yaml")
    det_runs = [run_once(root, cases, study_date, 0.0, seed) for seed in range(runs)]
    noisy_runs = [run_once(root, cases, study_date, noise, seed) for seed in range(runs)]
    metrics = {
        "grid_version": grid.GRID_VERSION,
        "cases": len(cases),
        "runs": runs,
        "noise": noise,
        "reference_baseline": reference_baseline(cases),
        "deterministic": {
            "rows": det_runs[0],
            "accuracy": accuracy(det_runs[0]),
            "critic": critic_stats(det_runs[0]),
            "identical_runs": len({comparable(r) for r in det_runs}) == 1,
        },
        "noisy": {
            "stability": stability(noisy_runs),
            "strict_accuracy_per_run": [accuracy(r)["strict_accuracy"] for r in noisy_runs],
            "critic": critic_stats([row for run in noisy_runs for row in run]),
        },
    }
    out = out_dir or root / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    report = build_report(metrics)
    (out / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return metrics


def record_case_fixtures(root: Path) -> None:
    """Fetch the Steam store items of every case (live, public endpoint) and save them as fixtures."""
    cases, _ = load_cases(root / "eval" / "cases.yaml")
    tools = SteamTools(HttpClient(mode="record", fixtures_dir=root / "fixtures" / "http"))
    tools.tag_names()
    for c in cases:
        r = tools.store_item(c.appid)
        ok = [i for i in r.data.get("response", {}).get("store_items", []) if i.get("success") == 1]
        print(f"{c.appid} {c.name}: {'ok' if ok and ok[0].get('tagids') else 'no store data'}")


if __name__ == "__main__":  # pragma: no cover
    import sys

    repo = Path(__file__).resolve().parents[2]
    if "--record" in sys.argv:
        record_case_fixtures(repo)
    else:
        run_evaluation(repo)
