"""Command line: sra demo | run | record | eval | graph | skills."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from sra.config import Settings
from sra.graph import build_graph, make_deps, mermaid, new_run_id, run_candidate
from sra.llm import ConfigError
from sra.skills import SkillRegistry
from sra.sources.http import HttpClient
from sra.sources.roblox import RobloxTools
from sra.sources.steam import SteamTools

DEMO_APPIDS = (5335510, 1211600, 3219030)


def _run(settings: Settings, appids: list[int]) -> int:
    run_id = new_run_id()
    out_dir = settings.output_dir / run_id
    deps = make_deps(settings, run_id, journal_path=out_dir / "journal.jsonl", reports_dir=out_dir)
    graph = build_graph(deps)
    print(f"run {run_id}: provider={settings.llm_provider} http={settings.http_mode} -> {out_dir}")
    failures = 0
    for appid in appids:
        try:
            st = run_candidate(graph, deps, appid)
        except Exception as exc:  # one bad candidate must not stop the batch
            failures += 1
            deps.journal.log("graph", "error", appid=appid, data={"error": f"{type(exc).__name__}: {exc}"})
            print(f"  {appid}: ERROR {type(exc).__name__}: {exc}")
            continue
        sc = st["score"]
        rounds = len(st.get("critic_history", []))
        flag = " [human review]" if st.get("needs_human_review") else ""
        print(
            f"  {appid} {st['steam'].name[:28]:28} {sc.verdict:9} {sc.score:3}/100  "
            f"critic rounds={rounds} validated={st.get('validated')}{flag}"
        )
    print(f"journal: {out_dir / 'journal.jsonl'}")
    return 1 if failures == len(appids) else 0


def _record(settings: Settings, appids: list[int]) -> int:
    http = HttpClient(mode="record", fixtures_dir=settings.fixtures_dir / "http")
    skill = SkillRegistry.from_dir(settings.skills_dir).get("clone-search")
    for appid in appids:
        snap = SteamTools(http).snapshot(appid)
        rob = RobloxTools(http).snapshot(
            snap.name,
            snap.tags,
            snap.description,
            snap.release_ts,
            skill.data["tag_keywords"],
            skill.data["description_keywords"],
        )
        print(
            f"{appid} {snap.name}: {len(snap.sources) + len(rob.sources)} responses recorded ({len(rob.clones)} clones)"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sra", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="offline run on recorded fixtures with the scripted model")
    p_run = sub.add_parser("run", help="score Steam app ids (settings from SRA_* env vars)")
    p_run.add_argument("appids", type=int, nargs="+")
    p_rec = sub.add_parser("record", help="fetch live Steam/Roblox data and save it as fixtures")
    p_rec.add_argument("appids", type=int, nargs="+")
    p_eval = sub.add_parser("eval", help="evaluate on the historical cases")
    p_eval.add_argument("--runs", type=int, default=5)
    p_eval.add_argument("--noise", type=float, default=0.2)
    p_eval.add_argument("--out", type=Path, default=None)
    sub.add_parser("graph", help="print the Mermaid diagram of the graph")
    sub.add_parser("skills", help="list the skill library")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    try:
        if args.cmd == "demo":
            demo = dataclasses.replace(
                settings, llm_provider="fake", http_mode="replay", fake_script="fixtures/llm/demo.json"
            )
            return _run(demo, list(DEMO_APPIDS))
        if args.cmd == "run":
            return _run(settings, args.appids)
        if args.cmd == "record":
            return _record(settings, args.appids)
        if args.cmd == "eval":
            from sra.evaluation import run_evaluation

            run_evaluation(settings.root, runs=args.runs, noise=args.noise, out_dir=args.out)
            return 0
        if args.cmd == "graph":
            print(mermaid())
            return 0
        if args.cmd == "skills":
            print(json.dumps(SkillRegistry.from_dir(settings.skills_dir).catalog(), indent=1))
            return 0
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
