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
- As of 2026-07-05 after probe63, the cleaned training run loaded 258 card-pick examples and wrote 70 stable card-id deltas. `slay_ai.learn --reset` read 53 logs, applied 42 completed runs, and skipped 11 incomplete runs. Probes64-70 are clean failure candidates for the next refresh, but they have not yet been folded into the learned memory.

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

Do not include `ai_runs_strategy_probe23` in the default training set; it is a known polluted terminal-loop log from before the main-menu synthetic game-over fix. Also do not include probe30 by default: `ai_runs_strategy_probe30` ended as `action_failed`, and `ai_runs_strategy_probe30_continue` is only a diagnostic continuation of that split episode. Do not include probe38 by default: it stopped at max steps in a shop loop and is execution-layer diagnostic only. Do not include the first probe39 attempt: it was manually stopped after repeated invalid `leave` action errors. Do not include probe43 by default: it was manually stopped after an F16 empty-hand wait loop and has no terminal outcome. Also exclude `ai_runs_strategy_probe43_continue_emptyhandfix` by default because it is a split continuation used only to validate the empty-hand fix. Do not include probe45 or its split continuations by default; they were Fiend Fire empty-hand diagnostics. Do not include probe46 by default; it was stopped after exposing the `GRID` `confirm` / MCP `proceed` preflight loop. Do not include probe50 or `ai_runs_strategy_probe50_continue_handselect_rewrite`; they are HAND_SELECT action-rewrite diagnostics. Do not include probe51; it ended as MCP unreachable/read_failed. Do not include probe52; it was manually stopped after exposing a Neow event GRID duplicate-card selection loop. Do not include probe60 or its split continuations by default; they were HAND_SELECT and Act 2 self-damage diagnostics. Probes31-37, probes40-42, probe44, probe47, probe48, probe49, probe53, probe54, probe55, probe61, probe62, probe63, probe64, probe65, probe66, probe67, probe68, probe69, and probe70 are clean completed failures or clean synthetic lethal failures and are candidates for the next learning refresh.

## Promotion Gates

Before increasing learning authority beyond card score deltas:

- At least dozens of completed runs per character/frontier target.
- JSONL logs must use stable ids for candidate options.
- Reward choices must be deduplicated so one reward screen produces one final label.
- Offline replay must show that the model's preferred choice is plausible against the heuristic.
- Live probes must not regress average floor, Act 1 boss reach rate, or MCP stability.

## Next Learning Targets

Order of expansion:

1. Card reward value model.
2. Potion use model for elite, boss, and lethal-risk turns.
3. Rest versus smith threshold calibration.
4. Route risk model based on HP, deck, potions, relics, and next-node options.
5. Shop purchase model.
6. One-turn combat local search.
7. Combat action sequence evaluator.

Current route assessment: stay in Stage 4 until Slime Boss split/minion pressure, Guardian/Hexaghost pressure, and early Act 2 low-HP survival are less brittle. Stage 5 can grow in shadow mode beside this work, but it should not replace the heuristic/search controller until probes show stable improvement.

Pure reinforcement learning is not a near-term target. The game is long, stochastic, and sparse-reward; the current MCP loop is too sample-limited for direct RL to beat the heuristic quickly.
