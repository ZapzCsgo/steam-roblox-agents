"""Record real LLM answers on the historical cases, for offline replay (`sra eval --recorded`).

The full LangGraph pipeline runs on each case, exactly as in the evaluation, with a live model
reached through the Hermes CLI (`hermes chat`, i.e. a chat subscription, no API key). Every
Analyst and Critic exchange is saved as prompt hash + raw answer in
eval/recordings/<model>/<case>.json. Resumable: a case whose run index is already recorded is
skipped. A global budget caps the number of live calls over all recordings.

Example (one case at a time, 2 s between calls):
    python scripts/record_llm.py --provider openai-codex --model gpt-6-sol
    python scripts/record_llm.py --provider anthropic --model claude-sonnet-5-5 \\
        --cases among-us,repo,only-up --run 1
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sra.evaluation import load_cases, run_with_model
from sra.hermes_cli import HermesCLIChatModel
from sra.recording import RecordingChatModel, calls_used, case_path, load_case, save_run

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, help="Hermes provider, e.g. openai-codex or anthropic")
    ap.add_argument("--model", required=True, help="model name for that provider")
    ap.add_argument("--cases", default="", help="comma-separated case ids (default: all 21)")
    ap.add_argument("--run", type=int, default=0, help="run index (0 = main run, 1.. = stability re-runs)")
    ap.add_argument("--budget", type=int, default=120, help="max live calls over all recordings")
    ap.add_argument("--reserve", type=int, default=4, help="calls kept in reserve before starting a case")
    ap.add_argument("--pause", type=float, default=2.0, help="seconds between calls")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per call")
    ap.add_argument("--critic-llm", action="store_true", help="also run the Critic's LLM review (more calls)")
    ap.add_argument("--out", type=Path, default=ROOT / "eval" / "recordings")
    args = ap.parse_args(argv)

    cases, study_date = load_cases(ROOT / "eval" / "cases.yaml")
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    unknown = wanted - {c.id for c in cases}
    if unknown:
        print(f"unknown case ids: {sorted(unknown)}", file=sys.stderr)
        return 2
    todo = [c for c in cases if not wanted or c.id in wanted]

    live = HermesCLIChatModel(provider=args.provider, model=args.model, timeout_s=args.timeout, pause_s=args.pause)
    recorder = RecordingChatModel(inner=live)
    for case in todo:
        label = f"{args.model}+critic-llm" if args.critic_llm else args.model
        path = case_path(args.out, label, case.id)
        if path.exists() and any(r["run"] == args.run for r in load_case(path)["runs"]):
            print(f"{case.id}: run {args.run} already recorded, skipped", flush=True)
            continue
        used = calls_used(args.out)
        if used + args.reserve > args.budget:
            print(f"budget reached ({used}/{args.budget} calls), stopping before {case.id}", flush=True)
            break
        start = time.monotonic()
        error, failed = None, 0
        try:
            row = run_with_model(ROOT, [case], study_date, recorder, f"record-{label}", args.critic_llm)[0]
            summary = f"{row['verdict']} {row['score']} (expected {case.expected}), rules {row['critic_rules']}"
        except Exception as exc:  # a failed case is a result too: keep what was exchanged
            error = f"{type(exc).__name__}: {exc}"[:500]
            failed = live.last_attempts if "CLICallError" in error else 0
            summary = f"ERROR {error}"
        save_run(
            path,
            model=args.model,
            provider=args.provider,
            critic_llm=args.critic_llm,
            case_id=case.id,
            appid=case.appid,
            exchanges=recorder.exchanges,
            error=error,
            failed_calls=failed,
            run=args.run,
        )
        print(
            f"{case.id} run {args.run}: {len(recorder.exchanges)} calls, {time.monotonic() - start:.0f}s -> {summary}",
            flush=True,
        )
    print(f"done, {calls_used(args.out)} live calls recorded in total", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
