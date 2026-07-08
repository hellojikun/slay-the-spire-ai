# Static Knowledge Dataset

The static knowledge dataset gives the learning pipeline factual features about Slay the Spire entities. It should not be treated as a win/loss label source. Labels still come from our own `clean_trainable` run logs.

## Purpose

- Card facts: type, cost, rough base and upgraded damage/block, and tags such as `aoe`, `weak`, `vulnerable`, `draw`, `self_damage`, and `slow_engine`.
- Upgrade facts: card lookup normalizes upgraded labels such as `Bash+` and card objects with `upgraded`/`upgrades`; deck features expose upgraded count, current damage/block totals, full-upgrade potential, and remaining upgrade gain for future deck-quality and rest/smith models.
- Monster facts: boss/elite flags, Act, multi-hit pressure, split mechanics, status pressure, debuff/scaling pressure, multi-enemy pressure, artifact, frontload/block checks, potion-tempo checks, and rough maximum expected attack.
- Potion facts: target requirements, role tags, and immediate numeric effects such as block, damage, or energy.
- Relic facts: starter/boss/shop flags through tags, route recovery, turn-one tempo, vulnerable/strength synergies, elite tempo, boss-relic downsides, and observed utility values such as potion slots, thorns, gold, curse protection, card removal cost, turn-three block, and attack-upgrade counts.
- Boss-mechanic facts: Act 1 boss mechanics, search-planning hints, deck needs, potion needs, and compact numeric facts such as split threshold percent, Guardian Mode Shift threshold, Sharp Hide damage, Hexaghost hit count, Burn damage, and post-split enemy count for shadow deck-quality, potion-tempo, and combat-search analysis.

These facts enrich `route_risk`, `potion_tempo`, `pre_boss_deck_quality`, and `combat_search_labels` shadow examples. They are especially useful for Act 1 readiness checks and search-label review: whether the run has enough frontload, block, AOE, debuffs, potion tempo, and HP buffer before an elite or boss, and whether a searched combat line used known enemy/boss facts. Combat-search labels keep the same boss numeric facts so later imitation/value models can learn from search context without reading policy internals; the PyTorch combat-value shadow model treats these boss and enemy pressure facts as numeric features. Monster tags roll up into stable `enemy_*_count` features such as `enemy_status_pressure_count`, `enemy_debuff_count`, `enemy_scaling_pressure_count`, `enemy_split_count`, `enemy_multi_enemy_pressure_count`, and `enemy_potion_tempo_check_count`; the combat-value model consumes those numeric profile features directly. Unknown static fields such as `deck_unknown_cards`, `enemy_unknown_count`, and `relic_unknown_count` are surfaced by shadow feature audit, live/completed `run_status`, per-run diagnosis, and Act 1 boss gate data-quality summaries, so missing knowledge or ID normalization gaps are reviewed as data work instead of being mistaken for strategy labels. Shadow feature audit also writes a `feature_focus` block with category-level missing-prefix, zero-prefix, and unknown-static issues; Act 1 boss gate rolls this up as `feature_issue_categories` for agent handoffs. The route-risk, potion-tempo, and pre-boss deck-quality stat trainers also allow boss numeric facts through their safe feature filters. Boss `search_hints` become `boss_search_hint_*` features, and boss numeric values become `boss_max_*` / `boss_total_*` features; they are advisory context for shadow models and label review, not direct policy controls.

## Files

```text
data/static_knowledge/cards.json
data/static_knowledge/monsters.json
data/static_knowledge/potions.json
data/static_knowledge/relics.json
data/static_knowledge/bosses.json
slay_ai/static_knowledge.py
```

Current seed scope is intentionally small: Act 1 Ironclad bottlenecks plus early Act 2 monsters seen in recent probes. Expand it from clean evidence and verified game facts, not from model guesses.

2026-07-06 probe94-106 refresh: the knowledge table now covers the English-id potion gaps `ColorlessPotion`, `FairyPotion`, `PowerPotion`, `EssenceOfSteel`, `BlessingOfTheForge`, `Fruit Juice`, and `SteroidPotion`, plus the observed Act 2 enemies `Chosen`, `BookOfStabbing`, `GremlinTsundere`, `Mugger`, and `TheCollector`. After refreshing manifests and shadow rows, the combined Act 1 boss gate stayed PASS with `schema_missing=0`; `potion_unknown_count` and `enemy_unknown_count` disappeared from gate-level `unknown_static_features`.

