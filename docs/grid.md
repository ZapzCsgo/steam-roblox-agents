# Scoring grid v1.1

Ten factors scored 0 to 5, weighted to a 0 to 100 score.

| Factor | Weight | Scored by |
|---|---|---|
| F1 co-op / social play | 15 | rules (Steam categories and tags) |
| F2 native Roblox genre | 15 | Analyst (LLM) |
| F3 ease of cloning | 12 | Analyst (LLM) |
| F4 video virality | 12 | Analyst (LLM) |
| F5 age 9-16 / Roblox rules | 10 | rules (descriptors, tags, content notes) |
| F6 short sessions | 8 | Analyst (LLM) |
| F7 Steam momentum + price | 5 | rules (peak CCU, growth, price) |
| F8 natural monetisation | 8 | Analyst (LLM) |
| F9 window / competition | 10 | rules (days since peak, Roblox clones) |
| F10 IP risk (5 = low) | 5 | Analyst (LLM) |

Adjustments, in order:

1. `+6` if the genre is durable on Roblox (tycoon, co-op horror, co-op survival) and F2 >= 4 and F8 >= 4.
2. `-10` if a Roblox game on the concept already has 20 000 players or more.
3. Caps: F5 = 0 caps at 30, F3 = 0 at 35, F1 = 0 at 40 (lowest wins).
4. Timing decay: x0.85 per 30 days since the Steam peak if F2 <= 2, x0.925 if F2 = 3, none if F2 >= 4.

Verdicts: >= 80 prototype, 65 to 79 watch, < 65 pass.

## Calibration

v1.0 was derived from 21 historical cases (2020 to 2026). Its backtest showed two errors:
Fall Guys scored too high (genre already saturated on Roblox) and Supermarket Simulator too
low (durable tycoon genre). v1.1 moved weight from F7 to F9 and added the saturation malus and
the durable-genre bonus. Because the same 21 cases drove both versions, the evaluation in
`eval/` measures consistency, not predictive power.
