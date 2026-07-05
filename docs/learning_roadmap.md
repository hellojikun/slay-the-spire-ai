# Learning Roadmap

This project should not wait for a perfect heuristic bot before adding learning. The current path is a hybrid:

1. Heuristics remain the safety controller.
2. JSONL logs provide training data.
3. Learned models score low-risk decisions in shadow mode first.
4. Rules keep veto power until offline replay and live probes show improvement.

## Current Status

- The runner writes per-decision JSONL logs.
- `slay_ai.learn` can replay completed runs into `data/learned_memory.json`.
- `slay_ai.train_card_model` can train `models/card_value_model.json`.
- `StrategyMemory.card_score()` already combines base card scores, learned memory deltas, and model deltas.
- As of 2026-07-05 after probe77, `slay_ai.training_manifest` can classify logs into `clean_trainable`, `diagnostic_excluded`, and `infra_blocked` before any offline learning step. The probe64-72 manifest classified 9 logs as clean, 0 diagnostic, and 0 infra-blocked, then extracted 128 route-risk rows, 484 potion-tempo rows, and 7 pre-boss deck-quality rows for shadow-model work. The probe76 manifest classified 1 clean Act 2 F21 failure and extracted 19 route-risk rows, 23 potion-tempo rows, and 1 pre-boss deck-quality row. The probe77 manifest classified 1 clean F16 Guardian failure and extracted 16 route-risk rows, 5 potion-tempo rows, and 1 pre-boss deck-quality row.
- `slay_ai.static_knowledge` now validates seed card/monster/potion facts under `data/static_knowledge/` and lets `training_manifest --knowledge-dir` enrich shadow samples with deck, potion, and enemy features. This is a feature layer, not a label source.
- `slay_ai.readiness.act1_readiness()` now provides an Act 1 readiness gate for shadow evaluation: HP, output, defense, AOE, debuffs, potion tempo, boss readiness, and elite readiness. `training_manifest --knowledge-dir` writes readiness scores, gaps, flags, and recommendations into route-risk and pre-boss shadow rows. After probe76, route policy also consumes this readiness signal as a conservative Act 1 soft penalty while keeping learned models shadow-first.
- `slay_ai.learn` and `slay_ai.train_card_model` accept `--manifest`, so training can consume only the clean bucket from a generated manifest. A probe64-72 card-model refresh loaded 67 card-pick examples and wrote 60 local comparison deltas.

## Project Phases

- Stage 1: engineering loop / automatic operation is complete.
- Stage 2: heuristic strategy prototype is complete.
- Stage 3: log learning / lightweight model is initially complete.
- Stage 4: search planning / deck evaluation is in progress. A conservative one-turn combat local search prototype exists, but broader deck evaluation and calibrated search scoring are not complete.
- Stage 5: autonomous learning AI is not complete; any learned model should remain shadow scoring, tie-breaker, or high-confidence veto only.
- Stage 6: high-win-rate A20 validation is not complete. Current work must still follow the real unlock frontier, not claim A20 until unlocked and proven.

## Agent Consensus

The current advisory agents agree on this boundary:

