# steam-roblox-agents

[![ci](https://github.com/ZapzCsgo/steam-roblox-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/ZapzCsgo/steam-roblox-agents/actions/workflows/ci.yml)

A LangGraph pipeline that decides whether a trending Steam game is worth a Roblox version: prototype, watch or pass.
It is split into agents so that only two steps call an LLM (scoring six judgement factors and reviewing them), while data collection and the arithmetic stay plain, tested code.
On 21 historical cases, real models get the exact verdict 61.9% (Claude Sonnet 5.5) and 42.9% (GPT-6 Sol) of the time, against 33.3% for a rule that only looks at the Steam player peak. Try it offline with `make install demo`.

## Résumé en français

Pipeline multi-agents LangGraph qui dit si un jeu Steam en tendance mérite une version
Roblox : prototyper, surveiller ou passer. Un agent Collecte lit les API publiques Steam, un
agent Roblox cherche les clones, un agent Analyste note six facteurs qualitatifs avec un LLM
(sortie structurée Pydantic, justification, confiance), un agent Critique revérifie ces notes
et peut les renvoyer deux fois, puis du code calcule le score. Chaque décision garde ses
sources dans un journal JSONL.

Évaluation sur 21 cas historiques (2020 à 2026) avec de vrais modèles : Claude Sonnet 5.5
trouve le verdict exact dans 61,9 % des cas, GPT-6 Sol dans 42,9 %. Une baseline naïve qui ne
regarde que le pic de joueurs Steam fait 33,3 %. Les notes manuelles de l'étude d'origine,
rejouées dans le même pipeline, font 81,0 %, mais je les ai posées en connaissant le résultat.
Les réponses des modèles ont été enregistrées une fois via mes abonnements ChatGPT et Claude
(CLI Hermes, sans clé d'API payante), puis rejouées hors ligne : la CI recalcule ces chiffres à
chaque commit. Deux limites : 21 cas, c'est peu (un cas vaut 4,8 points, et l'intervalle de
confiance à 95 % autour de 61,9 % va de 41 % à 79 %), et ces cas ont servi à calibrer la
grille. Ce n'est donc pas une mesure à l'aveugle.

Lancer : `make install demo` (hors ligne), puis `make test` et `make eval-recorded`.

---

## What it does

Given a Steam app id, the pipeline collects Steam and Roblox data, scores ten factors and
returns a verdict with a Markdown decision record and a JSONL audit trail.

```mermaid
flowchart TD
    START([start]) --> collector
    collector["Collector<br/>Steam tools"] --> scout["Roblox Scout<br/>clone search tools"]
    scout --> rules["Rule factors<br/>F1 F5 F7 F9 (code)"]
    rules --> analyst["Analyst<br/>LLM, structured output<br/>F2 F3 F4 F6 F8 F10"]
    analyst --> critic{"Critic<br/>skill rules + optional LLM review"}
    critic -- "rejected, at most 2 revisions" --> analyst
    critic -- "approved, or clamped and flagged" --> scorer["Scorer<br/>grid v1.1 (code)"]
    scorer --> reporter["Reporter<br/>Markdown + journal"]
    reporter --> END([end])
```

`sra graph` prints the compiled graph.

| Node | Kind | Responsibility |
|---|---|---|
| `collector` | tool executor | Steam store item, tags, current players, SteamCharts history, review histogram |
| `roblox_scout` | tool executor | builds search queries from the `clone-search` skill, finds clones and established games |
| `rule_factors` | deterministic | F1 social play, F5 age rules, F7 momentum, F9 window, durable genre, saturation |
| `analyst` | LLM | scores F2, F3, F4, F6, F8, F10 with a justification and a confidence level |
| `critic` | rules + LLM | checks structure and skill-defined bounds, asks for a revision or clamps |
| `scorer` | deterministic | weights, bonus, malus, caps, timing decay, verdict |
| `reporter` | deterministic | decision record with factor table, critic log, sources, skill hashes |

## Why several agents

I split the pipeline by kind of work. Only the Analyst and the Critic call an LLM, so the part
that is not reproducible is small and easy to audit. Collection and scoring are code with unit
tests.

The Critic is a separate role with its own skills. Its rule layer catches answers that
contradict the evidence (for example "easy to clone" for a game tagged Open World) and sends
them back with the rule id. After two revisions it clamps the value and flags the candidate for
a human instead of looping. The Collector and the Scout expose plain typed tools and follow a
fixed plan for now.

Nodes exchange Pydantic models (`SteamSnapshot`, `RobloxSnapshot`, `FactorScore`,
`CriticVerdict`, `ScoreBreakdown`) inside a `TypedDict` state. The Critic history and the list
of skills used are append-only.

## Skills

`skills/` holds Markdown files with YAML front matter. Each skill says which agents and tasks
it applies to, and agents pick their skills at run time (`SkillRegistry.select("critic",
"review")`). The body goes into the prompt; the optional `data:` block is read by code.

| Skill | Used by | Content |
|---|---|---|
| `scoring-rubric` | Analyst, Critic | 0 to 5 rubric for the six qualitative factors, output contract |
| `roblox-platform-rules` | Analyst | audience and policy constraints, lessons from past cases |
| `consistency-rules` | Critic | machine-checkable bounds (C1 to C5), revision and clamp policy |
| `clone-search` | Roblox Scout | tag and description keyword maps, clone definitions |
| `report-template` | Reporter | report structure and next-step wording per verdict |

The journal records `name@version#hash` for every skill used, so a score can be tied to the
exact rubric that produced it. `sra skills` lists the library.

## Traceability

Every node appends events to `runs/<run_id>/journal.jsonl`. Data events carry their sources:

```json
{"node": "collector", "event": "steam_snapshot", "appid": 5335510,
 "data": {"name": "Moon Sweepers", "peak_ccu": 38917, "days_since_peak": 0, "...": "..."},
 "sources": [{"tool": "steam.current_players", "origin": "fixture", "fetched_at": "2026-11-08T13:43:11Z",
              "url": "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=5335510"}]}
{"node": "critic", "event": "revision_requested", "appid": 5335510,
 "data": {"attempt": 1, "rule_ids": ["C1"], "skills": ["consistency-rules@1.0#6accf3ae"],
          "issues": ["[C1] F3=4 exceeds 2: complex systems (open world, MMO, creature AI) cannot be cloned quickly (tags: Open World)"]}}
```

Analyst events also store the model name, a hash of the prompt and a hash of the raw answer.
Each factor in the final report shows its origin (`rule`, `llm` or `critic-clamp`) and its
confidence.

## Evaluation

`eval/cases.yaml` holds 21 Steam games that trended between 2020 and 2026 and what happened on
Roblox afterwards (success, mixed, failure), taken from a research note I wrote for the original
project. Expected verdicts: success -> prototype, mixed -> watch, failure -> pass.

Two caveats come before any number. The set is small: one case is 4.8 points of accuracy, and
the 95% interval around 61.9% (13 of 21) runs from 41% to 79%. I also used these same cases to
set the grid weights, bonus and malus (v1.0 -> v1.1), so this checks the pipeline against known
history. It does not measure how well it predicts.

Each case gives the pipeline the real Steam store data of the game (recorded once from the
Steam API) and the context at its Steam peak from the case file (peak players, price at the
time, whether Roblox already had a leader in the genre).

### Results with real models

The Analyst ran with two real models on all 21 cases. I recorded their answers once through my
ChatGPT Plus and Claude subscriptions, using the Hermes agent CLI as a bridge (no paid API
key). They are replayed offline by prompt hash, and CI fails if the replayed metrics differ from
the committed ones. Full report: [`eval/results/recorded/report.md`](eval/results/recorded/report.md).

| Approach (same 21 cases) | Strict accuracy | Lenient accuracy | Score MAE vs study |
|---|---|---|---|
| Naive baseline: Steam peak only (>= 150k prototype, >= 50k watch, else pass), no agents | 33.3% | 71.4% | |
| Same baseline, thresholds picked in hindsight on these cases | 61.9% | 61.9% | |
| Pipeline + GPT-6 Sol (via ChatGPT Plus) | 42.9% | 85.7% | 8.2 |
| Pipeline + Claude Sonnet 5.5 (via Claude subscription) | 61.9% | 85.7% | 7.1 |
| Pipeline + scripted Analyst (the study's own hand scores) | 81.0% | 95.2% | 5.1 |
| Study's hand scores straight through the grid | 85.7% | 100.0% | 0 |

Strict means verdict == expected. Lenient means no success was dropped, no failure was kept and
no mixed case was rushed to prototype.

What I take from it:

- Both models beat the fixed peak baseline, by 2 cases (GPT) and 6 cases (Claude). With 21
  cases the GPT gap is within noise. Claude only ties a peak rule whose thresholds I tuned on
  the answers, and that tuned rule never says pass, which is why its lenient score is low.
- The real models are far below the scripted run. That gap is the one that matters: the
  scripted run replays scores I assigned while knowing the outcome.
- GPT-6 Sol is conservative. On average it scores every judgement factor below the study, F4
  virality most (-0.95 out of 5). Five successes land at watch instead of prototype, and four
  of the five mixed cases fall to pass.
- Claude Sonnet 5.5 stays closer to the study on F4 and F6, and errs the other way on F8
  monetisation (+0.43).
- Some misses are shared by every approach, which points at the grid or the data. Vampire
  Survivors gets watch everywhere because Steam now lists local co-op, added after its 2022
  peak. Only Up! and Escape from Tarkov (mixed) fall to pass everywhere, the study's own scores
  included. Fall Guys and Helldivers 2 (failures) get watch from both models: from their tags
  and descriptions they look like good party or co-op games, and the context behind the study's
  lower scores is not in the prompt.
- The models know these games by name and may know how they did. That leak could only push
  their accuracy up, so it does not explain the shortfall, but it is one more reason not to
  read these numbers as a forecast.

### Real sampling variance

Phasmophobia, Liar's Bar and Fall Guys were recorded three times per model.

| Model | Verdict agreement | Mean score std | Notable |
|---|---|---|---|
| GPT-6 Sol | 88.9% (2 of 3 cases stable) | 2.1 | Phasmophobia: prototype once, watch twice (86, 79, 79) |
| Claude Sonnet 5.5 | 100% (3 of 3 stable) | 1.0 | |

A case near a threshold can change verdict between two runs of the same model, so another run
of GPT on the 21 cases could easily land one case higher or lower.

### The Critic's LLM review

The main runs use the Critic's rule layer only. It sent back 3 of 21 answers for each model
(C1 on Fall Guys and Only Up!, C5 on Escape from Tarkov), and every one passed on the second
try. I also ran the optional LLM review on a subset: 8 cases with GPT and 7 with Claude, where
the call budget ran out.

- The first real run found a bug. The Critic rejected Among Us three times in a row for citing
  the Roblox genre leader, because its prompt did not include the Roblox evidence the Analyst
  had seen. It now gets the same evidence block (commit "Show the Critic's LLM review the
  evidence the Analyst saw").
- After that fix, GPT-6 Sol approved every answer, so it added nothing on this sample.
- Claude Sonnet 5.5 as Critic rejected 6 of 7 cases, often over details the data does not state
  (developer name, session length). It fixed one verdict (Fall Guys to pass) and broke two
  (Among Us and Phasmophobia, prototype to watch), at 4.3 calls per case instead of 1.1.

On this sample the LLM review costs calls without improving verdicts. I would keep the rule
layer on and make the LLM review opt-in until it has been measured on more cases.

### Scripted harness

`make eval` runs the same graph with the scripted Analyst (the study's hand scores): five
noise-free runs to check determinism, then five runs where each score moves by +-1 with
probability 0.2, to see how the Critic and the verdicts react. Results are in
[`eval/results/report.md`](eval/results/report.md): identical outputs over 5 runs and 93.3%
verdict agreement under noise. This harness found a real bug early on. Lethal Company was capped
at 30 because its content notes mention "fictional medical drugs" and rule F5 matched any
occurrence of "drug". A regression test pins the fix.

### How recording and replay work

`scripts/record_llm.py` runs the full graph on each case with `HermesCLIChatModel`, which sends
the prompt to `hermes chat` on stdin, with a timeout, two attempts and 2 s between calls, and
checks that the answer contains a JSON object. Pydantic validation and one corrective retry
happen in the shared `invoke_structured`, as for any text-only model. `RecordingChatModel`
saves each exchange as prompt hash + raw answer in `eval/recordings/<model>/<case>.json`.
`RecordedChatModel` serves them back by hash, so a replay goes through the same Critic decisions
and revisions as the live run. If a prompt changes, the replay fails and the recordings need
refreshing.

The recordings hold 112 live calls. Eight more went to connection tests and to a first Among Us
run made before the Critic fix, which I discarded because its prompts no longer exist. The
script stops when a call budget is reached. Hermes puts its own short system prompt in front of
mine and does not expose the sampling temperature, so these runs use each provider's default.

## Running it

Requirements: Python 3.11 or later. The default mode needs no API key and no network.

```bash
make install        # .venv + package + dev tools
make demo           # offline: recorded Steam/Roblox responses + scripted model
make test           # pytest with coverage gate (80%)
make eval           # scripted harness, writes eval/results/
make eval-recorded  # replays the real GPT and Claude answers, writes eval/results/recorded/
make lint typecheck # ruff + mypy
```

The demo scores three games that were trending on 2026-11-08 (Moon Sweepers, ZED ZONE, Planet
Zoo 2) and writes one report per game plus the journal in `runs/<run_id>/`. Its Analyst answers
are hand-written stand-ins (`fixtures/llm/demo.json`); the first answer for Moon Sweepers
breaks rule C1 on purpose so the demo shows a Critic revision.

### Real mode

Set a provider and let the HTTP client go live. Example with the Gemini free tier:

```bash
pip install -e ".[google]"
export SRA_LLM_PROVIDER=google GOOGLE_API_KEY=...   # default model gemini-2.5-flash
export SRA_HTTP_MODE=live                           # or "record" to save fixtures as well
sra run 3527290 3241660
```

Other providers are `anthropic` (`ANTHROPIC_API_KEY`), `openai` (`OPENAI_API_KEY`), `ollama`
(local, no key, with a `docker compose` file) and `hermes` (`SRA_LLM_MODEL=<provider>/<model>`,
through a local Hermes CLI). `SRA_MAX_REVISIONS` sets the Critic budget and
`SRA_CRITIC_LLM=false` turns off the LLM review. API providers use native structured output
(`with_structured_output`); text-only models go through JSON parsing with one retry. See
`.env.example`.

To record new answers (needs a logged-in Hermes CLI):

```bash
python scripts/record_llm.py --provider anthropic --model claude-sonnet-5-5
python scripts/record_llm.py --provider anthropic --model claude-sonnet-5-5 --cases fall-guys --run 1
```

### Docker

```bash
docker build -t steam-roblox-agents .
docker run --rm steam-roblox-agents demo
docker run --rm steam-roblox-agents eval --recorded
```

## Repository layout

```
src/sra/
  graph.py            LangGraph wiring, run_candidate()
  state.py            typed state and Pydantic contracts
  agents/             collectors.py, rules.py, analyst.py, critic.py, output.py
  sources/            http.py (live/record/replay), steam.py, roblox.py
  grid.py features.py deterministic scoring grid and rule factors
  skills.py           skill registry
  llm.py              provider factory, scripted model, structured invocation
  hermes_cli.py       chat model that calls the Hermes CLI
  recording.py        record and replay of real model answers
  trace.py            JSONL journal
  evaluation.py       scripted harness and naive baseline
  recorded_eval.py    evaluation on recorded real answers
  cli.py              sra demo | run | record | eval | graph | skills
scripts/record_llm.py records real answers on the historical cases
skills/               Markdown skills loaded by the agents
fixtures/             recorded HTTP responses, scripted demo answers
eval/                 historical cases, recorded model answers, generated results
docs/grid.md          grid specification and calibration history
tests/                140 tests, 98% line coverage
```

CI runs ruff, mypy, the tests on Python 3.11 to 3.13, both evaluations (diffed against the
committed metrics), the demo, a Docker build and a gitleaks scan of the full history.

## Known limitations

- 21 cases, also used for calibration. A difference of one or two cases between approaches is
  noise.
- Each model was recorded once on the 21 cases, and the variance runs cover three cases only.
- The Critic's LLM review was measured on 7 and 8 cases, which is anecdotal.
- Steam metadata drifts (Vampire Survivors gained local co-op after its peak), and the case
  context (peak players, Roblox leader at the time) is reconstructed from public sources. Roblox
  peaks before 2022 are unknown.
- Roblox player counts are live snapshots from a public mirror, not historical peaks.
- Rule F5 relies on Steam descriptors and keywords and can over-penalise (Chained Together gets
  F5 = 2 from its gore descriptor).
- The Collector and the Scout follow a fixed plan and do not choose their tools.
- Recorded runs go through Hermes, which adds its own system prompt and hides the sampling
  temperature.

## What I would do next

- Score games before their Roblox outcome is known and check back three months later. Only
  that measures prediction.
- Fix the shared misses in the grid rather than in prompts: date the Steam metadata to the
  peak, and find a signal for the failures both models miss (Fall Guys, Helldivers 2).
- Make the Critic's LLM review opt-in, then measure it on all 21 cases with both models.
- Record five or more runs per case, so the accuracy figure carries the model's own variance
  and not only the small sample.
- Let the Scout pick its tools with a tool-calling model and compare it with the fixed plan.

## How AI was used

The scoring grid, the factor weights and the 21-case study come from my earlier project, a
scoring cron written as plain scripts. I built this repository the way I build my other
projects: with AI coding agents. I set the goal and the constraints (LangGraph, a Critic that
can send work back, no paid API, an evaluation that runs offline in CI, honest numbers even
when they are bad). The agents proposed the graph, wrote the code, the tests and the first
drafts of this README; tests, CI and the recorded evaluation are how I check their work.
The answers in `eval/recordings/` are unedited outputs of GPT-6 Sol and Claude Sonnet 5.5.

License: MIT.
