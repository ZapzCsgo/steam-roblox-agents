---
name: scoring-rubric
version: "1.1"
description: Rubric for the six qualitative factors of the Steam to Roblox grid (F2, F3, F4, F6, F8, F10).
agents: [analyst, critic]
tasks: [score-factors, review]
---
You score one Steam game for its potential as a Roblox adaptation. Each factor is an
integer from 0 to 5. Use only the evidence given (Steam tags, description, player
numbers, Roblox search results). When evidence is thin, say so and lower the confidence
instead of guessing.

- **F2 Native Roblox genre.** 0 = narrative, strategy or niche simulation. 2 = shooter,
  MMO, complex management. 4 = co-op horror, tycoon, survival, social deduction.
  5 = obby, hide-and-seek, objective-based co-op horror, already in the current Roblox top charts.
- **F3 Ease of cloning.** 0 = open world, creature AI, vehicle physics, large content (Palworld).
  3 = several systems but an MVP fits in 2 to 4 weeks. 5 = one core mechanic that can be
  rebuilt in a few days.
- **F4 Video virality.** 0 = nothing clippable. 3 = streamed but no stand-out moments.
  5 = every round produces a funny or scary clip and "X in Roblox" videos already exist.
- **F6 Short sessions.** 1 = sessions of 60 minutes or more. 3 = 20 to 40 minutes.
  5 = rounds of 3 to 15 minutes.
- **F8 Natural monetisation.** 1 = nothing to sell without breaking the game. 3 = cosmetics.
  5 = revives, classes, upgrades, boosters that fit the loop.
- **F10 IP risk (inverted).** 1 = recognisable characters or brand, litigious owner.
  3 = strong identity but generic mechanic. 5 = generic mechanic, easy to re-skin.

Output contract: exactly one entry per factor (F2, F3, F4, F6, F8, F10), each with
`score`, a one-sentence `justification` that cites the evidence, and `confidence`
(`high`, `medium` or `low`). Do not compute the total score: the program does it.
