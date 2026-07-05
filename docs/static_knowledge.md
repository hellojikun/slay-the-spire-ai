# Static Knowledge Dataset

The static knowledge dataset gives the learning pipeline factual features about Slay the Spire entities. It should not be treated as a win/loss label source. Labels still come from our own `clean_trainable` run logs.

## Purpose

- Card facts: type, cost, rough base damage/block, and tags such as `aoe`, `weak`, `vulnerable`, `draw`, `self_damage`, and `slow_engine`.
- Monster facts: boss/elite flags, Act, multi-hit pressure, split mechanics, status pressure, and rough maximum expected attack.
- Potion facts: target requirements, role tags, and immediate numeric effects such as block, damage, or energy.

These facts enrich `route_risk`, `potion_tempo`, and `pre_boss_deck_quality` shadow examples. They are especially useful for Act 1 readiness checks: whether the run has enough frontload, block, AOE, debuffs, potion tempo, and HP buffer before an elite or boss.

## Files

```text
data/static_knowledge/cards.json
data/static_knowledge/monsters.json
data/static_knowledge/potions.json
slay_ai/static_knowledge.py
```

Current seed scope is intentionally small: Act 1 Ironclad bottlenecks plus early Act 2 monsters seen in recent probes. Expand it from clean evidence and verified game facts, not from model guesses.

## Commands

Validate the tables:

```powershell
python -m slay_ai.static_knowledge
```

Generate enriched training manifests and shadow datasets:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe64 ai_runs_strategy_probe65 ai_runs_strategy_probe66 ai_runs_strategy_probe67 ai_runs_strategy_probe68 ai_runs_strategy_probe69 ai_runs_strategy_probe70 ai_runs_strategy_probe71 ai_runs_strategy_probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72 --knowledge-dir data\static_knowledge
```

## Source Policy

Source priority:

1. Local MCP/game observations from our own runs.
2. Structured mod/API sources such as CommunicationMod/BaseMod.
3. Wiki pages used as human-readable cross-checks.

Do not scrape large text chunks into the repo. Store compact factual fields and source URLs. If a fact affects gameplay decisions, prefer adding a regression test or a live probe note.