- Start learning now, but only as an assistant to the heuristic policy.
- Do not let a model control the whole run yet.
- Begin with card rewards because the action space is small and the logs already capture candidate cards.
- The next autonomous-learning surfaces should be shadow scoring for route risk, potion tempo, shop buying, and rest/smith thresholds.
- Use stable ids for features and labels: card ids, relic ids, potion ids, screen types, and commands.
- Keep full combat learning for later; first improve combat with rules, one-turn local search, and encounter-specific evidence.
- Do not expand learning authority while execution-layer logs are polluted by MCP/action races; fix and exclude diagnostic runs first.
- After probe64, the next Stage 4 milestone is Slime Boss split/minion planning: split timing, post-split multi-target pressure, Slimed hand pollution, defensive floor, and potion/AoE tempo. This should be implemented as search/evaluator improvements before any broad learned combat controller.
- After probe65/probe66, early forced-elite bleed and slow-engine play under pressure are also active Stage 4 bottlenecks. These should feed route/deck evaluation and combat evaluator features before any learned model receives more authority.
- After probe67, Guardian/Hexaghost high-pressure defense and Sentries/Lagavulin HP bleed are active Stage 4 bottlenecks. Route changes can reach the Act 1 boss more often, but combat evaluator calibration still decides whether the run arrives with enough HP.
- After probe68, the route is still correct but should stay in Stage 4: Guardian was beaten and action execution stayed clean, while Act 2 low-HP survival, potion timing, Snecko/high-incoming turns, and route/resource evaluation became the next bottleneck. Do not promote a model from shadow scoring to control on this evidence alone.
- After probe69, low-HP Skill Potion handling has a regression test and live execution remained clean, but Slime Boss split/minion pressure is again the dominant Act 1 bottleneck. The next model/search milestone should be high-pressure card-order plus potion search, not a wider list of one-off potion names.
- After probe70, the next Stage 4 priority is search sequence commitment. The search often finds a full defensive line, but the runner/policy commits only one card and later replans can drift back to attacks. This should be fixed before training a combat action model from these traces.
- After probe71, search sequence commitment is live-validated: the run produced 17 continued search-sequence actions and still had 0 `action_failed` records. The route remains correct, but the next Stage 4 priority moves to boss/high-pressure evaluator calibration and deck/resource quality. Do not promote a learned combat controller yet; use learning first as a shadow value model for card rewards, route risk, shop/potion value, and post-sequence state quality.
- After probe72, the run ended earlier at F11 despite clean execution and live Weak search behavior. This confirms the direction is not "more heuristic exceptions forever"; the next useful work is clean-data labeling plus route risk, potion tempo, and boss-prep deck quality models while keeping heuristics/search as the controller.
- In the multi-agent parallelization round, program work added a scoped Liquid Memories high-pressure use rule, AI work added an Act 1 readiness scorer, and main-thread infrastructure added static card/monster/potion features to shadow data. These should be evaluated in shadow and targeted probes before broad model authority increases.
- After the flow-agent run and probe73, Act 1 failure evidence converged on early no-buffer forced elite commitment and Sentries readiness. The route policy now looks 6 layers ahead, logs `forced_elite_within_5`, and more strongly avoids high-HP no-buffer forced-elite paths when a shop/rest-buffer route is visible.
- After probe74, the route fix showed progress from F6 to F10 with clean execution, but the run still died before the Act 1 boss while carrying an unused `CultistPotion`. This does not invalidate the hybrid route; it shows that static potion knowledge and potion-tempo labels must be kept complete enough for shadow models to learn from clean failures. `CultistPotion` is now a known scaling/elite-tempo potion, and the next live check is probe75.
- After probe75, the run reached F12 with clean execution and one recovered preflight mismatch, then died in a second elite/multi-enemy pressure sequence. Readiness v3 now separates immediate tempo potions from scaling potions, stops counting starter Defends as premium block, and flags low-buffer hallway or forced-elite paths with no AOE/Weak/potion support.
- After probe76, readiness is no longer only shadow: Act 1 route lookahead consumes it as a conservative soft penalty, and the run beat the Act 1 boss before dying cleanly at Act 2 F21. This is positive evidence for the hybrid route. Keep learned models as shadow scorers or tie-breakers; the next shadow/control surface should be Act 2 low-HP route risk rather than a full combat action learner.
- Act 2 low-HP route risk has started as the next small promotion step. Readiness now labels low-HP Act 2 forced-combat paths with recovery, emergency potion, defense, and weak gaps; route policy uses those conditions as a soft penalty when recovery is not close. Probe76 shadow rows were regenerated so F18/F19/F20 are marked as Act2 route-risk examples.
- After probe77, Act 1 boss access remains possible, but boss readiness remains brittle: the run entered Guardian at full HP with no potions, readiness marked `boss_not_ready` and `boss_no_tempo_potion`, and the run died on a 36-incoming Guardian turn. Keep boss deck/resource quality as a parallel Stage 4 target beside Act 2 low-HP routing.
- Boss-prep resource quality has started as another small promotion step. Late Act 1 card rewards now prefer boss output/defense over unsupported slow engines when boss prep is weak, shops can prioritize a high-impact boss potion over ordinary Strike purge, and pre-boss shadow rows expose `boss_potion_gap` directly for future models.

## Clean Training Manifests

Before replaying logs into memory or model training, build a manifest and optional shadow datasets:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe64 ai_runs_strategy_probe65 ai_runs_strategy_probe66 ai_runs_strategy_probe67 ai_runs_strategy_probe68 ai_runs_strategy_probe69 ai_runs_strategy_probe70 ai_runs_strategy_probe71 ai_runs_strategy_probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72
python -m slay_ai.training_manifest ai_runs_strategy_probe64 ai_runs_strategy_probe65 ai_runs_strategy_probe66 ai_runs_strategy_probe67 ai_runs_strategy_probe68 ai_runs_strategy_probe69 ai_runs_strategy_probe70 ai_runs_strategy_probe71 ai_runs_strategy_probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72 --knowledge-dir data\static_knowledge
python -m slay_ai.training_manifest ai_runs_parallel_flow --output data\training_manifest_parallel_flow.json --shadow-dir data\shadow_parallel_flow --knowledge-dir data\static_knowledge
python -m slay_ai.training_manifest ai_runs_strategy_probe73 --output data\training_manifest_probe73.json --shadow-dir data\shadow_probe73 --knowledge-dir data\static_knowledge
python -m slay_ai.training_manifest ai_runs_strategy_probe74 --output data\training_manifest_probe74.json --shadow-dir data\shadow_probe74 --knowledge-dir data\static_knowledge
python -m slay_ai.training_manifest ai_runs_strategy_probe75 --output data\training_manifest_probe75.json --shadow-dir data\shadow_probe75 --knowledge-dir data\static_knowledge
python -m slay_ai.training_manifest ai_runs_strategy_probe76 --output data\training_manifest_probe76.json --shadow-dir data\shadow_probe76 --knowledge-dir data\static_knowledge
python -m slay_ai.training_manifest ai_runs_strategy_probe77 --output data\training_manifest_probe77.json --shadow-dir data\shadow_probe77 --knowledge-dir data\static_knowledge
python -m slay_ai.train_card_model --manifest data\training_manifest_probe64_72.json --model-path models\card_value_model_probe64_72.json --min-count 1 --max-delta 6
python -m slay_ai.learn --manifest data\training_manifest_probe64_72.json --reset
```

The manifest is the gate between execution evidence and learning. Completed clean failures are trainable; action failures and manual diagnostics stay visible but out of default training.
The static knowledge directory adds compact factual features such as `deck_tag_aoe`, `deck_tag_weak`, `potion_role_emergency`, `enemy_boss_count`, and `enemy_max_expected_attack`; these features help shadow models reason about Act 1 readiness without treating wiki/mod data as outcomes.

## First Learning Stage

The first stage is card-reward learning:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_strategy_probe55 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_strategy_probe55 ai_runs_subject_probe ai_runs_subject_probe2 --reset
python -m unittest discover -s tests
```

