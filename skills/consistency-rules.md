---
name: consistency-rules
version: "1.0"
description: Machine-checkable consistency rules the Critic applies to the Analyst's scores.
agents: [critic]
tasks: [review]
data:
  min_justification_chars: 20
  low_confidence_review_threshold: 3
  bounds:
    - id: C1
      factor: F3
      max: 2
      when_tags_any: [Open World, MMORPG, Massively Multiplayer, Open World Survival Craft, Colony Sim, Creature Collector, Extraction Shooter, Vehicular Combat]
      why: complex systems (open world, MMO, creature AI) cannot be cloned quickly
    - id: C2
      factor: F2
      max: 2
      when_tags_any: [Visual Novel, Grand Strategy, 4X, Interactive Fiction, Choices Matter, Turn-Based Strategy, CRPG]
      why: narrative and strategy genres have no host genre on Roblox
    - id: C3
      factor: F6
      max: 3
      when_tags_any: [Open World, Grand Strategy, 4X, MMORPG, Colony Sim, Story Rich]
      why: these genres imply long sessions
    - id: C4
      factor: F4
      max: 3
      when_rule_factor_max: {F1: 0}
      why: a solo game rarely produces the group clips that drive Roblox virality
    - id: C5
      factor: F2
      min: 3
      when_tags_any: [Social Deduction, Party Game, Survival Horror, Hide and Seek, Parkour]
      why: these tags map directly to established Roblox genres
---
The Critic re-checks the Analyst's scores before they reach the deterministic scorer.

1. Structure: one score per qualitative factor, justification of at least
   `min_justification_chars` characters, confidence set.
2. Bounds: each rule in `bounds` caps (`max`) or floors (`min`) a factor when its condition
   holds (Steam tags present, or a rule-based factor at or below a value). A violation is
   sent back to the Analyst with the rule id and the reason.
3. Low confidence: if `low_confidence_review_threshold` or more factors have low
   confidence, the result is valid but flagged for human review.
4. After two revisions, remaining violations are clamped to the bound and the candidate is
   flagged `needs_human_review`. The clamp is logged with the rule id.

When an LLM review is enabled, the Critic also asks the model whether each justification
supports its score. Answer with `approved` and a list of concrete `issues`; an issue must
name the factor and the contradicting evidence.