The manifest and gap-report pipeline also derives a run-local observed card alias map from `card_reward_options` entries that contain both localized `name` and verified English `id`. Conflicting aliases are ignored, and the mapping is used only for static feature extraction/reporting, not for labels or live policy. On probe94-106 this plus verified English card facts for observed Ironclad cards such as `Feel No Pain`, `Sentinel`, `Demon Form`, `Fiend Fire`, `Ghostly Armor`, `Rampage`, and `Fire Breathing` reduced the standalone static gap report from `cards=16465/56` to `cards=2449/16`. After refreshing manifests and shadow rows, gate-level `deck_unknown_cards` disappeared entirely.

The relic table now uses verified name keys from the local game jar (`desktop-1.0.jar` `localization/zhs/relics.json` and `localization/eng/relics.json`) for observed probe94-106 relics, including Potion Belt, Bronze Scales, Regal Pillow, Gambling Chip, Matryoshka, Blue Candle, Old Coin, Fusion Hammer, Meat on the Bone, Whetstone, Captain's Wheel, Smiling Mask, Omamori, Fossilized Helix, and Nunchaku. This reduced standalone relic gaps from `relics=4878/19` to `relics=689/1`, and gate-level unknown static total from `1928` to `127`. The remaining relic string `���а�` is not present in any local relic localization file, so it should stay unresolved until future logs capture reward/relic ids directly.

2026-07-07 `_24` refresh: after the A0 `_24` batch, static knowledge gaps named `Bloodletting`, `Sever Soul`, `Infernal Blade`, `Searing Blow`, `Bag of Preparation`, `Tiny House`, and `Darkstone Periapt`. These entries were added as compact factual features only. A focused rescan of `runs\ai_runs_climb_cycle_ironclad_a0_win_24` now reports `static_knowledge_gaps logs=3 missing=0`. This improves shadow feature coverage and review clarity; it is not a strategy rule or training label.

2026-07-07 `_25` refresh: after the A0 `_25` batch, static knowledge gaps named `Doubt` / `疑虑`, `Brutality` / `残暴`, localized `Cleave` / `顺劈斩`, and the relics `War Paint` / `战纹涂料`, `Pantograph` / `缩放仪`, and `Mercury Hourglass` / `水银沙漏`. These entries were added as compact factual features only. A focused rescan of `runs\ai_runs_climb_cycle_ironclad_a0_win_25` now reports `static_knowledge_gaps logs=3 missing=0`. This is feature coverage, not a live policy change.

## Commands

Validate the tables:

```powershell
python -m slay_ai.static_knowledge
```

Find concrete missing entities from run logs when `feature_unknown` or `unknown_static_features` appears:

```powershell
python -m slay_ai.static_knowledge_gaps runs\ai_runs_strategy_probe104_a0 runs\ai_runs_strategy_probe105_a0 --output data\static_knowledge_gaps_probe104_105.json
```

`slay_ai.offline_batch` also writes `static_knowledge_gaps_<name>.json` automatically when `--knowledge-dir` is enabled, and carries a compact gap summary into `batch_triage.static_knowledge_gaps`, agent handoff JSON, and assignment prompts. Use the standalone command when you need a focused gap report without rebuilding the whole manifest/advice/gate bundle.

For lightweight monitor handoffs, `run_status` can write the same concrete report while staying read-only:

```powershell
python -m slay_ai.run_status runs\ai_runs_strategy_probe106_a0 --static-knowledge-gaps-output data\static_knowledge_gaps_probe106.json --assignment-output data\run_status_assignment_probe106.txt
```

Audit excluded combat-search labels before retraining shadow combat models:

```powershell
python -m slay_ai.combat_label_audit data\shadow_probe104_a0 data\shadow_probe105_a0 --output data\combat_label_audit_probe104_105.json
```

Generate enriched training manifests and shadow datasets:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe64 ai_runs_strategy_probe65 ai_runs_strategy_probe66 ai_runs_strategy_probe67 ai_runs_strategy_probe68 ai_runs_strategy_probe69 ai_runs_strategy_probe70 ai_runs_strategy_probe71 ai_runs_strategy_probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72 --knowledge-dir data\static_knowledge
```

## Source Policy

Source priority:

1. Local MCP/game observations from our own runs.
2. Structured mod/API sources such as CommunicationMod/BaseMod.
3. External structured card/reference datasets such as Hugging Face `slaythespire-codex`, when used only for compact factual card fields or embeddings with provenance.
4. Wiki pages used as human-readable cross-checks.

Do not scrape large text chunks into the repo. Store compact factual fields and source URLs. If a fact affects gameplay decisions, prefer adding a regression test or a live probe note.

External run-history datasets are documented separately in `docs/external_run_datasets.md`. They are label/trajectory sources, not static facts, and must not be mixed into this static-knowledge directory without an explicit provenance field.