This produces:

- `models/card_value_model.json`
- `data/learned_memory.json`

The trained model is intentionally lightweight JSON. It can be deleted or regenerated at any time if it looks polluted.

Do not include `ai_runs_strategy_probe23` in the default training set; it is a known polluted terminal-loop log from before the main-menu synthetic game-over fix. Also do not include probe30 by default: `ai_runs_strategy_probe30` ended as `action_failed`, and `ai_runs_strategy_probe30_continue` is only a diagnostic continuation of that split episode. Do not include probe38 by default: it stopped at max steps in a shop loop and is execution-layer diagnostic only. Do not include the first probe39 attempt: it was manually stopped after repeated invalid `leave` action errors. Do not include probe43 by default: it was manually stopped after an F16 empty-hand wait loop and has no terminal outcome. Also exclude `ai_runs_strategy_probe43_continue_emptyhandfix` by default because it is a split continuation used only to validate the empty-hand fix. Do not include probe45 or its split continuations by default; they were Fiend Fire empty-hand diagnostics. Do not include probe46 by default; it was stopped after exposing the `GRID` `confirm` / MCP `proceed` preflight loop. Do not include probe50 or `ai_runs_strategy_probe50_continue_handselect_rewrite`; they are HAND_SELECT action-rewrite diagnostics. Do not include probe51; it ended as MCP unreachable/read_failed. Do not include probe52; it was manually stopped after exposing a Neow event GRID duplicate-card selection loop. Do not include probe60 or its split continuations by default; they were HAND_SELECT and Act 2 self-damage diagnostics. Probes31-37, probes40-42, probe44, probe47, probe48, probe49, probe53, probe54, probe55, probe61, probe62, probe63, probe64, probe65, probe66, probe67, probe68, probe69, probe70, probe71, probe72, probe73, probe74, probe75, probe76, and probe77 are clean completed failures or clean synthetic lethal failures and are candidates for manifest-gated learning.

## Promotion Gates

Before increasing learning authority beyond card score deltas:

- At least dozens of completed runs per character/frontier target.
- JSONL logs must use stable ids for candidate options.
- Reward choices must be deduplicated so one reward screen produces one final label.
- Offline replay must show that the model's preferred choice is plausible against the heuristic.
- Live probes must not regress average floor, Act 1 boss reach rate, or MCP stability.

## Next Learning Targets

Order of expansion:

1. Clean manifest and stable trainable datasets.
2. Static card/monster/potion feature tables.
3. Card reward value model.
4. Act 1 readiness shadow gate for boss/elite preparation.
5. Route risk shadow model based on HP, floor, path commitment, deck strength, potions, relics, and buffers.
6. Potion tempo shadow model for elite, boss, and lethal-risk turns.
7. Pre-boss/post-combat deck quality labels, including explicit boss potion/resource gaps.
8. Rest versus smith threshold calibration.
9. Shop purchase model.
10. One-turn combat local search and evaluator calibration.
11. Combat action sequence learning, only after search labels are stable.

Current route assessment: stay in Stage 4 until early Act 1 forced-elite commitment, Sentries AOE/Weak/readiness, Slime Boss split/minion pressure, Hexaghost/boss high-pressure survival, deck/resource quality before boss fights, and early Act 2 low-HP survival are less brittle. Stage 5 can grow in shadow mode beside this work, especially through route-risk and resource-risk labels, but it should not replace the heuristic/search controller until probes show stable improvement.

Pure reinforcement learning is not a near-term target. The game is long, stochastic, and sparse-reward; the current MCP loop is too sample-limited for direct RL to beat the heuristic quickly.
