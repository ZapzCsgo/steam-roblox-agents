---
name: report-template
version: "1.0"
description: Structure and wording of the per-candidate decision report.
agents: [reporter]
tasks: [report]
data:
  next_steps:
    prototype: Build a playable prototype within 7 days. Adapt the genre with original characters instead of copying assets.
    watch: Keep monitoring for two weeks. Look for an angle (Roblox-native twist, durable genre) before investing.
    pass: Do not invest. Re-check only if the Roblox signals change (new clone above 5 000 players).
---
Sections, in this order:

1. Header: game name, Steam app id, verdict, score, grid version, run id.
2. Decision summary: verdict, the main positive factors and the main blockers.
3. Factor table: factor, score, origin (rule, llm, critic-clamp), confidence, justification.
4. Score arithmetic: raw total, durable bonus, saturation malus, cap, timing decay.
5. Critic log: each review round with the rule ids that fired.
6. Roblox evidence: queries, clones, established leader.
7. Sources: every URL with its fetch time and whether it was live or a fixture.
8. Skills used, with their content hash.

Plain factual wording, no superlatives. Numbers carry their source.
