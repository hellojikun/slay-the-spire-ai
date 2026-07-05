# Multi-Agent Workflow

This document records the multi-agent setup used for the Slay the Spire growing AI project, so the collaboration pattern can be resumed without relying on memory.

## Current Snapshot

Updated 2026-07-05 after the probe82 Gremlin Nob Rage search fix.

- Current unlock frontier: `IRONCLAD:A4`, `SILENT:A0`, `DEFECT:A0`, `WATCHER:A0`. A20 is the long-term upper target, not the current runnable claim.
- Latest included probes: `ai_runs_strategy_probe82/20260705_181408_ironclad_a4.jsonl` ended as a clean F6 Gremlin Nob game-over after 151 ok actions, 1 recovered action, and 0 failed actions; Burn/Burn+ was not live-validated because the run died early, but it exposed a Nob Rage miss where `Defend_R -> Defend_R -> Headbutt` was evaluated as `loss 24->14` while live incoming rose 24 -> 27 -> 30 after the Skills. `ai_runs_strategy_probe81/20260705_175914_ironclad_a4.jsonl` ended as a clean F16 Hexaghost game-over after 234 ok actions, 1 recovered action, and 0 failed actions; it reached the boss with no potions, then exposed a combat-search evaluation miss where two `Burn+` cards in hand made the chosen `Uppercut -> Defend_R` line lethal even though search reported `loss 8->1`. `ai_runs_strategy_probe80/20260705_174222_ironclad_a4.jsonl` ended as a clean F16 Guardian game-over after 312 ok actions, 1 recovered action, and 0 failed actions.
- Latest excluded diagnostic probes: `ai_runs_strategy_probe38/20260705_072307_ironclad_a4.jsonl` stopped at max steps due to a `SHOP_ROOM` / `SHOP_SCREEN` loop; the first probe39 attempt was aborted after proving `leave` is not a valid `execute_actions` action; probe43 and its continuation were split empty-hand diagnostics; probe45 and its two continuations were split Fiend Fire empty-hand diagnostics; probe46 (`ai_runs_strategy_probe46/20260705_085517_ironclad_a4.jsonl`) was stopped after exposing a `GRID` `confirm` / MCP `proceed` preflight loop; probe50 (`ai_runs_strategy_probe50/20260705_093533_ironclad_a4.jsonl`) and `ai_runs_strategy_probe50_continue_handselect_rewrite/20260705_093831_ironclad_a0.jsonl` are HAND_SELECT action-rewrite diagnostics; probe51 (`ai_runs_strategy_probe51/20260705_093933_ironclad_a4.jsonl`) ended as MCP unreachable/read_failed; probe52 (`ai_runs_strategy_probe52/20260705_095355_ironclad_a4.jsonl`) was manually stopped after exposing a Neow event GRID duplicate-card selection loop; probe60 (`ai_runs_strategy_probe60/20260705_124621_ironclad_a4.jsonl`) was manually stopped after exposing a multi-card `HAND_SELECT` rewrite gap, `ai_runs_strategy_probe60_continue_handselect_multichoose/20260705_125224_ironclad_a0.jsonl` is a partial continuation validation, and `ai_runs_strategy_probe60_continue_act2/20260705_125715_ironclad_a0.jsonl` is a split continuation used only to diagnose Act 2 Byrds self-damage overuse. Do not use these diagnostic logs in default training.
- Latest execution fix: `SHOP_SCREEN` waits if inventory has not loaded, then uses MCP's valid `cancel` action for the leave button; runner remembers the floor after a shop cancel and forces the next same-floor `SHOP_ROOM` state to `proceed` instead of re-entering the shop.
- Latest runner action fix: before executing actions, runner now checks `get_available_commands`; stale unavailable actions are skipped as `preflight_mismatch`, followed by settle and stable-state reread so the next loop can replan. Narrow rewrites handle stale `GRID` confirmations (`confirm` -> `proceed`) and `HAND_SELECT` drops when MCP exposes `choose` instead of `select_cards`; single-card drops become one `choose`, and multi-card drops become ordered `choose` actions plus `proceed` when available. The runner records `executed_actions`, `rewrite_reason`, and `available_commands`.
- Latest policy fix: Stage 4 has started with a conservative one-turn combat local search. It enumerates bounded pure numeric card sequences, returns only the first action, and falls back to the old single-card heuristic for unsupported hand-changing cards, Gremlin Nob nonlethal skill sequences, protected control cards, reflect-risk attacks, or still-lethal projections. Combat search, policy, and runner share the same `move.damage * move.hits` incoming-damage calculation. After probe56/probe57, combat potion policy now uses Duplication Potion in boss/elite or dangerous turns when the current hand has a high-impact defensive or offensive card, and treats Gambler's Brew as an emergency tempo potion in low-HP/high-incoming turns. After probe58, shop potion scoring now treats Duplication, Energy, Gambler's Brew, Regen, and Swift potions as high-impact buys when potion slots are empty. After probe59, local search may accept modest already-blocking follow-up block sequences, and X-cost attacks preserve block energy under dangerous pressure unless they remove the attack threat. After the probe60 Act 2 continuation, immediate self-damage engine cards such as `Offering` and `Bloodletting` now receive extra risk penalties in Act 2 multi-enemy setup turns and after current-turn activity at unsafe HP, while high-HP Offering remains legal. After probe62, `Hemokinesis` self-damage is modeled in both single-card scoring and one-turn search, and nonlethal use at HP <= 2 is vetoed. After probe63, combat search and single-card scoring ignore spurious `block` values on generic attack cards; only known attack-block cards such as `Dash`, `Iron Wave`, `Just Lucky`, and `Wallop` count attack-card block. After probe64/probe65, single-card scoring avoids zero-energy X-cost attacks, penalizes shallow no-follow-up Slime splits, and treats `Dark Embrace` as a slow engine under dangerous incoming pressure. After probe67, combat search models Guardian `Mode Shift` as stopping the current attack when enough unblocked damage is dealt, and single-card intent-stop checks use the same Guardian threshold. After probe68, `SkillPotion` is treated as a defensive emergency resource only when projected incoming damage creates `defensive_danger`. After probe69, combat search and single-card scoring ignore spurious `damage` values on non-attack cards such as `Defend`, `Battle Trance`, and `Slimed`, and the search acceptance gate allows modest block-first lines under extreme incoming pressure. After probe71, accepted defensive search results store the remaining normalized card-id sequence for the same floor/turn, continue playable follow-up cards across repeated state reads, and clear the commitment when the turn, screen, hand, energy, or reflect-risk context changes. After probe72, combat search models Weak from cards such as `Clothesline`, including multi-hit attack reduction, Artifact blocking, and all-enemy Weak cards. After probe74/probe75, `CultistPotion` is recognized as scaling/elite-tempo static knowledge, route/readiness treat it as high-impact but not immediate hallway tempo, combat may use Cultist/Strength/Steroid scaling potions in dangerous early multi-enemy hallway fights or emergency low-HP tempo turns, and readiness v3 flags low-buffer hallway routes without immediate tempo or premium block. After probe76, Act 1 route lookahead consumes readiness v3 as a conservative soft penalty, records `readiness_penalty`, `readiness_flags`, and `readiness_gaps` on map option lookahead, and skips current shop/rest/treasure buffer nodes. The next iteration adds an Act 2 low-HP route-risk gate: route lookahead now records and penalizes `act2_critical_hp_forced_combat`, `act2_no_recovery_buffer`, `act2_no_emergency_potion`, and weak/block gaps when low HP paths force hallway combat before recovery. After probe77, Act 1 boss-prep scoring gives late Act 1 boss-damage/defense card rewards extra weight when the deck lacks boss output or potions, penalizes unsupported slow engines such as `Dark Embrace`, and lets high-impact boss-prep potions beat Strike purge in shops. After probe78, Act 1 card rewards add a first-AOE gap bonus from F5-F15 when the deck has no AOE, so evidence like `Flame Barrier / Cleave / Pommel Strike` can choose `Cleave` before Slime Boss/Sentries pressure while still preferring premium block once AOE is covered; post-split multi-slime target selection now focuses lower-HP attacking slimes before a healthier large slime. After probe79, Guardian high-pressure Mode Shift turns play zero-cost `Battle Trance` before spending energy on block, unless `No Draw` is already active. After probe80, Guardian Mode Shift pressure also allows local-search commitments to continue attack follow-ups such as `Whirlwind`, instead of only block follow-ups. After probe81, combat search includes remaining-hand `Burn`/`Burn+` end-turn damage in initial/projected loss and lethal checks. After probe82, combat search models Gremlin Nob `Anger`/Rage attack gain when Skill cards are played, including player Vulnerable scaling.
- Latest route policy fix: route decisions now attach a best-effort full-map observation only on `MAP` screens, with a 2.5s timeout and per-episode failure disable after repeated MCP errors. When the map payload can be parsed, `policy_route` evaluates a bounded 6-layer lookahead and applies path-commitment risk for low-HP forced elite/combat routes while logging base score, lookahead adjustment, final score, node count, and edge count. If map observation fails, route policy falls back to the old immediate `next_nodes` scoring. After probe61, Act 2 low-HP route scoring penalizes immediate hallway fights when a question mark is available, so equal-risk lookahead no longer lets the monster base score dominate. After probe63, Act 1 routes that force an elite within 3 nodes without a rest/shop buffer are penalized, while nearby shop/rest buffers receive an explicit survival bonus. After probe66, the no-buffer forced elite penalty was strengthened so a full-HP path does not lock into an early elite when a slightly lower-score question path has later recovery. After probe73, high-HP Act 1 paths that still force an elite within 3 nodes with no rest/shop buffer receive a stronger penalty, so a visible shop/rest-buffer route can win before the path is locked.
- Latest MCP client fix: `MCPClient.initialize()` is idempotent for reused sessions, `campaign` and `runner` call `ensure_initialized()`, and invalid non-JSON HTTP responses are wrapped as `MCPError` with a short response preview.
- Latest architecture refactor: MCP implementation now lives in `slay_ai.mcp.client`, with `slay_ai.mcp_client` kept as a compatibility shim. State reading/retry/stability checks now live in `slay_ai.core.state_reader`. Monster incoming-damage interpretation now lives in `slay_ai.domain.monsters`, with `slay_ai.combat_math` kept as a compatibility shim. Route/map policy now lives in `slay_ai.policy_route`; `Decision` now lives in `slay_ai.policy_decision`.
- Latest runner start fix: if a new run is blocked by a lingering terminal `GAME_OVER` screen exposing only `proceed`, the runner now proceeds once and retries `start_game`. This keeps `existing_save=fail` strict while recovering the common post-death menu state.
- Latest learning pipeline fix: `GAME_OVER` logs with missing `victory` now default to a completed loss in snapshots and offline learning, and `slay_ai.training_manifest` can gate training data into `clean_trainable`, `diagnostic_excluded`, and `infra_blocked` buckets. `slay_ai.learn` and `slay_ai.train_card_model` can consume only the clean bucket via `--manifest`.
- Latest learning run: `python -m slay_ai.training_manifest ...probe64 ...probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72 --knowledge-dir data\static_knowledge` classified 9 clean logs, 0 diagnostic logs, and 0 infra-blocked logs, then wrote static-knowledge and readiness-enriched route-risk, potion-tempo, and pre-boss deck-quality rows. The flow-agent manifest `data\training_manifest_parallel_flow.json` classified 1 clean log, probe73 manifest `data\training_manifest_probe73.json` classified 1 clean log, probe74 manifest `data\training_manifest_probe74.json` classified 1 clean log with 33 potion-tempo rows, probe75 manifest `data\training_manifest_probe75.json` classified 1 clean log with 13 route-risk rows, probe76 manifest `data\training_manifest_probe76.json` classified 1 clean log with 19 route-risk rows, 23 potion-tempo rows, and 1 pre-boss deck-quality row, probe77 manifest `data\training_manifest_probe77.json` classified 1 clean log with 16 route-risk rows, 5 potion-tempo rows, and 1 pre-boss deck-quality row, probe78 manifest `data\training_manifest_probe78.json` classified 1 clean log with 15 route-risk rows, 122 potion-tempo rows, and 1 pre-boss deck-quality row, probe79 manifest `data\training_manifest_probe79.json` classified 1 clean log with 15 route-risk rows, 38 potion-tempo rows, and 2 pre-boss deck-quality rows, probe80 manifest `data\training_manifest_probe80.json` classified 1 clean log with 15 route-risk rows, 39 potion-tempo rows, and 1 pre-boss deck-quality row, probe81 manifest `data\training_manifest_probe81.json` classified 1 clean log with 15 route-risk rows, 43 potion-tempo rows, and 1 pre-boss deck-quality row, and probe82 manifest `data\training_manifest_probe82.json` classified 1 clean log with 6 route-risk rows, 43 potion-tempo rows, and 0 pre-boss deck-quality rows. Static potion knowledge now covers 21 potions including `SpeedPotion`, `DistilledChaos`, and `SmokeBomb`.
- Latest validation: `python -m unittest discover -s tests` passed 217 tests, `python -m compileall slay_ai tests` passed, and `git diff --check` passed apart from CRLF warnings.
- Next live validation: run probe83 after the Gremlin Nob Rage search fix. Active bottlenecks are early forced-elite route risk, boss potion/resource quality, Hexaghost output/resource pressure, Guardian Mode Shift planning, Slime Boss split/minion pressure, Act 2 low-HP route/resource planning, and runner target/settle races.

## Long-Lived Agents

Keep these advisory agents available across the project unless the user explicitly asks to close them. Spawn a fresh Turing-style worker only for a bounded, disjoint code-edit task when thread capacity allows.

| Agent | Role | Current id | Responsibility |
| --- | --- | --- | --- |
| Archimedes | Flow / run-data agent | `019f30f8-ed4d-7bd3-9519-b3bcfda8116d` | Run frontier probes, accumulate climb logs, classify whether failures are clean/diagnostic/infra, and report concrete bottlenecks. Does not edit code. Current assignment: run probe83 after the Nob Rage patch is committed and pushed. |
| Harvey | AI upgrade / algorithm agent | `019f2e94-552b-7301-9cc5-21eed1fc2949` | Static features, readiness gates, shadow models, training data design, and model promotion gates. Does not edit code unless explicitly reassigned. Current status: resume attempt failed with `agent thread limit reached`; recreate or resume when tool capacity allows. |
| Main Codex agent | Program optimization / integration | current thread | Owns code edits, tests, commits, pushes, and conflict control while subagent capacity is capped at two active child agents. |
| Mill | Program optimization agent | unavailable in current tool state | The old id `019f2f5c-fae1-7cb2-bea6-b8574085839d` now returns `not_found`; recreate only when thread capacity allows and update this table. |
| Turing | Code development worker | spawn on demand | Evidence-backed bounded code work with disjoint write scope when thread capacity allows. |

If context compaction or tool state makes an agent's actual status uncertain, first inspect active subagent ids from the environment/tool state and reuse the canonical thread in this table when available. Start a fresh long-lived agent only if the canonical thread is missing or unusable and thread capacity allows, then update this table immediately.

## Current Activation Log

2026-07-05 multi-agent parallelization round:

- User direction: make multi-agent collaboration real and parallel because Act 1 has been blocked too long.
- Long-lived agent roles were reassigned:
  - Archimedes runs the process and accumulates logs with `ai_runs_parallel_flow` / `data\campaign_parallel_flow.json`.
  - Mill owns program-level fixes, starting with potion tempo / Liquid Memories.
  - Harvey owns AI-upgrade work, starting with Act 1 readiness gates and shadow model features.
  - Halley owns coordination and merge-risk review.
- Main thread owns integration, static knowledge infrastructure, tests, commits, and pushes.
- Static knowledge work started in main thread: `data/static_knowledge/cards.json`, `monsters.json`, `potions.json`, `slay_ai.static_knowledge`, and `training_manifest --knowledge-dir` enrich route/potion/pre-boss shadow rows.
- AI-upgrade agent delivered `slay_ai.readiness.act1_readiness`, a read-only Act 1 readiness scorer that returns scores, gaps, risk flags, features, and recommendations without changing policy behavior.
- Program agent delivered a scoped Liquid Memories potion-tempo fix: under `defensive_danger`, the policy may use Liquid Memories only when discard contains a high-impact block/control/lethal/intent-stop target.
- Validation after integration: `python -m unittest discover -s tests` ran 190 tests OK; `python -m compileall slay_ai tests` OK; `git diff --check` OK.

2026-07-05 probe76 readiness-route validation round:

- Main-thread behavior change: Act 1 route lookahead now consumes `act1_readiness()` as a conservative soft penalty instead of leaving readiness purely shadow-only. The route option lookahead records `readiness_penalty`, `readiness_flags`, and `readiness_gaps`, and current shop/rest/treasure buffer nodes are not penalized.
- Static knowledge update: `Speed Potion` and `Distilled Chaos` are now known potion facts, bringing static potion coverage to 21 entries.
- Probe76 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe76.json --log-dir ai_runs_strategy_probe76 --use-all-hardware
```

- Probe76 result: `ai_runs_strategy_probe76/20260705_164024_ironclad_a4.jsonl` beat the Act 1 boss and ended as a clean Act 2 F21 game-over. Execution was usable for training: 311 ok actions, 4 recovered actions, and 0 failed actions. The recovered actions were transition/target/GRID races, not run-killing loops.
- Probe76 manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe76 --output data\training_manifest_probe76.json --shadow-dir data\shadow_probe76 --knowledge-dir data\static_knowledge
```

- Probe76 manifest result: 1 clean trainable log, 0 diagnostic logs, 0 infra-blocked logs, 19 route-risk rows, 23 potion-tempo rows, and 1 pre-boss deck-quality row.
- New bottleneck: the project is no longer blocked at "cannot reach first boss" for this frontier. The next probe should focus on Act 2 low-HP route/resource policy and the four recovered action races.

2026-07-05 probe77 parallel dispatch:

- Main thread committed the probe76 route-readiness change locally as `Use readiness risk in Act 1 routes`. SSH push to `github.com` was blocked by connection resets/timeouts, and HTTPS push stalled in `git-credential-manager`; do not assume the remote has this commit until a later successful push is recorded.
- Program-agent command:

```text
multi_agent_v1.send_input(Mill, "Inspect probe76 recovered actions only, do not edit files, classify invalid play/end, target index out of bounds, and GRID confirm/choose mismatch by root cause and minimal tests.")
```

2026-07-05 probe78 three-workstream dispatch:

- Tool-state constraint: only two child agents could be kept active; spawning a third program worker failed with `agent thread limit reached`. The project still ran three workstreams by assigning program optimization/integration to the main Codex agent.
- Flow-agent command:

```text
multi_agent_v1.send_input(Archimedes, "You are the flow/run-data agent. Inspect probe78, then run probe79. Do not edit code. Write only ai_runs_strategy_probe79/, data/training_manifest_probe79.json, and data/shadow_probe79/.")
```

- AI-agent command:

```text
multi_agent_v1.send_input(Harvey, "You are the AI/algorithm optimization agent. Read policy/readiness/training_manifest/static_knowledge and probe76-78 data. Do not edit files. Recommend the next Slime Boss, route-risk, potion-tempo, and pre-boss deck-quality changes.")
```

- Program-worker spawn attempt:

```text
multi_agent_v1.spawn_agent(worker, "Program optimization agent: implement one bounded MCP/runner stability fix such as ensure_initialized or JSONDecodeError wrapping.")
```

- Result: spawn failed because the thread limit was reached. The main agent handled the program line locally.
- Probe78 manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe78 --output data\training_manifest_probe78.json --shadow-dir data\shadow_probe78 --knowledge-dir data\static_knowledge
```

- Probe78 result: 1 clean trainable F16 Slime Boss loss, 0 diagnostic, 0 infra; shadow rows: 15 route-risk, 122 potion-tempo, and 1 pre-boss deck-quality. The pre-boss row showed `boss_potion_gap=true`, `aoe_missing`, and only `SmokeBomb`; F6 card rewards showed `Flame Barrier / Cleave / Pommel Strike`, so the main thread added a first-AOE Act 1 reward bonus and regression tests.

2026-07-05 probe79 flow-agent result:

- Flow-agent result: `ai_runs_strategy_probe79/20260705_172700_ironclad_a4.jsonl` was clean trainable: 230 ok actions, 2 recovered preflight mismatches, 0 failed actions.
- Probe79 manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe79 --output data\training_manifest_probe79.json --shadow-dir data\shadow_probe79 --knowledge-dir data\static_knowledge
```

- Probe79 result: F16 Guardian loss. The run had AOE (`Thunderclap`) but no potions before boss. At Guardian T8, HP was 11 with 36 incoming; the policy played `Flame Barrier` and `Defend`, then drew with `Battle Trance` after spending energy, leaving zero-cost attacks too late to find a Mode Shift line. Next code target: high-pressure draw/order planning before spending the last energy.

2026-07-05 Guardian draw/order fix:

- Main-thread behavior change: under dangerous Guardian Mode Shift pressure, the policy now plays playable zero-cost `Battle Trance` before spending energy on block, unless the player already has `No Draw`. This is a narrow response to probe79 T8 and does not expand learned-combat authority.
- Validation: `python -m unittest discover -s tests` passed 214 tests; `python -m compileall slay_ai tests` passed; `git diff --check` passed apart from CRLF warnings.

2026-07-05 probe80 flow-agent result and follow-up:

- Flow-agent result: `ai_runs_strategy_probe80/20260705_174222_ironclad_a4.jsonl` was clean trainable: 312 ok actions, 1 recovered action, and 0 failed actions.
- Probe80 manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe80 --output data\training_manifest_probe80.json --shadow-dir data\shadow_probe80 --knowledge-dir data\static_knowledge
```

- Probe80 result: F16 Guardian loss. `Battle Trance` was not in the deck, so the probe79 draw-order rule did not trigger. The new bottleneck was local-search commitment: the search found lines like `Defend_R -> Whirlwind`, but only block follow-ups were remembered, so the next policy step replanned into `Bash` or other weaker attacks. Main-thread fix: allow attack follow-up commitment only under Guardian Mode Shift pressure.
- Validation after the follow-up fix: `python -m unittest discover -s tests` passed 215 tests; `python -m compileall slay_ai tests` passed; `git diff --check` passed apart from CRLF warnings.

- Program-agent result: the four recovered actions are state/transition races, not policy blockers. Recommended fixes are a GRID incomplete-snapshot wait, a targeted-play fresh-state guard or `target_index_race` classification, and `post_transition_race` handling for invalid `play`/`end` when available commands already show reward actions.
- AI-agent command:

```text
multi_agent_v1.send_input(Harvey, "Evaluate probe76 Act2/F21 clean failure and choose the next Stage 4/5 algorithm step from Act2 low-HP route risk, combat defense search, and potion search.")
```

- AI-agent result: prioritize an Act 2 low-HP route-risk shadow gate first. Probe76 F18-F20 had low/critical HP, no emergency potion, distant rest, and forced/selected hallway fights before the F21 death. The next control promotion should be a small route-risk penalty after shadow labels prove stable, not a full combat action learner.
- Flow-agent command:

```text
multi_agent_v1.send_input(Archimedes, "Run probe77 with ai_runs_strategy_probe77 and data\\campaign_strategy_probe77.json, then generate data\\training_manifest_probe77.json and data\\shadow_probe77 with static knowledge. Do not edit or commit.")
```

- Architecture-agent note: `Halley` could not be resumed in this round because the sub-agent thread limit was reached. Keep the architecture role documented, and restore or respawn it after one active agent completes if coordination risk increases.

2026-07-05 Act2 low-HP route-risk round:

- Trigger evidence: probe76 F18-F20 Act 2 route rows showed `forced_combat_within_2=true`, no potions, no close recovery, weak/defense gaps, and death at F21. F20 had only 22/88 HP and still selected an `M` route with score 42.0 because the old route policy only applied a small generic low-HP penalty.
- Readiness update: `act1_readiness()` now also emits non-Act-1 route-pressure flags for shadow learning: `act2_low_hp_forced_combat`, `act2_critical_hp_forced_combat`, `act2_no_recovery_buffer`, `act2_no_emergency_potion`, `act2_defense_gap`, and `act2_weak_gap`.
- Route update: `policy_route` consumes those Act 2 conditions as a conservative soft penalty on low-HP paths that force hallway combat before close recovery. Rest, shop, and treasure current nodes are not penalized, and existing severe forced-elite-within-3 penalties are not double-counted.
- Probe76 shadow data was regenerated with static knowledge. The Act 2 F18/F19/F20 `route_risk` rows now carry the new Act2 route-risk flags and recommendations.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_map_act2_probe76_critical_hp_monster_route_is_marked_risky tests.test_policy.PolicyTests.test_map_act2_low_hp_prefers_close_recovery_over_forced_hallway tests.test_policy.PolicyTests.test_map_act2_extreme_low_hp_prefers_question_over_probe61_monster
python -m unittest tests.test_readiness.ReadinessTests.test_act2_context_is_flagged_but_still_scores tests.test_readiness.ReadinessTests.test_act2_low_hp_forced_combat_route_is_flagged
python -m unittest tests.test_policy -k map
python -m unittest tests.test_readiness tests.test_training_manifest
```

2026-07-05 probe77 flow result:

- Flow command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe77.json --log-dir ai_runs_strategy_probe77 --use-all-hardware
```

- Result: `ai_runs_strategy_probe77/20260705_170042_ironclad_a4.jsonl` reached F16 Guardian and ended as synthetic game-over after a lethal transition. It had 210 ok actions, 1 recovered preflight mismatch, and 0 failed actions, so the log is clean trainable.
- Manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe77 --output data\training_manifest_probe77.json --shadow-dir data\shadow_probe77 --knowledge-dir data\static_knowledge
```

- Manifest result: 1 clean trainable log, 0 diagnostic logs, 0 infra-blocked logs, 16 route-risk rows, 5 potion-tempo rows, and 1 pre-boss deck-quality row.
- New evidence: pre-boss readiness at F15 had full HP but no potions and `boss_not_ready` / `boss_no_tempo_potion`, then Guardian killed the run on T14 with 36 incoming and no energy. This points to boss deck/resource quality and boss potion acquisition/use as the next Act 1 boss-side bottleneck, while probe76 remains the Act 2 route-risk bottleneck.

2026-07-05 probe77 boss-prep fix round:

- AI-agent result: prioritize shop/potion boss preparation first, then Guardian evaluator refinements, then broader card-reward changes. Probe77 T14 had no survivable one-turn line because the run entered Guardian without potions and insufficient damage to trigger `Mode Shift` under 36 incoming.
- Card reward fix: in late Act 1 boss-prep windows, the policy now gives boss-damage and boss-defense cards an extra bonus when the deck lacks boss output or usable potions, and applies an extra penalty to unsupported slow engines such as `Dark Embrace`.
- Shop fix: when Act 1 boss preparation needs a potion, high-impact boss-prep potions receive enough score to beat ordinary Strike purge. This targets the probe77 pattern of entering Guardian with `potion_count=0`.
- Learning fix: pre-boss shadow rows now include `boss_potion_gap`, so future models can learn from the explicit boss-tempo resource gap instead of reparsing readiness flags.
- Probe78 was dispatched to the flow agent with current working-tree code:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe78.json --log-dir ai_runs_strategy_probe78 --use-all-hardware
python -m slay_ai.training_manifest ai_runs_strategy_probe78 --output data\training_manifest_probe78.json --shadow-dir data\shadow_probe78 --knowledge-dir data\static_knowledge
```

2026-07-05 probe74 multi-agent flow/tempo validation round:

- User direction: make the multi-agent collaboration genuinely parallel:
  - one agent runs process/probes and accumulates climb information;
  - one agent handles program/script optimization;
  - one agent handles AI/model/algorithm upgrades;
  - main agent integrates, tests, commits, and pushes.
- Main-thread role assignment commands:

```text
multi_agent_v1.send_input(Archimedes, "You are the flow/run-data agent. Read probe74, confirm/generate manifest/shadow data, summarize process stability/death resources/route/potion misses, and recommend probe75. Do not edit code.")
multi_agent_v1.send_input(Mill, "You are the program optimization agent. Inspect current diff and probe74, look for execution/strategy bugs that can explain F10 death, make only small scoped code/test changes if needed.")
multi_agent_v1.send_input(Harvey, "You are the AI upgrade/algorithm agent. Evaluate probe74 and shadow data, propose or implement one small measurable Stage 4/5 upgrade, and answer how online/static knowledge should combine with logs.")
multi_agent_v1.send_input(Halley, "You are the architecture/coordination agent. Audit the multi-agent division of labor, merge cadence, MCP need, and write a short SOP.")
```

- Flow result: `ai_runs_strategy_probe74/20260705_161424_ironclad_a4.jsonl` was clean trainable: 160 ok actions, 0 failed actions, 0 recovered actions, F10 game-over. The death was not an MCP/action-loop blocker. The run reached F10 after the route fix, improving over probe73's F6 Sentries death, but still died before the Act 1 boss.
- Probe74 evidence: F10 Jaw Worm + Acid Slime killed the run. Death sequence showed low HP, repeated high incoming, and unused `CultistPotion`. Static knowledge initially treated `CultistPotion` as unknown, so potion-tempo shadow rows could not learn its role.
- Static knowledge fix: `data/static_knowledge/potions.json` now includes `Cultist Potion` with scaling, boss-damage, elite-tempo, and long-fight roles. The enriched probe74 shadow rows now show `potion_known_count=1` and `potion_role_scaling=1` instead of `potion_unknown_count=1`.
- Policy/readiness fix: Cultist is now a high-impact route/readiness potion. Combat may use Cultist/Strength/Steroid scaling potions in dangerous early multi-enemy hallway fights, and Cultist is included in emergency tempo tokens for low-HP danger turns. A safe-short-hallway regression keeps it from being spent too freely.
- Manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe74 --output data\training_manifest_probe74.json --shadow-dir data\shadow_probe74 --knowledge-dir data\static_knowledge
```

- Probe75 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe75.json --log-dir ai_runs_strategy_probe75 --use-all-hardware
```

- Probe75 result: `ai_runs_strategy_probe75/20260705_162852_ironclad_a4.jsonl` ended clean at F12 with 129 ok actions, 1 recovered preflight mismatch, and 0 failed actions. It got past probe74's F10 hallway death and died in the second elite/multi-enemy pressure sequence. No potion-tempo rows were generated because the run carried no potion during incoming-damage combat states.
- Probe75 manifest command:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe75 --output data\training_manifest_probe75.json --shadow-dir data\shadow_probe75 --knowledge-dir data\static_knowledge
```

- Readiness v3 evidence: probe74 F8/F9 now flags `act1_low_buffer_no_recovery`, `hallway_no_immediate_tempo_potion`, and `hallway_lacks_premium_block` despite having `CultistPotion`, because Cultist is scaling tempo rather than immediate hallway defense. Probe75 F11 flags `aoe_missing`, `weak_missing`, `elite_potion_missing`, and `elite_not_ready` before the second elite path.
- Halley SOP: Archimedes owns probe logs/classification/death summaries, Mill owns execution and small code fixes, Harvey owns AI/model/readiness suggestions as shadow-first changes, Halley audits coordination. Main thread integrates in order: data classification, execution blockers, one behavior change, shadow AI connection. Trigger architecture review after two same-type clean failures, any execution failure loop, any promotion of model/readiness into control, or any broad refactor.

2026-07-05 probe73 route/readiness validation round:

- Flow agent command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_parallel_flow.json --log-dir ai_runs_parallel_flow --use-all-hardware
```

- Flow result: target was still `IRONCLAD:A4`; the run ended as a clean F10 Sentries game-over with 215 action records, 1 recovered preflight mismatch, and 0 failed actions. It supported the route/resource hypothesis: at F7 the run had 37/88 HP, then the path was locked into `T -> E`.
- Probe73 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe73.json --log-dir ai_runs_strategy_probe73 --use-all-hardware
```

- Probe73 result: target was still `IRONCLAD:A4`; the run ended as a clean F6 Sentries game-over. F3 had a shop option, but the policy chose a no-buffer forced-elite monster path because the old no-buffer penalty was not strong enough at high HP.
- Route fix: route lookahead horizon increased to 6, route feature logs now include `forced_elite_within_5` and `forced_combat_within_4`, and high-HP Act 1 paths that force an elite within 3 nodes without rest/shop buffer receive a stronger penalty. Regression tests: `test_map_parallel_flow_avoids_deep_no_buffer_forced_elite` and `test_map_probe73_prefers_shop_over_healthy_no_buffer_forced_elite`.
- Readiness fix: `training_manifest --knowledge-dir` now writes readiness scores, gaps, flags, and recommendations into route-risk and pre-boss shadow rows. In the flow-agent run, F7/F9 route rows show `low_hp`, `aoe_missing`, and `weak_missing`.
- Validation:

```powershell
python -m unittest tests.test_policy -k map
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe72 clean manifest, Weak modeling, and route assessment round:

- Trigger evidence from probe71: Hexaghost T9 had `Clothesline` in hand against a 6x5 attack, but combat search did not model Weak, so it undervalued a line that reduced the current turn's multi-hit damage.
- Search fix: `combat_search` now treats supported Weak cards as search candidates, applies Weak to targeted or all-enemy cards, respects Artifact, and reduces multi-hit attacks per hit when possible.
- Runner fix: a lingering terminal `GAME_OVER` screen can block `start_game` with `Possible commands: [proceed]`; the runner now proceeds once and retries the start.
- Training fix: `slay_ai.training_manifest` classifies logs before learning and extracts route-risk, potion-tempo, and pre-boss deck-quality shadow rows. `slay_ai.learn` and `slay_ai.train_card_model` accept `--manifest`.
- Probe72 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe72.json --log-dir ai_runs_strategy_probe72 --use-all-hardware
```

- Probe72 result: target was still `IRONCLAD:A4`. It reached F11 and ended as clean game over at step 185, with 0 failed actions. Weak-aware search live-triggered, but the run still died with potion/resource tempo unresolved.
- Clean manifest and shadow-data commands:

```powershell
python -m slay_ai.training_manifest ai_runs_strategy_probe64 ai_runs_strategy_probe65 ai_runs_strategy_probe66 ai_runs_strategy_probe67 ai_runs_strategy_probe68 ai_runs_strategy_probe69 ai_runs_strategy_probe70 ai_runs_strategy_probe71 ai_runs_strategy_probe72 --output data\training_manifest_probe64_72.json --shadow-dir data\shadow_probe64_72
python -m slay_ai.train_card_model --manifest data\training_manifest_probe64_72.json --model-path models\card_value_model_probe64_72.json --min-count 1 --max-delta 6
```

- Manifest result: 9 clean trainable logs, 0 diagnostics, 0 infra-blocked. Shadow rows: 128 route risk, 484 potion tempo, 7 pre-boss deck quality. The local model artifact under `models/` is ignored and can be regenerated.
- Multi-agent route evaluation commands:

```text
multi_agent_v1.send_input(Harvey, "Evaluate whether heuristic-only is hitting a ceiling and whether clean manifest + shadow models is the right route.")
multi_agent_v1.send_input(Mill, "Evaluate the route from MCP/script reliability and observability.")
multi_agent_v1.send_input(Halley, "Evaluate architecture and multi-agent work mode after Act 1 instability.")
multi_agent_v1.send_input(Archimedes, "Give an independent assessment of the most urgent decision gap.")
```

- Validation:

```powershell
python -m unittest tests.test_training_manifest tests.test_runner.RunnerTests.test_start_recovers_terminal_game_over_before_new_run tests.test_policy.PolicyTests.test_combat_search_models_probe71_clothesline_weak_under_lethal_hexaghost
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe71 search sequence commitment and route assessment round:

- Trigger evidence from probe70: one-turn search found full defensive lines, but the policy only returned the first card. Subsequent state reads could replan into attacks even when the original sequence needed follow-up block.
- Search commitment fix: `combat_search.SearchResult` now returns `sequence_card_keys`, and `HeuristicPolicy` stores the remaining normalized card-id sequence for the same floor and combat turn. Pending commitments continue only if the card is still playable with enough energy, recompute attack targets from the current monster state, clear on turn/screen/hand mismatch, and avoid unnecessary block-only continuations once current block already covers incoming.
- Regression tests: `test_combat_commits_defensive_search_sequence_across_state_reads` and `test_combat_search_sequence_commitment_clears_on_new_turn`.
- Probe71 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe71.json --log-dir ai_runs_strategy_probe71 --use-all-hardware
```

- Probe71 result: target was still `IRONCLAD:A4`. It reached F16 Hexaghost and ended as game over at step 235. Action execution stayed clean enough for training: 234 action records, 233 `ok`, 1 recovered `preflight_mismatch` at a reward transition, and 0 `action_failed`. Search sequence commitment live-triggered 17 times. The final failure was strategic: Hexaghost T9 had 17 HP, 12 block, 30 incoming, no potions, and 51 boss HP remaining.
- Multi-agent route evaluation commands:

```text
multi_agent_v1.send_input(Halley, "Evaluate whether the current staged route is reasonable after probe71; do not modify files.")
multi_agent_v1.send_input(Archimedes, "Evaluate the current AI/learning route after probe71; do not modify files.")
multi_agent_v1.spawn_agent(Mill, "Respawn the game mod/MCP script senior engineer for read-only route assessment.")
```

- Agent result: Halley and Archimedes agreed the current route is correct. Keep the heuristic/search controller, continue Stage 4 search planning and card/deck/resource evaluation, and keep learned models as shadow scorers or tie-breakers. The attempted Mill respawn failed because the active sub-agent thread limit was reached; keep the pending respawn note below.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_commits_defensive_search_sequence_across_state_reads tests.test_policy.PolicyTests.test_combat_search_sequence_commitment_clears_on_new_turn tests.test_policy.PolicyTests.test_combat_local_search_ignores_spurious_non_attack_damage_after_probe69 tests.test_policy.PolicyTests.test_combat_accepts_probe69_defend_under_extreme_slime_pressure tests.test_policy.PolicyTests.test_combat_local_search_triggers_guardian_mode_shift_to_stop_attack
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe70 non-attack damage modeling and pressure-block acceptance round:

- Trigger evidence from probe69: Slime Boss T7 showed `Battle Trance`, `Defend_R`, and `Slimed` carrying `damage: 1` in MCP snapshots. The policy treated non-attacks as tiny attacks, causing `Defend_R` to be penalized under pressure and allowing `Slimed`/draw cards to consume energy before block.
- Damage modeling fix: `combat_search` and `policy` now count card `damage` only for cards whose type is `ATTACK`. This mirrors the earlier attack-block fix and prevents status/skill card field noise from entering combat scoring.
- Search acceptance fix: under extreme incoming pressure, a block-first search line can override single-card attack scoring even when it only reduces loss modestly. This is needed for Slime Boss and Hexaghost turns where every block point changes survival margins.
- Regression tests: `test_combat_local_search_ignores_spurious_non_attack_damage_after_probe69` and `test_combat_accepts_probe69_defend_under_extreme_slime_pressure`.
- Probe70 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe70.json --log-dir ai_runs_strategy_probe70 --use-all-hardware
```

- Probe70 result: target was still `IRONCLAD:A4`. It reached F16 Hexaghost and ended as synthetic game over at step 203. Action execution stayed clean: 202 action records, 0 recovered action errors, 0 failed actions. The run used stronger block-first lines in Sentries, Fungi, and Hexaghost, but it still exposed that one-turn search commits only one action; after the first block card, later replans can drift back to attacks instead of preserving the full defensive line.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_local_search_ignores_spurious_non_attack_damage_after_probe69 tests.test_policy.PolicyTests.test_combat_accepts_probe69_defend_under_extreme_slime_pressure tests.test_policy.PolicyTests.test_combat_local_search_triggers_guardian_mode_shift_to_stop_attack tests.test_policy.PolicyTests.test_combat_local_search_continues_block_after_probe59_impervious
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe69 low-HP Skill Potion validation and Slime Boss pressure round:

- Trigger evidence from probe68: F21 Snecko had 10/80 HP, 27 incoming, and both `SkillPotion` and `LiquidMemories` still unused. The policy played available cards and died instead of first using Skill Potion to look for defense.
- Potion fix: emergency potion logic now treats `SkillPotion` as a defensive emergency resource, but only under `defensive_danger`. This avoids spending it in low-HP but no-incoming turns.
- Regression test: `test_combat_uses_skill_potion_under_probe68_snecko_lethal_pressure` recreates the F21 Snecko lethal-pressure state and expects `use_potion` slot 1.
- Probe69 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe69.json --log-dir ai_runs_strategy_probe69 --use-all-hardware
```

- Probe69 result: target was still `IRONCLAD:A4`. It reached F16 Slime Boss and ended as synthetic game over at step 337. Action execution stayed clean: 336 action records, 0 recovered action errors, 0 failed actions. Skill Potion did not live-trigger because this seed did not carry it into a lethal turn. New evidence: after Slime Boss split, the run faced 30 then 38 incoming while low on block, with `Slimed` hand pollution and fallback actions that did not prevent lethal pressure.
- Agent input:

```text
multi_agent_v1.send_input(Archimedes, "Evaluate the SkillPotion defensive_danger fix and whether to prioritize LiquidMemories, Act 2 low-HP route, or high-pressure potion/card-sequence search.")
```

- Agent assessment: the Skill Potion patch is correctly scoped. Do not keep adding potion-name rules blindly. Next priority should be high-pressure short-horizon search that can evaluate card order plus potions together. `LiquidMemories` should be added later as a search action once discard-pile/GRID context is reliable.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_uses_skill_potion_under_probe68_snecko_lethal_pressure tests.test_policy.PolicyTests.test_combat_uses_gamblers_brew_as_emergency_tempo tests.test_policy.PolicyTests.test_combat_does_not_spend_gamblers_brew_when_safe tests.test_policy.PolicyTests.test_combat_does_not_use_block_potion_at_low_hp_without_incoming
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe68 Guardian Mode Shift search and route assessment round:

- Trigger evidence from probe67: Guardian T2 had a playable line that could deplete `Mode Shift` and cancel 36 incoming damage, but the old search treated Mode Shift as ordinary HP damage and chose block instead. The run also showed that route fixes were now good enough to reach F16, so the next high-value Stage 4 work was combat evaluator fidelity rather than a broad autonomous-learning jump.
- Search fix: `combat_search` now carries a per-monster `mode_shift` counter and clears the current attack when enough unblocked damage reaches zero. Single-card intent-stop logic also treats Guardian Mode Shift as an attack-stopping outcome.
- Regression test: `test_combat_local_search_triggers_guardian_mode_shift_to_stop_attack` recreates the post-Bash Guardian state and expects the search to open with `Reckless Charge` instead of settling for block.
- Probe68 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe68.json --log-dir ai_runs_strategy_probe68 --use-all-hardware
```

- Probe68 result: target was still `IRONCLAD:A4`. It reached F21 Snecko and ended as clean game over at step 281. Guardian was beaten from 2 HP via search-assisted lethal, but the run entered Act 2 with too little buffer and died with two potions unused. This supports the current route: keep Stage 4 search/evaluator work first, and feed clean logs into lightweight learning later.
- Multi-agent route evaluation:

```text
multi_agent_v1.send_input(Halley, "Evaluate whether the current staged route is reasonable; do not modify files.")
multi_agent_v1.spawn_agent(Archimedes, "Evaluate the route from a game AI / learning model perspective; do not modify files.")
```

- Agent consensus: the route is correct. Do not let autonomous learning take over yet. First finish Stage 4 search, route/resource evaluation, and a regression probe set. Learned models should remain shadow rankers, tie-breakers, or high-confidence advisors until search produces reliable labels and live probes stop regressing.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_local_search_triggers_guardian_mode_shift_to_stop_attack tests.test_policy.PolicyTests.test_combat_local_search_counts_move_damage_hits tests.test_policy.PolicyTests.test_combat_local_search_continues_block_after_probe59_impervious
python -m unittest tests.test_policy -k guardian
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe67 no-buffer forced elite route tuning round:

- Trigger evidence from probe66: F2 had a choice between `M` with no rest/shop buffer before a forced elite and `?` with a later rest path. The old scores were `M` 40 versus `?` 38, so the policy chose `M`, locked into F6 Sentries, left the elite at 28/80 HP, and died in the next hallway fight.
- Route fix: the Act 1 no-buffer forced elite penalty was increased from `22.0` to `32.0`. This keeps healthy elite routes available, but lets a lower-base-score question path win when it avoids the no-buffer lock.
- Regression test: `test_map_probe66_prefers_question_over_no_buffer_forced_elite` recreates the F2 choice and expects the question route.
- Probe67 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe67.json --log-dir ai_runs_strategy_probe67 --use-all-hardware
```

- Probe67 result: target was still `IRONCLAD:A4`. It reached F16 Guardian and ended as game over at step 248. The route went through a healing event, rest, shop, and second elite, showing route forward progress compared with probe66. New evidence: Lagavulin/Sentries still bleed too much HP, Guardian T2 did 31 damage after only 5 block, and F11 had one recoverable action race where a planned `Flame Barrier` play hit an MCP target requirement mismatch.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_map_probe66_prefers_question_over_no_buffer_forced_elite tests.test_policy.PolicyTests.test_map_probe63_prefers_shop_buffer_before_forced_elite tests.test_policy.PolicyTests.test_map_healthy_act1_can_still_choose_monster_over_shop tests.test_policy.PolicyTests.test_map_healthy_act1_can_still_choose_monster_over_question
python -m unittest tests.test_policy -k map
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe65/probe66 Slime split and slow-engine pressure round:

- Trigger evidence from probe64: Slime Boss was pushed from 80 to 67 HP with only 1 energy and no follow-up, creating two 67 HP large slimes and later lethal split/minion pressure. The same run also played a zero-energy `Whirlwind`, which consumed the card without meaningful damage.
- Slime split fix: single-card scoring now penalizes shallow no-follow-up splits when there is no current incoming pressure. It does not penalize a split that stops an active attack, so the existing large-slime defensive split behavior remains valid.
- X-cost fix: single-card combat no longer treats zero-energy X-cost attacks as valuable damage.
- Probe65 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe65.json --log-dir ai_runs_strategy_probe65 --use-all-hardware
```

- Probe65 result: target was still `IRONCLAD:A4`. It reached F16 Hexaghost and ended as synthetic game over at step 274. Because the boss was Hexaghost, this did not live-validate the Slime Boss split fix. It exposed a new concrete pressure issue: at F12/F14/F16 the policy still played `Dark Embrace` before available block under dangerous incoming pressure.
- Slow-engine fix: `Dark Embrace` is now treated like other slow engine cards under dangerous pressure, so immediate block can win when HP/incoming make the engine unsafe.
- Probe66 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe66.json --log-dir ai_runs_strategy_probe66 --use-all-hardware
```

- Probe66 result: target was still `IRONCLAD:A4`. It ended as a clean synthetic lethal failure at F7 after F6 Sentries left the run at 28/80 HP and the next hallway fight finished the run. This validates execution stability after the pressure fixes but not Slime Boss live behavior.
- Agent input:

```text
multi_agent_v1.send_input(Harvey, "Give the next minimal high-leverage Slime Boss strategy suggestions; do not modify files.")
multi_agent_v1.send_input(Halley, "Evaluate whether this round should be a Slime Boss-specific strategy or a generic combat_search evaluator extension; do not modify files.")
```

- Agent consensus: keep this as a Stage 4 evaluator/search problem. Use Slime Boss fixtures, but avoid a broad hard-coded boss strategy. Next likely work is a small evaluator for multi-target pressure and split/future-pressure risk, plus route/deck evaluation for early forced elite damage.
- Validation:

```powershell
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe64 forced-elite buffer and attack-block modeling round:

- Route assessment: the overall project route is still correct. Continue the current loop of fresh frontier probe, concrete failure analysis, narrow fix, regression test, and fresh validation. Do not jump to pure autonomous learning or a broad relocation refactor yet; Stage 4 search/planning is now the main bottleneck.
- Trigger evidence from probe63: F3 offered a question-mark path with a forced elite and no buffer versus a shop/rest-buffered path. The old policy chose the forced-elite path. In the later Lagavulin fight, MCP snapshots after Speed/Dexterity effects exposed generic attacks with a positive `block` field, so the local search treated attacks such as Strike/Thunderclap as defensive plays.
- Route fix: Act 1 forced elites within 3 nodes now receive a penalty when there is no nearby rest/shop buffer and a bonus when the path offers a shop/rest buffer before the elite. The regression test recreates the probe63 F3 choice and expects the buffered shop path.
- Combat modeling fix: combat search and single-card scoring now ignore attack-card `block` values unless the attack is a known attack+block card (`Dash`, `Iron Wave`, `Just Lucky`, or `Wallop`). The regression test recreates the probe63 Lagavulin Speed Potion state and expects a real Defend instead of a bogus-block attack.
- Probe64 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe64.json --log-dir ai_runs_strategy_probe64 --use-all-hardware
```

- Probe64 result: target was still `IRONCLAD:A4`. It reached F16 Slime Boss and ended as synthetic game over at step 223 after likely lethal transition. The run reached the boss at 82/88 HP, then died after split/minion pressure with low block and incoming 24 at 16 HP. The next bottleneck is Slime Boss split timing, multi-target pressure, Slimed hand pollution, and defensive search under boss pressure.
- Multi-agent route evaluation commands:

```text
multi_agent_v1.send_input(Harvey, "Evaluate the current route from the game AI/autonomous-learning perspective; do not modify files.")
multi_agent_v1.send_input(Mill, "Evaluate the current route from the MCP/script engineering perspective; do not modify files.")
multi_agent_v1.send_input(Halley, "Evaluate the current route from the long-term architecture/work-mode perspective; do not modify files.")
multi_agent_v1.wait_agent(targets=[Harvey, Mill, Halley], timeout_ms=60000)
```

- Agent consensus: keep heuristic control, keep learned models in shadow/tie-breaker roles, finish Stage 4 before expanding Stage 5, and avoid a broad refactor while strategy evidence is moving. The next three priorities are Slime Boss split strategy, post-split small-slime pressure search, and Slimed/defense/potion handling under high incoming.
- Validation:

```powershell
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe62/probe63 low-HP route and Hemokinesis self-damage round:

- Trigger evidence from probe61: F20 MAP at HP 21/80 offered `?` and `M`. Both options had the same `lookahead_adjustment` of `-145.0`, but the policy chose immediate `M` because monster base score was higher. Harvey recommended route-risk first and combat safety margin second; Halley recommended continuing strategy small fixes rather than moving to `action_executor`.
- Route fix: Act 2 low-HP route scoring now penalizes immediate `M` when `?` is available. The regression test recreates the probe61 equal-lookahead-risk case and expects the question mark route.
- Probe62 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe62.json --log-dir ai_runs_strategy_probe62 --use-all-hardware
```

- Probe62 result: target was still `IRONCLAD:A4`. It died on F11 after a forced-combat route from F10 at 8/80 HP. The direct strategy bug was F11 HP 2/80: the policy played nonlethal `Hemokinesis` while block already covered incoming, which likely caused the final lethal transition.
- Hemokinesis fix: `combat_search` now includes self-damage in projected loss and adds low remaining-HP safety margin penalties unless the fight ends. `policy.py` also models `Hemokinesis` as a 2 HP cost card, heavily penalizing nonlethal low-HP use while preserving safe lethal use.
- Probe63 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe63.json --log-dir ai_runs_strategy_probe63 --use-all-hardware
```

- Probe63 result: target was still `IRONCLAD:A4`. It reached F8 Lagavulin and ended as synthetic game over after likely lethal transition. It did not repeat the HP2 `Hemokinesis` bug; the next bottleneck is path commitment into a forced elite at 44/88 HP and Lagavulin defensive planning.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_local_search_counts_hemokinesis_self_damage tests.test_policy.PolicyTests.test_combat_avoids_nonlethal_hemokinesis_at_two_hp tests.test_policy.PolicyTests.test_combat_can_play_hemokinesis_for_safe_lethal tests.test_policy.PolicyTests.test_map_act2_extreme_low_hp_prefers_question_over_probe61_monster
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

2026-07-05 probe61 self-damage engine risk round:

- Trigger evidence: `ai_runs_strategy_probe60_continue_act2/20260705_125715_ironclad_a0.jsonl` resumed the Guardian continuation into Act 2 and died on F20 Byrds. The critical state was Act 2 floor 20, HP 49/80, three Byrds on `BUFF`, incoming 0, and two `Offering` cards in hand. The policy played `Offering` twice before building defense, dropping to 37 HP before the Byrds' multi-hit turns.
- Agent route review conclusion: Harvey, Mill, and Halley agreed the overall route is correct: keep heuristic control, keep learned models as shadow/tie-breakers, continue evidence-driven probes, and interleave thin-slice refactors instead of pausing for a project-wide relocation.
- Fix: `policy.py` now penalizes immediate self-damage engine cards (`Offering`, `Bloodletting`) in Act 2 multi-enemy setup turns when HP is below the safety threshold and a safe non-self-damage play exists. It also penalizes unsafe current-turn repeated self-damage activity. Tests preserve the counter-case where high-HP `Offering` remains playable.
- Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_avoids_offering_in_probe60_byrds_setup tests.test_policy.PolicyTests.test_combat_avoids_second_offering_after_turn_activity tests.test_policy.PolicyTests.test_combat_can_play_offering_when_safe tests.test_policy.PolicyTests.test_combat_can_play_burning_pact_when_safe tests.test_policy.PolicyTests.test_combat_avoids_combust_under_low_hp_pressure
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

- MCP recovery before clean validation:

```powershell
python -m slay_ai.mcp_watchdog --json
@'
from slay_ai.mcp.client import MCPClient
client = MCPClient(timeout=10)
client.ensure_initialized()
print(client.execute_actions([{"action": "proceed"}]))
'@ | python -
python -m slay_ai.mcp_watchdog --json
```

- Probe61 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe61.json --log-dir ai_runs_strategy_probe61 --use-all-hardware
```

- Probe61 result: target was still `IRONCLAD:A4`. It reached F21 Act 2 and ended as a synthetic game over at step 370 after likely lethal MCP null-state transition. It beat Guardian at low HP, reached F18 with 80/80 HP after the chest, then exposed a new bottleneck: low-HP Act 2 hallway routing and high-pressure defense/search calibration. The run had recoverable action races but no unrecovered action failure.

2026-07-05 probe60 Slime Boss pressure and HAND_SELECT multi-choose round:

- Probe60 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe60.json --log-dir ai_runs_strategy_probe60 --use-all-hardware
```

- Strategy fix before probe60: add regression tests from probe59 Slime Boss states. Local search can now accept an already-blocking follow-up block sequence when it reduces at least 5 projected damage, and X-cost attacks are penalized under dangerous pressure when they spend all energy while a playable block card remains and the attack does not kill or split the attacker.
- Probe60 result: the run reached F16 Guardian with 71/80 HP. It exposed a multi-card `HAND_SELECT` execution gap after Gambler's Brew: the policy emitted `select_cards drop [5, 3, 4, 2, 1]`, but MCP exposed only `choose` and `proceed`, causing repeated `preflight_mismatch` recoveries. The run was manually stopped as diagnostic evidence, so do not include it in default training.
- Runner fix: `_rewrite_hand_select_to_choose` now rewrites single-card drops to one `choose`, and multi-card drops to ordered `choose` actions plus `proceed` when MCP exposes `choose/proceed`.
- Continuation validation command:

```powershell
python -m slay_ai.runner --max-steps 80 --interval 0.08 --startup-timeout 5 --log-dir ai_runs_strategy_probe60_continue_handselect_multichoose
```

- Continuation result: first action rewrote `select_cards drop [5, 3, 4, 2, 1]` to five `choose` actions plus `proceed`, logged `executed_actions` and `rewrite_reason`, beat Guardian, entered Act 2, and reached F18 MAP before the 80-step continuation limit.
- Validation: `python -m unittest discover -s tests` ran 154 tests OK; `python -m compileall slay_ai tests` OK.

2026-07-05 architecture-route evaluation round:

- User question: evaluate whether the current route is correct and reasonable, given the proposed target structure (`app/`, `core/`, `mcp/`, `domain/`, `policy/`, `search/`, `learning/`, `campaign/`, `tests/`) and the current probe-driven workflow.
- Joint conclusion: the strategic direction is correct, but full project-wide relocation should not happen in one step. Keep the live evidence loop running and do thin-slice refactors with unchanged behavior.
- Faraday / Harvey: keep heuristic control, supervised learning as shadow/tie-breaker, and prioritize combat local search, route-risk data, and learning features before larger RL or full policy replacement.
- Mill / Hegel: the MCP/action boundary is the highest-value refactor area. Next safe cut is `core/action_executor.py`, moving preflight, settle, command alias/rewrite, and recovery out of `runner.py` without changing behavior.
- Halley / Descartes: avoid creating `slay_ai/policy/` while `slay_ai/policy.py` is still the public import target. Prefer flat compatibility-preserving modules and one architecture boundary per validation round.

Main-thread commands for this review round:

```text
multi_agent_v1.send_input(Harvey, "Evaluate current route from the game AI/autonomous-learning perspective; do not modify files.")
multi_agent_v1.send_input(Mill, "Evaluate current route from the Slay the Spire mod/MCP/automation engineering perspective; do not modify files.")
multi_agent_v1.send_input(Halley, "Evaluate current route from the long-term architecture/work-mode perspective; do not modify files.")
multi_agent_v1.wait_agent(targets=[Harvey, Mill, Halley], timeout_ms=60000)
```

2026-07-05 probe58/probe59 shop-potion validation round:

- Probe58 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe58.json --log-dir ai_runs_strategy_probe58 --use-all-hardware
```

- Probe58 result: `IRONCLAD:A4` died on F7 Gremlin Nob. Evidence: F3 shop bought Shrug It Off and purged Strike, then left with 154 gold while Swift, Regen, and Duplication potions were available. The shop high-impact potion token list did not include those potions.
- Fix: add Duplication, Energy, Gambler's Brew, Regen, and Swift potion tokens to shop high-impact potion scoring and add a regression test for the probe58 shop state.
- Probe59 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe59.json --log-dir ai_runs_strategy_probe59 --use-all-hardware
```

- Probe59 result: `IRONCLAD:A4` reached F16 Slime Boss and ended as synthetic game-over at step 219 after a likely lethal transition. Evidence: F13 shop bought a Speed Potion after purge; F14 Lagavulin fight used Essence of Steel and Speed Potion, then survived the elite. The new bottleneck is Slime Boss split/minion pressure, especially Slimed hands and hand-changing/search coverage after the split.
- Learning update after adding probe58/probe59: `slay_ai.train_card_model` loaded 258 card-pick examples and wrote 70 card deltas; `slay_ai.learn --reset` read 53 logs, applied 42 completed runs, and skipped 11 incomplete runs.

2026-07-05 map-lookahead review round:

- Harvey / Faraday recommended prioritizing map observation plus 3-4 layer path-commitment risk before four-character policy splits or larger learning authority. Rationale: recent probe54/55 failures were route commitments into forced elite risk, and this affects every character.
- Mill / Hegel recommended keeping `map` out of the default state include, using a best-effort MAP-only request, preserving current `next_nodes` choice indices for actions, logging observation status, and adding timeout/failure fallback because `get_game_state(..., "map")` can return `Internal error:null`.
- Halley / Descartes recommended doing this inside the existing `policy_route.py` boundary rather than continuing broad policy refactors, with tests proving old immediate route fallback remains intact when full-map data is missing.

Main-thread commands for this review round:

```text
multi_agent_v1.send_input(Harvey, "Evaluate whether to prioritize map observation + 3-4 layer route lookahead or four-character/light-model work.")
multi_agent_v1.send_input(Mill, "Evaluate MCP get_game_state map boundaries, best-effort fallback, logging, and failure handling.")
multi_agent_v1.send_input(Halley, "Evaluate architecture fit for map observation + route risk versus continuing policy splits.")
```

2026-07-05 probe56 review round:

- Probe command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe56.json --log-dir ai_runs_strategy_probe56 --use-all-hardware
```

- Result: `IRONCLAD:A4` reached F16 Hexaghost and ended as a synthetic game-over at step 258. `map_observation` succeeded on all 17 MAP records; route evaluation parsed 54 nodes and 65 edges.
- Route evidence: the map lookahead was active and logged route adjustments, including low-HP F12/F13 recovery-path bonuses. Route is no longer the immediate bottleneck for this run.
- Combat evidence: the bot entered Hexaghost with `DuplicationPotion` and `SpeedPotion`, used Speed Potion on T2, but never used Duplication Potion before dying. This made boss-fight resource use the next concrete fix.
- Harvey / Faraday recommended prioritizing boss combat potion/defense strategy over rest/smith threshold changes; F15 `67/88 smith` into Hexaghost is not strong evidence of a rest bug because Hexaghost's large attack scales with HP.
- Mill / Hegel recommended not prioritizing the two recovered action races this round. They did not cause `action_failed` or `max_steps`; if repeated, next small fixes are HAND_SELECT empty-drop avoidance and REST/GRID transition logging.

Main-thread commands for this review round:

```text
multi_agent_v1.send_input(Harvey, "Evaluate probe56 boss death: rest/smith, boss potion/defense, or earlier route/elite greed?")
multi_agent_v1.send_input(Mill, "Evaluate probe56 recovered action races and whether runner/preflight needs this-round repair.")
```

2026-07-05 probe57 validation round:

- Probe command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe57.json --log-dir ai_runs_strategy_probe57 --use-all-hardware
```

- Result: `IRONCLAD:A4` reached F16 Hexaghost and ended as a synthetic game-over at step 221. `map_observation` succeeded on all 16 MAP records.
- Potion evidence: the bot used Steroid Potion early against Gremlin Nob, Dexterity Potion in a dangerous multi-gremlin fight, and Fire Potion to finish Lagavulin. This supports the direction of earlier boss/elite potion spending.
- New bottleneck evidence: the bot entered Hexaghost with `GamblersBrew`, reached T9 at 9/80 HP with high incoming, and still died while keeping the potion. `GamblersBrew` was added to emergency tempo potion handling for low-HP/high-incoming turns.
- Execution note: one recovered action race occurred after combat transition (`end_turn` became invalid while `choose/proceed` were available). It did not stop the run.

2026-07-05: the first same-directory `fork_thread` attempt is deprecated. Those forked threads inherited too much active context and some began trying to create further sub-agents. Do not reuse these deprecated ids as long-lived agents: `019f2e87-0fb4-7660-a04c-698631fb1273`, `019f2e87-b470-7460-9ad5-af23949fef82`, `019f2e87-d1f1-7e92-970f-4860eb0ddbfe`, `019f2e88-9657-7bd3-9d9d-1b514b2d3c91`, `019f2e88-c96a-7c53-af23-eddae11c858a`, `019f2e89-1184-7fc2-94ed-4ba1f424d420`.

2026-07-05: current valid long-lived agents were created as clean project threads with explicit role prompts and hard constraints: no thread creation, no file edits unless explicitly handed off, and initial output only.

```text
Faraday -> 019f2e8b-0604-7442-8554-bbfdb9ee8513
Hegel -> 019f2e8b-50cf-7340-a53b-ddb3fc2e8caf
Descartes -> 019f2e8b-8c52-7df3-8341-bd8741106808
```

Actual startup tool calls used by the main thread:

```text
codex_app.create_thread({
  "target":{"type":"project","projectId":"C:\\Users\\jikun\\Documents\\game","environment":{"type":"local"}},
  "thinking":"high",
  "prompt":"Faraday clean long-lived strategy advisor prompt; no thread creation; no file edits unless explicitly handed off."
})
codex_app.set_thread_title({"threadId":"019f2e8b-0604-7442-8554-bbfdb9ee8513","title":"Faraday - Clean Strategy Agent"})
codex_app.set_thread_pinned({"threadId":"019f2e8b-0604-7442-8554-bbfdb9ee8513","pinned":true})

codex_app.create_thread({
  "target":{"type":"project","projectId":"C:\\Users\\jikun\\Documents\\game","environment":{"type":"local"}},
  "thinking":"high",
  "prompt":"Hegel clean long-lived MCP/execution advisor prompt; no thread creation; no file edits unless explicitly handed off."
})
codex_app.set_thread_title({"threadId":"019f2e8b-50cf-7340-a53b-ddb3fc2e8caf","title":"Hegel - Clean MCP Execution Agent"})
codex_app.set_thread_pinned({"threadId":"019f2e8b-50cf-7340-a53b-ddb3fc2e8caf","pinned":true})

codex_app.create_thread({
  "target":{"type":"project","projectId":"C:\\Users\\jikun\\Documents\\game","environment":{"type":"local"}},
  "thinking":"high",
  "prompt":"Descartes clean long-lived architecture reviewer prompt; no thread creation; no file edits unless explicitly handed off."
})
codex_app.set_thread_title({"threadId":"019f2e8b-8c52-7df3-8341-bd8741106808","title":"Descartes - Clean Architecture Reviewer"})
codex_app.set_thread_pinned({"threadId":"019f2e8b-8c52-7df3-8341-bd8741106808","pinned":true})
```

2026-07-05: current visible `multi_agent_v1` long-lived agents in the sub-agent panel at that moment:

```text
Faraday / Harvey -> 019f2e94-552b-7301-9cc5-21eed1fc2949
Hegel / Mill -> 019f2f5c-fae1-7cb2-bea6-b8574085839d
Descartes / Halley -> 019f2f5d-0f0e-7612-86d2-d4bb3aeee828
Turing -> spawn on demand; old id 019f2e97-8cb7-7e93-9805-18bfd8738003 was not callable on the probe36 review cycle
```

2026-07-05: current joint evaluation was requested from the three visible long-lived sub-agents after probe46 exposed the preflight loop.

```text
multi_agent_v1.send_input(target="019f2e94-552b-7301-9cc5-21eed1fc2949", interrupt=true, task="Evaluate learning/AI priorities after probe46.")
multi_agent_v1.send_input(target="019f2f5c-fae1-7cb2-bea6-b8574085839d", interrupt=true, task="Evaluate runner/MCP confirm/proceed preflight fix.")
multi_agent_v1.send_input(target="019f2f5d-0f0e-7612-86d2-d4bb3aeee828", interrupt=true, task="Evaluate main-agent workflow and multi-agent division of labor.")
multi_agent_v1.wait_agent(targets=[Harvey, Mill, Halley], timeout_ms=300000)
```

Joint result:

- Faraday / Harvey: pause expansion of learning authority until execution logs are clean; route, potion, shop, and rest/smith learning remain good next targets after MCP/action stability.
- Hegel / Mill: implement only a narrow `GRID` `confirm` -> `proceed` rewrite with before-state guards and log the actual executed action.
- Descartes / Halley: keep the main loop as run script -> inspect JSONL -> patch one concrete failure -> test -> document -> rerun. Classify failures as strategy, execution, diagnostic split, or data pipeline before changing code.

2026-07-05: the user asked the three visible long-lived sub-agents to jointly evaluate the state after probe49/probe50/probe51. The main thread sent read-only evaluation tasks and did not ask them to edit files.

```text
multi_agent_v1.send_input({
  "target":"019f2e94-552b-7301-9cc5-21eed1fc2949",
  "interrupt":true,
  "message":"Evaluate current model/strategy/learning route after probe49 clean, probe50 HAND_SELECT diagnostic, and probe51 MCP unreachable; do not modify files."
}) -> submission_id 019f2ff4-eb33-7fd0-98d4-e99eed58b558
multi_agent_v1.send_input({
  "target":"019f2f5c-fae1-7cb2-bea6-b8574085839d",
  "interrupt":true,
  "message":"Evaluate execution layer, MCP reliability, HAND_SELECT rewrite, and next probe requirements; do not modify files."
}) -> submission_id 019f2ff5-120b-7ae2-8891-a3eccf3f8b8c
multi_agent_v1.send_input({
  "target":"019f2f5d-0f0e-7612-86d2-d4bb3aeee828",
  "interrupt":true,
  "message":"Evaluate overall work mode, multi-agent division, documentation ledger, and next five steps; do not modify files."
}) -> submission_id 019f2ff5-352c-7eb2-b653-76be4d014610
multi_agent_v1.wait_agent({
  "targets":[
    "019f2e94-552b-7301-9cc5-21eed1fc2949",
    "019f2f5c-fae1-7cb2-bea6-b8574085839d",
    "019f2f5d-0f0e-7612-86d2-d4bb3aeee828"
  ],
  "timeout_ms":180000
})
```

Joint result:

- Faraday / Harvey: start autonomous learning stage 1 now, but only as shadow scoring, tie-breaker, or high-confidence veto. Prioritize route risk, potion tempo, shop buying, and continued card reward learning. Next major strategy improvement should be one-turn combat local search before large combat learning or RL.
- Hegel / Mill: the highest current risk is MCP 8080 unreachable, followed by preflight/execute transition races and remaining non-combat command aliases. The `HAND_SELECT` `select_cards` -> `choose` rewrite is properly narrow for probe50, but split runs, MCP unreachable logs, and transition races must be classified so training does not learn from infrastructure failures.
- Descartes / Halley: the evidence loop is still the right operating model. Classify every run as `clean_trainable`, `diagnostic_excluded`, or `infra_blocked`; update the ledger every probe; do not modify strategy while MCP is down.
- Main-thread synthesis: recover and verify MCP, update docs, keep probe49 in training, exclude probe50/probe50-continuation/probe51, run fresh probe52, then decide between execution-layer classification and one-turn local search from the next clean evidence.

Previous clean Codex project-thread backups remain available if needed:

```text
Faraday thread -> 019f2e8b-0604-7442-8554-bbfdb9ee8513
Hegel thread -> 019f2e8b-50cf-7340-a53b-ddb3fc2e8caf
Descartes thread -> 019f2e8b-8c52-7df3-8341-bd8741106808
```

2026-07-05: old `multi_agent_v1` ids for Hegel, Descartes, and Turing were checked and were no longer callable. Full-history temporary forks `019f2f54-9abf-73f1-bdc4-29edf2cf9dcf` and `019f2f54-c477-7211-a4c0-608ad45cdd35` timed out and were closed. The current lightweight advisory replacements are Mill and Halley above.

2026-07-05 after probe41: the main thread asked the three current long-lived `multi_agent_v1` advisors for a joint read-only evaluation.

```text
multi_agent_v1.send_input({
  "target":"019f2e94-552b-7301-9cc5-21eed1fc2949",
  "message":"Evaluate the learning route after probe41; read docs and slay_ai policy/learn/runner; do not modify files."
})
multi_agent_v1.send_input({
  "target":"019f2f5c-fae1-7cb2-bea6-b8574085839d",
  "message":"Evaluate MCP/script reliability after probe41; focus on runner, MCP client, policy, tests; do not modify files."
})
multi_agent_v1.send_input({
  "target":"019f2f5d-0f0e-7612-86d2-d4bb3aeee828",
  "message":"Evaluate long-term architecture and main-agent work mode after probe41; do not modify files."
})
multi_agent_v1.wait_agent({
  "targets":[
    "019f2e94-552b-7301-9cc5-21eed1fc2949",
    "019f2f5c-fae1-7cb2-bea6-b8574085839d",
    "019f2f5d-0f0e-7612-86d2-d4bb3aeee828"
  ],
  "timeout_ms":240000
})
```

Joint conclusion:

- Faraday / Harvey: the learning route is correct. Keep heuristic control with supervised learning as shadow/tie-breaker; prioritize potion tempo, route risk, shop buying, and rest/smith before combat learning or RL.
- Hegel / Mill: the largest engineering risk is still action legality and transition races. Add a unified `get_available_commands` preflight and post-action transition guard before investing in a custom MCP service.
- Descartes / Halley: do not keep modifying strategy before a new live run. The next step should be probe42 to validate the `Energy Potion` fix and the shop cancel/proceed state fix.
- Main-thread synthesis: run probe42 next; after the next JSONL, likely implementation priorities are candidate-score logging, action preflight, and then shadow models for potion use / route risk.

## How To Spawn Faraday

Use an explorer agent for strategy guidance. Do not ask it to edit code unless the role is changed explicitly.

```text
You are this project's game AI model expert advisor. Based on the current repository and real run logs, guide the Slay the Spire AI project.

Working directory: C:\Users\jikun\Documents\game.
Main files: slay_ai/policy.py, slay_ai/runner.py, slay_ai/campaign.py, slay_ai/memory.py, data/default_memory.json, tests/test_policy.py.
Important logs: ai_runs_subject_probe/, ai_runs_subject_probe2/, ai_runs_strategy_probe3/, ai_runs_strategy_probe3_continue/.

Project background: the long-term goal is four-character A20, but the immediate goal is the main AI functionality: combat decisions, events, rewards, route choices, and a learning loop. Current unlock frontier should be read from local preference files, not assumed to be A20. The user requires evidence-driven iteration: run the script, inspect concrete failures, then improve.

Please output:
1. Phased project roadmap: heuristic bot -> data loop -> model/RL/search.
2. The highest-impact 5-8 features to add next, ordered by win-rate impact.
3. Concrete combat AI architecture advice: state features, action generation, safety constraints, potion/rest/route strategy.
4. Multi-agent collaboration responsibilities.
5. Do not edit code. Cite concrete files/logs as evidence.
Reply in Chinese, concise but actionable.
```

## How To Spawn Hegel

Use a worker agent for bounded script execution-layer changes. Give it a disjoint write scope so it does not conflict with strategy edits in the main thread.

```text
You are this project's game Mod / Slay the Spire MCP / script integration senior engineer worker. Work in C:\Users\jikun\Documents\game.

Task boundary: focus on slay_ai/runner.py, slay_ai/mcp_client.py, and runner tests. Do not modify policy scoring or data/default_memory.json unless explicitly reassigned. You are not alone in the codebase: the main thread may edit slay_ai/policy.py and data/default_memory.json. Do not revert others' edits; adapt to them.

Background: MCPTheSpire run logs show action races such as MAP choose succeeding but the next frame still reading stale MAP, combat ending but the script still trying play/end, and get_game_state sometimes failing with Internal error: null after death or transition.

Implement execution-layer improvements:
1. Add action-aware settle waits after choose/proceed/play_card/end_turn/use_potion/rest/grid/select/confirm.
2. Treat recoverable action errors as races: log, wait, reread state, and continue when possible.
3. Retry transient Internal error: null reads briefly and return a more accurate status when recovery fails.
4. Add useful logging fields such as action latency, settle delay, last error, and recovered.
5. Add or update lightweight tests and run verification commands.

Report changed files, core logic, and validation commands.
```

## How To Reuse Existing Agents

When an agent is still open, prefer sending it a focused follow-up instead of spawning a duplicate.

Example follow-up for Faraday:

```text
New run log: ai_runs_strategy_probe3_continue/<latest>.jsonl.
Please diagnose the next strategy bottleneck only. Focus on combat decisions and route/event choices. Do not edit code.
Return concrete recommendations ordered by expected impact.
```

Example follow-up for Hegel:

```text
New execution issue: MCP returned Internal error: null after F14 death/transition in ai_runs_strategy_probe3_continue/<latest>.jsonl.
Please inspect runner/mcp_client behavior and propose or implement execution-layer fixes only. Do not touch policy scoring.
```

Example follow-up for Descartes:

```text
Please audit the current work mode after the latest run/fix cycle.
Check whether the main thread is still following evidence-driven iteration toward four-character A20, whether agent responsibilities are efficient, and what the next highest-leverage action should be.
Do not edit code. Return: continue / stop / change / next action / risk.
```

## Architecture Review Cadence

Ask Descartes for a work-mode audit at these points:

- After each meaningful live run that changes the failure mode.
- After every 2-3 code patches, before adding broader abstractions.
- When MCP stability work and strategy work compete for priority.
- Before moving from heuristic rules into model training, search, or RL.
- Before claiming a milestone such as Act 1 stability, an unlock increase, or A20 completion.

## Joint Evaluation Log

2026-07-05: all four long-lived `multi_agent_v1` agents jointly reviewed the probe12 failure mode:

```text
multi_agent_v1.send_input(Faraday, "Evaluate no-monsters combat, learning priorities, and next probe metrics.")
multi_agent_v1.send_input(Hegel, "Evaluate MCP/runner handling for no-monsters combat and terminal recovery.")
multi_agent_v1.send_input(Descartes, "Audit work mode, agent division, and learning-stage timing.")
multi_agent_v1.send_input(Turing, "Review the proposed policy/test patch for no-monsters combat transition frames.")
multi_agent_v1.wait_agent([Faraday, Hegel, Descartes, Turing])
```

Consensus:

- P0: `screen_type=NONE`, `room_phase=COMBAT`, and `combat_state.monsters=[]` is a transition or stale combat frame. The policy must not emit `end_turn`, play cards, or spend emergency potions in that state.
- The minimal policy fix is `wait`; only `proceed` when the room is explicitly complete.
- Add regression coverage for empty monsters with empty hand, empty monsters with cards in hand, and empty monsters with low HP plus a usable potion.
- Keep the evidence loop: run probe, inspect JSONL, apply one small fix, add a test, run validation, then probe again.
- Start learning only as a shadow/assistant layer. Prioritize card rewards, potion use, rest/smith calibration, and route risk before combat-action learning.
- Hegel flagged a follow-up: `mcp_watchdog --recover-terminal` should eventually delay and recheck after recovery instead of immediately trusting the first post-recovery probe.

2026-07-05: all four long-lived agents reviewed the probe15 result. The run reached F14 on `IRONCLAD:A4` after the partial-hand and Act 1 rest-threshold fixes.

```text
multi_agent_v1.send_input(Faraday, "Evaluate probe15 priorities: REST complete, low-HP route, exhaust-payoff card picks, combat/potions.")
multi_agent_v1.send_input(Hegel, "Evaluate REST complete policy/runner boundary and end-turn transition races.")
multi_agent_v1.send_input(Descartes, "Audit whether to continue small evidence-backed fixes after reaching F14.")
multi_agent_v1.send_input(Turing, "Review implementation risk for REST complete, exhaust-payoff card scoring, and low-HP routing.")
multi_agent_v1.wait_agent([Faraday, Hegel, Descartes, Turing])
```

Consensus:

- P0: `REST + room_phase=COMPLETE` must `proceed` before considering rest/smith. This prevents repeated rest choices and polluted campfire logs.
- P1: low-HP routing/rest should be considered after the next probe if deaths still come from entering fights at very low HP.
- P2: a narrow card-reward rule is acceptable: penalize `Feel No Pain` and `Dark Embrace` when the deck has no exhaust enabler; avoid a broad synergy rewrite for now.
- End-turn reward-transition races are lower priority while they remain recoverable and do not block progress.
- Continue the small loop: patch one concrete failure, add tests, validate, then probe.

2026-07-05: probe16 reached the Act 1 boss on `IRONCLAD:A4` but the MCP HTTP connection closed during the low-HP boss state. `slay_ai.mcp_client` now wraps `RemoteDisconnected`, `TimeoutError`, and connection errors as `MCPError` so runner recovery can handle them instead of crashing the campaign process.

2026-07-05: probe17 verified the disconnect fix: a terminal MCP null state was recorded as synthetic game over instead of a Python traceback. The run also exposed a route failure: at F5, HP 74/80, choices `R` and `E`, and only non-tempo potions (`ColorlessPotion`, `GamblersBrew`, `Ancient Potion`), the policy chose elite and reached F6 reward at 12/80. A narrow map rule was added: in Act 1 after floor 5, when a rest node is available, HP is below 95%, and no elite-tempo potion is held, immediate elite is penalized.

2026-07-05: probe18 verified the route rule in live play. The run avoided the early elite trap, reached the Act 1 boss at F16 with 72/95 HP, and died to boss combat. Next likely bottleneck is boss/elite potion timing and high incoming-damage turns, not route-to-Act-1-boss stability.

2026-07-05: probe19 exposed a Neow `GRID` loop after choosing the event that transforms cards. Generic event grids must choose low-value cards, not high-value cards like `Bash`, and must skip already selected cards. `GRID` snapshots now log `num_cards`, `selected_cards`, and grid flags so future grid failures are inspectable from JSONL.

2026-07-05: probe20 verified that the Neow grid loop no longer blocks startup. The same patch set also refined emergency potion use: high projected damage now triggers defensive potions earlier, while low HP with no incoming no longer wastes `Block Potion`. REST choose actions now wait for the campfire transition to complete before the next state read.

2026-07-05: all four long-lived agents jointly reviewed probe20 after it died on F8 at `IRONCLAD:A4`.

```text
multi_agent_v1.send_input(Faraday, "Evaluate probe20 early hallway deaths, learning timing, and probe21 priorities.")
multi_agent_v1.send_input(Hegel, "Evaluate runner/MCP logging and data-quality guards before probe21.")
multi_agent_v1.send_input(Descartes, "Audit the main work loop, multi-agent division, and next success metrics.")
multi_agent_v1.send_input(Turing, "Review narrow code-level fixes for combat policy, route policy, and logging.")
multi_agent_v1.wait_agent([Faraday, Hegel, Descartes, Turing])
```

Consensus:

- The current primary bottleneck is early hallway combat survival, not A20 unlock logic, MCP startup, or learning-model control.
- Probe21 should first add better combat observability: player HP, block, energy, powers, incoming damage, and monster HP/block/intent/move in JSONL snapshots.
- Combat policy should receive a narrow patch only: prioritize targets whose death or split stops current incoming damage, reduce non-defensive attacks under low-HP pressure, and keep true lethal/split attacks allowed.
- Route policy should only add a narrow low-HP Act 1 safety rule: if HP is low and `?` or `R` is available, penalize immediate `M`; do not globally avoid hallway fights while healthy.
- Learning remains shadow/assistant only. Do not replace the heuristic controller until clean logs are abundant and Act 1 is more stable.
- Probe21 success metrics: no GRID/REST regression, F8 entered with meaningfully more HP than probe20 or death floor advances to F10+, and new logs explain each large HP loss without guessing energy/block.

Implemented from this review:

```powershell
python -m unittest tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

The implemented patch added combat player/monster snapshot fields, low-HP pressure attack penalties, high-threat target selection, large-slime split targeting, and the narrow low-HP Act 1 `?`/`R` route safety rule.

2026-07-05: probe21 validated the probe20 review patch. The run reached the Act 1 boss on `IRONCLAD:A4` and ended at F16 with a synthetic game-over record after a likely lethal boss transition. This exceeded the probe21 success bar of F10+ and confirmed that GRID/REST did not regress.

Key probe21 evidence:

- Combat snapshots now include player energy/block/HP and monster HP/block/intent/move, which made the next issue inspectable.
- F12 exposed an execution-layer stability issue: several monsters still had `intent=DEBUG` and empty `move` while the hand and energy looked ready, so the policy spent energy as if incoming were 0. A runner guard now treats pre-action `DEBUG` monster intents as unstable and waits for the intent frame to settle.
- Boss entry was only 42/88 HP; Guardian T2 hit for 36 while only one `Defend` was available, causing a 31 HP loss. Next strategy bottleneck is entering the boss with more HP and handling large Act 1 attacks better.
- After the synthetic game-over, MCP briefly returned `Internal error: null`; `mcp_watchdog --recover-terminal` advanced to terminal/main-menu, and a delayed watchdog recheck confirmed `MAIN_MENU` health.

Validation after probe21:

```powershell
python -m unittest tests.test_runner tests.test_policy
python -m compileall slay_ai tests
python -m unittest discover -s tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

2026-07-05: probes22-25 continued the same evidence loop.

- Probe22 reached F16 Hexaghost at 72/80 HP but died to boss damage. `DEBUG` monster-intent decision frames were 0, validating the runner intent-settle guard.
- Faraday, Hegel, and Turing reviewed probe22. Consensus: improve Act 1 boss resource structure with deck-shape card reward scoring, keep 72/80 Hexaghost smith acceptable, and tighten GRID/REST transition waits.
- Implemented deck-shape card rewards: when Act 1 deck lacks premium block, boost `Power Through`, `Shrug It Off`, `Flame Barrier`, `Ghostly Armor`, `True Grit`, etc.; when attack-heavy, penalize extra duplicate attacks such as a third `Clothesline`.
- Implemented GRID transition waits so upgrade `confirm` waits through stale REST frames until the campfire becomes complete.
- Probe23 showed the GRID duplicate-smith race was gone, but exposed a runner terminal bug: after death returned to `MAIN_MENU`, the episode continued until `max_steps`. That log is polluted and must not be used for training.
- Implemented `synthetic_after_main_menu`: if the previous state was in-game and the next state is main menu, record a synthetic game-over instead of looping.
- Probe24 validated terminal recording: it ended as `game_over` at F6 rather than `max_steps`. It also exposed an injured route issue that led into a forced elite.
- Probe25 reached F16 Slime Boss and recorded a clean synthetic game-over. It showed that injured ordinary-fight avoidance should not be bypassed by tempo potions; that exception now only matters for elite risk.

Validation and learning after probes24/25:

```powershell
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

2026-07-05: probes26-29 exposed the next Act 1 survival bottlenecks.

- Probe26 recorded a clean game-over at F6 against Gremlin Nob. The route chose an ordinary fight instead of an available shop at 63/80 HP on F4, then entered a forced elite. Gremlin Nob combat also showed basic `Defend` plays pumping Nob when the incoming pressure was not lethal. The policy now heavily prefers an available shop over `M` when injured in Act 1, and basic block cards are penalized against Nob unless pressure is lethal.
- Probe27 recorded a clean game-over at F11 from an event option at 8/88 HP. The losing option text was mojibake but contained an HP-loss pattern (`ʧȥ 11 ������`). Event scoring now parses English, Chinese, and observed mojibake HP-loss text and heavily avoids lethal or low-HP loss choices.
- Probe28 recorded a synthetic game-over at F6 after choosing a shop but leaving without buying anything. This is a strategy gap, not an MCP crash: low-HP shop routing needs a minimal purchase policy before it can reliably improve survival.
- Probe29 recorded a clean game-over at F4. The reward path picked `Burning Pact` on F1 from `Burning Pact` / `Havoc` / `Thunderclap`, then at 37/88 HP picked `Pummel` over `Iron Wave`. The next policy patch should make early Act 1 reward scoring less engine-heavy and more survival-oriented at low HP.

The long-lived agents jointly reviewed these failures with explicit role prompts:

```text
multi_agent_v1.send_input(Faraday, "Evaluate probe26-29 strategy/model gaps: Act 1 survival, shop resources, Burning Pact/Combust-style risk.")
multi_agent_v1.send_input(Hegel, "Evaluate runner/MCP/script reliability around SHOP_ROOM, HAND_SELECT, and terminal snapshots.")
multi_agent_v1.send_input(Descartes, "Audit the main-agent/sub-agent work loop and next-hour priorities.")
multi_agent_v1.send_input(Turing, "Review policy.py/runner.py minimal code risk for early engine cards and shop behavior.")
multi_agent_v1.wait_agent([Faraday, Hegel, Descartes, Turing])
```

Consensus:

- Continue the evidence loop; do not jump to A20 assumptions or broad rewrites.
- The current primary strategy gaps are Act 1 survival card selection, low-HP combat risk gates, and shop resource use.
- First patch the clean probe29 failure: penalize unsupported early `Burning Pact`/slow engine picks and prefer survival cards such as `Iron Wave` when low HP in early Act 1.
- Keep learning in shadow/tie-breaker mode. The next learning upgrades should focus on card rewards, shop/potion spending, and route risk before combat RL.
- Add shop inventory logging and a minimal purchase policy next, but only after confirming the current MCP shop schema from logs.

Implemented from this review:

```powershell
python -m unittest tests.test_policy
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

The patch added early Act 1 penalties for unsupported slow engine rewards such as `Burning Pact`, low-HP early survival-card preference such as `Iron Wave` over pure attacks, and combat risk gates that avoid `Burning Pact`/`Combust`-style low-HP pressure plays.

2026-07-05: probe30 verified that the probe29 card reward fix moved the run much farther but exposed a new route-risk issue.

- The first probe30 process advanced from a clean main menu to F8 REST and then lost the MCP connection at step 91. The log is `action_failed` and must not be treated as a complete training episode.
- After restarting ModTheSpire, `runner --continue` resumed the save and reached a synthetic game-over at F13. This continuation is diagnostic but should also stay out of default training because the episode is split across an action-failed campaign log and a continuation log named with the runner default `a0`.
- The key strategic failure happened before the forced death: at F10 with 72/88 HP and only `SpeedPotion`, the map offered `E` and `?`; the policy chose the elite. The run survived that elite at 43/88, then was forced through a hallway and a later elite, entering the forced elite at 9/88 and dying.
- A narrow route rule now avoids late Act 1 elites when HP is below 88%, a safe alternative exists, and the only potion support is not high-impact. High-impact potions such as Fire, Explosive, Attack, Fear, Strength, Power, Duplication, or Distilled Chaos still allow the elite route.

2026-07-05: probe31 recorded a clean synthetic game-over at F5. It validated that the runner can still start from a clean main menu, but exposed two new policy issues:

- Reward picks formed an unsupported exhaust-heavy package: `True Grit` on F1, `Second Wind` over `Flame Barrier` on F2, and `Fiend Fire` over `Perfected Strike` on F3. Without `Feel No Pain` or `Dark Embrace`, repeated exhaust enablers left the deck fragile in a hard hallway fight.
- Combat at F5 had several turns where high incoming pressure made ordinary attacks score below the minimum threshold. When no useful defense was available, the policy ended the turn instead of spending remaining energy on attacks, leaving enemies alive and preserving incoming damage.

Implemented from probe31:

- Early Act 1 card rewards now apply a duplicate exhaust-enabler penalty when the deck already has exhaust enablers but no exhaust payoff. The penalty escalates after two existing enablers.
- Combat now has a narrow pressure fallback: if no card clears the normal threshold, incoming exceeds block, energy remains, and a playable attack exists, play the best attack instead of ending the turn.

2026-07-05: probe32 reached F12 Lagavulin and recorded a clean game-over at step 196. This was a meaningful improvement over probe31, and the new combat fallback fired in live play at F10 (`No defense under pressure; play ... as fallback`). The next bottleneck was shop resource use:

- At F5, the policy selected a shop with 156 gold and two Attack Potions, then `SHOP_ROOM` immediately used `proceed`, leaving without opening the shop inventory. The next node was a forced elite.
- MCPTheSpire was inspected directly from `MCPTheSpire.jar`. `ChoiceScreenUtils.getShopRoomChoices()` returns a single `shop` choice, so `SHOP_ROOM` should use `choose 1`, not `proceed`.
- `SHOP_SCREEN` choice order is: affordable purge if available, affordable cards, affordable relics, affordable potions. `GameStateConverter.getShopScreenState()` exposes `cards`, `relics`, `potions`, `purge_available`, and `purge_cost` with prices.

Implemented from probe32:

- `SHOP_ROOM` now enters the shop via `choose 1`.
- `SHOP_SCREEN` has a conservative purchase policy: buy high-value cards, purge Strike when no better purchase is available, buy high-impact potions only with an empty potion slot, otherwise leave.
- Runner JSONL snapshots now include shop inventory and prices so future shop decisions can be audited from logs.

2026-07-05: probe34 recorded a clean synthetic game-over at F6. It did not encounter a shop, so the shop implementation still needs live validation. The main failure path was:

- F3 at 78/88 HP offered `M` and `?`; the policy chose another hallway while the deck still lacked premium block.
- F4 was a three-Louse fight. `Thunderclap` was in hand but undervalued as a single-target attack, so the policy spent energy on `Bash` and took a large HP loss.
- F5 event was safely skipped because the active option cost HP, but the next map node was a forced elite at only 51/88 HP.

Implemented from probe34:

- AoE attacks such as `Thunderclap`, `Cleave`, `Immolate`, and `Reaper` now score against the number of live monsters instead of only one target. `Thunderclap` gets an extra multi-enemy utility bonus and does not attach a target index.
- Act 1 route scoring now penalizes an ordinary hallway in favor of `?` when it is at least floor 3, HP is below 90%, and the deck still has no premium block stabilizer.

2026-07-05: probe35 validated the probe34 route change. At F2/F3 the policy chose `?`, the run recovered to full HP through an event, reached F16 Slime Boss, and died late in the boss fight at step 231.

Key evidence:

- The conservative `?` route avoided the early F4/F6 collapse seen in probe34.
- The run entered Slime Boss with 59/88 HP and survived through the split.
- At the lethal boss endgame, the player still held `SneckoOil`. Under 11/88 HP and 30 incoming damage, drawing with Snecko Oil was better than holding it, but the emergency potion logic did not recognize it.

Implemented from probe35:

- `SneckoOil` is now treated as emergency tempo under lethal/high-pressure conditions.

2026-07-05: probe36 recorded a clean game-over at F6 after the Neow transform start. It exposed a route scoring bug:

- At F4, HP was 55/80 and the next map offered `$` and `M`.
- The policy chose `M`, then entered a forced elite path and died to Lagavulin at F6.
- The intended low-HP shop safety rule was shadowed by an earlier generic hallway penalty branch, so the shop-specific penalty did not stack.

Implemented from probe36:

- The low-HP shop-vs-hallway penalty now stacks independently instead of being blocked by the generic low-HP hallway branch.
- Regression coverage was added for the F4 low-HP `$` vs `M` route case.

Validation and learning after probe36:

```powershell
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_subject_probe ai_runs_subject_probe2 --reset
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- `slay_ai.learn --reset`: read 38 logs, applied 19 completed runs, skipped 19 incomplete runs.
- `slay_ai.train_card_model`: loaded 149 card-pick examples and wrote 66 card deltas.
- `unittest discover`: ran 104 tests OK.
- `compileall`: OK.

2026-07-05: lightweight multi-agent review after probe36:

```text
multi_agent_v1.send_input(Faraday / Harvey, "Evaluate learning route after probe36: 149 examples, 38 logs, 104 tests OK.")
multi_agent_v1.spawn_agent(Hegel / Mill, "Evaluate runner/MCP/shop/HAND_SELECT reliability after probe36.")
multi_agent_v1.spawn_agent(Descartes / Halley, "Audit work mode, agent registry drift, frontier management, and probe37 criteria.")
multi_agent_v1.wait_agent([Faraday / Harvey, Hegel / Mill, Descartes / Halley])
multi_agent_v1.close_agent(temporary full-history Carver)
multi_agent_v1.close_agent(temporary full-history Helmholtz)
```

Consensus:

- Keep the controller heuristic-first. Learning remains a shadow/tie-breaker layer; do not let a model control the whole run yet.
- Continue supervised log learning for card rewards first, then route risk, potion/shop resource use, and rest/smith calibration. Do not attempt whole-run RL or end-to-end combat RL now.
- Before adding more policy rules, run probe37 to live-validate the recent AoE, low-HP route, shop entry, and emergency potion fixes.
- If probe37 fails due to `action_failed`, stale screen, bad shop index, or MCP transition, prioritize runner instrumentation and available-command guards over strategy changes.
- Maintain an explicit agent registry and cycle ledger so valid ids, probe status, training inclusion, and verification commands do not drift.

2026-07-05: probe37 reached the Act 1 boss on `IRONCLAD:A4` and recorded a clean game-over at F16. It did not enter a shop, so the shop-screen live validation remains open. It did validate that the route/rest loop is now strong enough to reach the boss again.

Key evidence:

- The run beat F6 Lagavulin, rested at F8, beat a second elite at F10, rested at F15, and entered Guardian at 67/95 HP.
- Boss combat used `DistilledChaos` and `Dexterity Potion` under T2 incoming 36, but kept `CultistPotion` and `Swift Potion` until death.
- The final state had Guardian at 16 HP, player at 0/95 HP, and Guardian still had `Sharp Hide`.
- Step 194 logged a recoverable action race (`play 1 2` target index out of bounds), so runner action-guard instrumentation remains a follow-up if this repeats.

Implemented from probe37:

- Long boss/elite fights now use `CultistPotion` early instead of saving it until death.
- `Swift Potion` is now treated as an emergency tempo potion.
- Combat scoring penalizes nonlethal attacks into `Sharp Hide` / `Thorns` when reflected damage can kill the player, while still allowing likely lethal attacks.
- Runner snapshots now default missing `GAME_OVER` victory to `False`; `slay_ai.learn.read_log` also treats older `GAME_OVER` logs with missing victory as completed losses.

Validation and learning after probe37:

```powershell
python -m unittest tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `tests.test_policy tests.test_runner`: ran 106 tests OK.
- `unittest discover`: ran 109 tests OK.
- `compileall`: OK.
- `slay_ai.train_card_model`: loaded 158 card-pick examples and wrote 66 card deltas.
- `slay_ai.learn --reset`: read 39 logs, applied 28 completed runs, skipped 11 incomplete runs.

2026-07-05: probe38 is an excluded diagnostic run, not a training episode. It reached F13 shop with 59/80 HP and 239 gold, bought `Shrug It Off`, purged a Strike, and bought a potion, then looped between `SHOP_ROOM` and `SHOP_SCREEN` until max steps.

Key evidence:

- `SHOP_SCREEN` initially logged an empty inventory, then a later read showed real shop inventory. Leaving immediately on the empty snapshot would miss purchases.
- After the final purchase, policy returned `cancel`; MCP moved back to `SHOP_ROOM`, and the existing `SHOP_ROOM` policy immediately chose `choose 1`, re-entering the shop.
- The run stopped at max steps on F13 and must not be included in default `train_card_model` or `learn` commands.

Implemented from probe38:

- `SHOP_SCREEN` now waits when cards, relics, potions, and purge status are all missing, treating the state as inventory still loading.
- `SHOP_SCREEN` keeps using MCP's valid `cancel` action for the leave button.
- Runner now records the floor after a `SHOP_SCREEN` cancel and forces the next same-floor `SHOP_ROOM` state to `proceed`, preventing a re-entry loop.

Validation after probe38:

```powershell
python -m unittest tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Initial probe39 check:

- The first probe39 attempt proved that `leave` appears as an available command description but is not a valid `execute_actions` action; it produced repeated `Unknown action: leave` recoverable errors and was stopped manually.
- Do not include that probe39 log in training.

Results:

- `tests.test_policy tests.test_runner`: ran 108 tests OK.
- `unittest discover`: ran 111 tests OK.
- `compileall`: OK.

2026-07-05: probe40 verified the shop-loop fix. The run bought a shop card, purged a Strike, left the shop, then the runner forced same-floor `SHOP_ROOM` to `proceed` at step 114. It reached the Act 1 boss and ended as a clean synthetic game-over at F16.

Key evidence:

- F7 shop sequence: buy, purge, no high-confidence purchase, `cancel`, then `Shop already left; proceed.`
- The run reached F16 Guardian at 50/88 HP.
- During Guardian `Sharp Hide`, pressure fallback still selected negative-score attacks (`fallback score -23.5` and `-25.5`), so the new reflected-damage penalty was not enough by itself.

Implemented from probe40:

- Pressure fallback now refuses attacks below the normal minimum card score, preventing negative-score fallback attacks into `Sharp Hide` / `Thorns`.
- The affected card-reward test now uses isolated learned memory so future offline learning updates do not change unit-test expectations.

Validation and learning after probe40:

```powershell
python -m unittest tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `tests.test_policy tests.test_runner`: ran 109 tests OK.
- `unittest discover`: ran 112 tests OK.
- `compileall`: OK.
- `slay_ai.train_card_model`: loaded 167 card-pick examples and wrote 67 card deltas.
- `slay_ai.learn --reset`: read 40 logs, applied 29 completed runs, skipped 11 incomplete runs.

2026-07-05: probe41 revalidated the shop fix twice, at F5 and F11/F13, with `Shop already left; proceed.` It reached F16 Hexaghost and recorded a clean game-over.

Key evidence:

- F5 and later shops used `cancel`, then runner forced same-floor `SHOP_ROOM` to `proceed`; no shop loop recurred.
- The run entered F16 at 43/87 HP and died on Hexaghost turn 9 to 30 incoming damage.
- The final boss state still held `Energy Potion`; during combat it had `can_use=True`, but emergency tempo did not include energy potions.

Implemented from probe41:

- `Energy Potion` is now treated as emergency tempo under lethal/high-pressure conditions.

Validation and learning after probe41:

```powershell
python -m unittest tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `tests.test_policy tests.test_runner`: ran 110 tests OK.
- `unittest discover`: ran 113 tests OK.
- `compileall`: OK.
- `slay_ai.train_card_model`: loaded 175 card-pick examples and wrote 67 card deltas.
- `slay_ai.learn --reset`: read 41 logs, applied 30 completed runs, skipped 11 incomplete runs.

2026-07-05: probe42 followed the joint agent recommendation to run before further strategy edits. It reached F6 and died to Gremlin Nob as a clean game-over.

Command:

```powershell
python -m slay_ai.mcp_watchdog --attempts 3 --delay 1 --recover-terminal --json
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 340 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe42.json --log-dir ai_runs_strategy_probe42 --use-all-hardware
```

Key evidence:

- Target was still `IRONCLAD:A4`; `--ascension 20` remained only the long-term upper target.
- The run ended at `ai_runs_strategy_probe42/20260705_075617_ironclad_a4.jsonl`, step 101, F6 `GAME_OVER`.
- The route reached F5 at 39/80 HP with only an elite node available next, so earlier route risk/path-shape remains a future bottleneck.
- Nob turn 2 had three `Strike_R` cards and one `Whirlwind`; policy chose single-target `Whirlwind`, spent all energy, and the next state showed Nob HP only 78 -> 73.
- A recoverable action race appeared at step 60: attempted `end` while available commands were `[choose, potion, proceed, key, click, wait, save, state]`.
- Emergency potion logic did trigger: T3 used `Dexterity Potion` under dangerous incoming. It was not enough to save the fight.

Implemented from probe42:

- Single-target X-cost attacks now receive an opportunity-cost penalty when they do not kill and ordinary playable attacks can spend the same energy better.
- Added a regression test for the probe42 Nob hand so it chooses `Strike` before single-target `Whirlwind`.

Validation and learning after probe42:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_subject_probe ai_runs_subject_probe2 --reset
python -m unittest tests.test_policy.PolicyTests.test_combat_prefers_strike_over_single_target_x_cost_when_energy_would_be_wasted tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- `slay_ai.train_card_model`: loaded 180 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 42 logs, applied 31 completed runs, skipped 11 incomplete runs.
- Targeted policy/runner tests: ran 112 tests OK.
- `unittest discover`: ran 114 tests OK.
- `compileall`: OK.

2026-07-05: probe43 was run to validate the probe42 X-cost fix. It reached the Act 1 boss, but was manually stopped after an empty-hand wait loop at F16; do not include it in training.

Command:

```powershell
python -m slay_ai.mcp_watchdog --attempts 3 --delay 1 --recover-terminal --json
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 340 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe43.json --log-dir ai_runs_strategy_probe43 --use-all-hardware
```

Key evidence:

- Log: `ai_runs_strategy_probe43/20260705_080556_ironclad_a4.jsonl`.
- The run reached F16 Hexaghost, so it validated that the X-cost change did not break early progression.
- F8 Nob showed the intended direction: T1 played `Strike` repeatedly rather than spending all energy on a bad single-target `Whirlwind`.
- The F12 shop sequence again validated the shop fix: buy, purge, buy potion, `cancel`, then `Shop already left; proceed.`
- A recoverable action race recurred at step 102, reinforcing the need for a future `get_available_commands` preflight.
- F16 T2 got stuck from step 198 onward with `hand=[]`, energy 2, block 30, incoming 18. Policy kept waiting for a hand to be dealt even though the turn had already played all cards and block covered incoming.

Implemented from probe43:

- Empty-hand combat now ends the turn when `turn > 1`, current block is positive, and block covers incoming damage, even if energy remains.
- Added a regression test for the F16 Hexaghost empty-hand/block-covered state while preserving the existing new-turn empty-hand wait test.

Validation after probe43 diagnostic:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_ends_turn_when_empty_hand_block_already_covers_incoming tests.test_policy.PolicyTests.test_combat_waits_when_new_turn_hand_is_not_dealt_yet tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Targeted policy/runner tests: ran 114 tests OK.
- `unittest discover`: ran 115 tests OK.
- `compileall`: OK.
- No learning/training update from probe43 because it was manually stopped and has no terminal outcome.

Continuation validation:

```powershell
python -m slay_ai.mcp_watchdog --attempts 2 --delay 1 --json
python -m slay_ai.runner --max-steps 160 --interval 0.08 --startup-timeout 8 --log-dir ai_runs_strategy_probe43_continue_emptyhandfix
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- Continuation log: `ai_runs_strategy_probe43_continue_emptyhandfix/20260705_081939_ironclad_a0.jsonl`.
- First continuation decision was `Hand is empty and block covers incoming; end the turn.`
- The continuation killed the Act 1 boss, entered Act 2, and ended at F20 `GAME_OVER`.
- Because this was a split continuation after a manually stopped diagnostic run, it remains excluded from default training.
- `slay_ai.learn --reset` was rerun on the default included logs to remove the runner's incremental continuation update; final learning state remains 42 logs read, 31 completed applied, 11 skipped.

2026-07-05: probe44 was a fresh validation run after the empty-hand fix. It ended as a clean synthetic game-over at F8 and is included in default training.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 360 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe44.json --log-dir ai_runs_strategy_probe44 --use-all-hardware
```

Key evidence:

- Target remained `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe44/20260705_082507_ironclad_a4.jsonl`.
- No F16 empty-hand stall recurred; the run ended earlier at F8.
- F5 offered `R` and `E` at 76/80 HP. The old elite/rest penalty used `hp_ratio < 0.95`, so exactly 76/80 was treated as safe and chose elite.
- The F6 elite left the bot at 18/80 HP, then pathing forced hallway into another elite. F8 Lagavulin ended the run.
- The run also showed the F3 event added `Doubt` after taking 175 gold; event option logging is still too weak to safely build a targeted rule from this alone.

Implemented from probe44:

- Act 1 route scoring now treats `hp_ratio <= 0.95` as risky when choosing elite over rest without an elite-tempo potion.
- Added a regression test for the exact probe44 state: F5, 76/80 HP, no potion, `R` vs `E` should choose rest.

Validation and learning after probe44:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_map_prefers_rest_over_act1_elite_at_exact_probe44_hp_without_potion tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- Targeted policy/runner tests: ran 114 tests OK.
- `unittest discover`: ran 116 tests OK.
- `compileall`: OK.
- `slay_ai.train_card_model`: loaded 184 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 43 logs, applied 32 completed runs, skipped 11 incomplete runs.

2026-07-05: probe45 was started to validate the probe44 route fix, but became an execution diagnostic after `Fiend Fire` exhausted the hand and policy waited forever. Do not include probe45 or its continuations in default training.

Key evidence:

- Main log: `ai_runs_strategy_probe45/20260705_083255_ironclad_a4.jsonl`.
- F5 T3 used `Fiend Fire`, leaving `hand=[]`, energy 1, one Fungi Beast alive, and incoming 15. The policy repeatedly waited for a hand that would not be dealt until end turn.
- First continuation after the turn>1 fix: `ai_runs_strategy_probe45_continue_emptyhand_energyfix/20260705_084110_ironclad_a0.jsonl`.
- That continuation's first decision was `Hand is empty after spending energy; end the turn`, and it later reached F18 before exposing the same issue on turn 1 after another `Fiend Fire`.
- Second continuation after generalizing the fix: `ai_runs_strategy_probe45_continue_turn1_emptyhandfix/20260705_084740_ironclad_a0.jsonl`.
- That continuation's first decision also ended the empty hand turn and it progressed to F21 synthetic `GAME_OVER`.
- Recoverable action races continued to recur, including an invalid `proceed` at step 130 in the first continuation and invalid `end` at step 22 in the second continuation.

Implemented from probe45:

- Empty-hand combat now ends the turn whenever `0 < current_energy < 3`, not only after turn 1.
- Added regression tests for both turn>1 and turn1 empty-hand-after-spending-energy states, while keeping the first-turn empty/no-energy settling wait.

Validation after probe45 diagnostics:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_ends_turn_when_first_turn_empty_hand_after_spending_energy tests.test_policy.PolicyTests.test_combat_waits_on_first_turn_empty_hand_even_without_energy tests.test_policy tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- Targeted policy/runner tests: ran 117 tests OK.
- `unittest discover`: ran 118 tests OK.
- `compileall`: OK.
- `slay_ai.learn --reset`: read 43 logs, applied 32 completed runs, skipped 11 incomplete runs. The reset removed split-continuation incremental updates from `learned_memory`.

2026-07-05: repeated recoverable action races from probes42-45 were addressed in the runner execution layer.

Key evidence:

- probe42: invalid `end` when MCP had `[choose, potion, proceed, key, click, wait, save, state]`.
- probe43 continuation: invalid `end` during later combat.
- probe45 first continuation: invalid `proceed` when MCP had `[choose, potion, return, key, click, wait, save, state]`.
- probe45 second continuation: invalid `end` at step 22.
- Mill's earlier review identified the same root cause: stale policy decisions were executed without a uniform action legality preflight.

Implemented:

- `_execute_actions_with_settle` now calls `get_available_commands` before sending non-wait actions.
- If an action's command alias is absent, runner records `preflight_mismatch`, does not send the stale action, waits using the recoverable settle delay, rereads stable state, and lets the next loop replan.
- Added command alias mapping for `cancel`/`return`, `end_turn`/`end`, `use_potion`/`potion`, and other action names.
- Added a runner regression test proving unavailable stale `choose` actions are skipped instead of executed.

Validation:

```powershell
python -m unittest tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- `tests.test_runner`: ran 22 tests OK.
- `unittest discover`: ran 119 tests OK.
- `compileall`: OK.

2026-07-05: probe46 exposed an over-strict preflight loop.

Key evidence:

- Log: `ai_runs_strategy_probe46/20260705_085517_ironclad_a4.jsonl`.
- Opening Neow event upgraded a card, then the runner saw `GRID` with `confirm_up=true`.
- The policy planned `[{"action": "confirm"}]`, but MCP `get_available_commands` returned `cancel` and `proceed`, not `confirm`.
- The new preflight correctly refused the unavailable `confirm`, but without an equivalence rewrite it repeated `preflight_mismatch` until the diagnostic run was stopped.

Implemented after joint agent evaluation:

- `_preflight_actions` now returns the actual action list to execute plus rewrite metadata.
- A narrow `GRID` rewrite changes a single planned `confirm` to `proceed` only when the previous state is `GRID`, confirmation is ready or selection is complete, MCP exposes `proceed`, MCP does not expose `confirm`, and there are no remaining `choose` / `select_cards` commands.
- `action_result` records `executed_actions`, `rewrite_reason`, and `available_commands` when a rewrite occurs.
- Stale unavailable non-equivalent actions still return `preflight_mismatch` and are not sent.

Validation:

```powershell
python -m unittest tests.test_runner
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- `tests.test_runner`: ran 24 tests OK.
- `unittest discover`: ran 121 tests OK.
- `compileall`: OK.

2026-07-05: probe47 was a fresh validation after the `GRID` confirm/proceed rewrite. It is a clean completed failure and is included in default training.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 380 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save abandon --progress-file data\campaign_strategy_probe47.json --log-dir ai_runs_strategy_probe47 --use-all-hardware
```

Results:

- Target: `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe47/20260705_090405_ironclad_a4.jsonl`.
- Status: clean `GAME_OVER`, F14, 164 steps.
- `action_result` statuses: 163 `ok`, no action failures, no `preflight_mismatch`.
- The new rewrite fired twice as intended at steps 18 and 143: planned `confirm`, executed `proceed`, available commands `cancel` / `proceed`.
- Strategic failure evidence: at F12 with 51/98 HP and no potions, route scoring chose `?` over `M`; the event led to a forced F14 elite where Gremlin Nob killed the run.

Learning after probe47:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `slay_ai.train_card_model`: loaded 191 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 44 logs, applied 33 completed runs, skipped 11 incomplete runs.

Route patch after probe47:

- Faraday reviewed the clean F14 death and agreed the issue was a narrow late-Act-1 event-risk case, not a general reason to force hallway fights.
- `_map_node_score` now detects Act 1 floor >= 11, 35%-65% HP, no rest/shop choice, no high-impact elite potion, and `M` versus `?`.
- In that state, generic low-HP `M` penalties are reduced and `?` receives a late-event risk penalty so the probe47 F12 state prefers `M`.
- The rule does not trigger for early floors, extremely low HP, rest/shop options, or high-impact elite potions.

Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_map_late_act1_low_hp_prefers_monster_over_question_at_probe47_risk tests.test_policy.PolicyTests.test_map_early_act1_low_hp_still_prefers_question_over_monster tests.test_policy.PolicyTests.test_map_extremely_low_hp_late_act1_does_not_force_monster_over_question tests.test_policy
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Route-focused policy run: 100 tests OK.
- `unittest discover`: 124 tests OK.
- `compileall`: OK.

2026-07-05: probe48 validated the route patch enough for inclusion. It reached the Act 1 boss and ended as a synthetic game-over at F16.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 380 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe48.json --log-dir ai_runs_strategy_probe48 --use-all-hardware
```

Results:

- Target: `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe48/20260705_091457_ironclad_a4.jsonl`.
- Status: synthetic `GAME_OVER`, F16 boss, 203 steps.
- `action_result` statuses: 200 `ok`, 2 recovered `recoverable_error`, no unrecovered action failure.
- Late route evidence: F12 chose `M` at 46/88 HP when only `M` was available; F13 chose `?` over `E`, then F14 reached rest and F16 boss. The exact probe47 `M`/`?` fork did not recur, but the run avoided the previous F14 forced elite death.

Learning after probe48:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_subject_probe ai_runs_subject_probe2 --reset
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- `slay_ai.train_card_model`: loaded 199 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 45 logs, applied 34 completed runs, skipped 11 incomplete runs.
- `unittest discover`: 124 tests OK after retraining.
- `compileall`: OK.

2026-07-05: probe48 boss-fight inspection found a concrete combat sequencing miss: `Seeing Red` was not played before immediate energy payoffs such as `Whirlwind` / expensive attacks in the Hexaghost fight.

Patch:

- Added a narrow energy setup path for `Seeing Red`.
- Guarded it away from Gremlin Nob.
- Required an immediate payoff: X-cost, newly enabled expensive effective card, or useful hand cost exceeding current energy.

Validation:

```powershell
python -m unittest tests.test_policy
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Policy tests: 103 tests OK.
- `unittest discover`: 127 tests OK.
- `compileall`: OK.

2026-07-05: probe49 was a clean completed failure and is included in default training.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 400 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe49.json --log-dir ai_runs_strategy_probe49 --use-all-hardware
```

Results:

- Target: `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe49/20260705_092827_ironclad_a4.jsonl`.
- Status: `GAME_OVER`, F7, died to Sentries.
- Progress file: `data/campaign_strategy_probe49.json`, `last_status: game_over`, `last_steps: 104`, `last_floor: 7`.
- Evidence: F6 campfire at 60/80 HP chose smith while holding `BlessingOfTheForge`, `ElixirPotion`, and `SteroidPotion`; F7 Sentries fought with all three potions unused.

Patch after probe49:

- Treat `SteroidPotion`/strength potions as early elite/boss tempo in long fights.
- Treat `BlessingOfTheForge` as early long-fight tempo when useful.
- Count multi-enemy total live HP as a long fight, covering Sentries.
- Let Act 1 rest/smith logic distinguish elite-tempo potions from low-impact potions.

Validation and learning after probe49:

```powershell
python -m unittest tests.test_policy
python -m unittest discover -s tests
python -m compileall slay_ai tests
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `unittest discover`: 129 tests OK after the probe49 policy patch.
- `compileall`: OK.
- `slay_ai.train_card_model`: loaded 203 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 46 logs, applied 35 completed runs, skipped 11 incomplete runs.

2026-07-05: probe50 is diagnostic/excluded. It exposed a HAND_SELECT execution-layer mismatch.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe50.json --log-dir ai_runs_strategy_probe50 --use-all-hardware
```

Results:

- Log: `ai_runs_strategy_probe50/20260705_093533_ironclad_a4.jsonl`.
- No progress file was written because the process was manually stopped.
- Issue: policy planned `select_cards` on `HAND_SELECT`, but MCP available commands exposed `choose` and not `select_cards`; preflight looped on unavailable action(s).

Patch:

- Runner rewrites single-card `HAND_SELECT` `select_cards` actions to MCP `choose` when guarded by before-state screen type and available commands.
- Added a regression test for the rewrite.

Diagnostic continuation:

```powershell
python -m slay_ai.runner --max-steps 30 --interval 0.08 --startup-timeout 8 --log-dir ai_runs_strategy_probe50_continue_handselect_rewrite
```

Results:

- Log: `ai_runs_strategy_probe50_continue_handselect_rewrite/20260705_093831_ironclad_a0.jsonl`.
- The rewrite advanced through the F2 and F3 hand-select states.
- This split continuation is excluded from default training.
- `unittest discover`: 130 tests OK after the HAND_SELECT rewrite.
- `compileall`: OK.

2026-07-05: probe51 is infrastructure-blocked/excluded. It reached F16 Hexaghost, then MCPTheSpire became unreachable on port 8080.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save abandon --progress-file data\campaign_strategy_probe51.json --log-dir ai_runs_strategy_probe51 --use-all-hardware
```

Results:

- Log: `ai_runs_strategy_probe51/20260705_093933_ironclad_a4.jsonl`.
- Progress file: `data/campaign_strategy_probe51.json`, `last_status: read_failed`, `last_steps: 168`, `last_floor: null`.
- Error: `Cannot reach MCPTheSpire at http://127.0.0.1:8080/mcp`.
- Java process was still present, so classify this as MCP endpoint unreachable, not a strategy loss.

Recovery command used:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'java*' -and $_.CommandLine -like '*ModTheSpire*' } | Select-Object ProcessId,CommandLine
Stop-Process -Id 115300 -Force
Start-Process -FilePath "E:\steamApp\steamapps\common\SlayTheSpire\jre\bin\java.exe" -ArgumentList @("-jar","E:\steamApp\steamapps\workshop\content\646570\1605060445\ModTheSpire.jar","--skip-launcher","--mods","basemod,MCPTheSpire") -WorkingDirectory "E:\steamApp\steamapps\common\SlayTheSpire" -WindowStyle Hidden
```

Next required step: run `python -m slay_ai.mcp_watchdog --attempts 5 --delay 2 --recover-terminal --json`, confirm healthy state, then run fresh probe52.

Recovery verification after restart:

```powershell
python -m slay_ai.mcp_watchdog --attempts 5 --delay 2 --recover-terminal --json
python -c "from slay_ai.mcp_client import MCPClient; print(MCPClient().get_screen_state())"
```

Results:

- Watchdog status: `healthy`.
- Direct screen state: `MAIN_MENU`, `ready_for_command: True`, available commands `start`, `state`.
- Next live probe should be fresh probe52 from main menu.

2026-07-05: probe52 is diagnostic/excluded. It exposed a Neow event GRID duplicate-card selection loop and was manually stopped.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe52.json --log-dir ai_runs_strategy_probe52 --use-all-hardware
```

Results:

- Log: `ai_runs_strategy_probe52/20260705_095355_ironclad_a4.jsonl`.
- No progress file was written because the process was manually stopped.
- Issue: after Neow's transform/remove-style event grid, the policy selected duplicate `Strike_R` copies by UUID, but MCP's localized GRID choice handling effectively toggled the same visible duplicate instead of selecting a second distinct copy. The run stayed at F0 `GRID`.
- Training status: excluded from default training.

Patch:

- Event/purge GRID selection now skips all cards with the same stable card id/key after one copy has already been selected.
- Regression test added: `test_event_grid_skips_duplicate_card_ids_after_selection_for_probe52_neow_transform`.

Validation:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_event_grid_picks_low_value_card_for_transform tests.test_policy.PolicyTests.test_event_grid_skips_already_selected_cards tests.test_policy.PolicyTests.test_event_grid_skips_duplicate_card_ids_after_selection_for_probe52_neow_transform tests.test_policy.PolicyTests.test_event_grid_confirms_when_selection_count_is_complete
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Focused GRID tests: 4 tests OK.
- `unittest discover`: 131 tests OK.
- `compileall`: OK.

2026-07-05: probe53 validated the GRID duplicate-card fix and is included in default training.

Command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save abandon --progress-file data\campaign_strategy_probe53.json --log-dir ai_runs_strategy_probe53 --use-all-hardware
```

Results:

- Target: `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe53/20260705_100257_ironclad_a4.jsonl`.
- Progress file: `data/campaign_strategy_probe53.json`, `last_status: game_over`, `last_steps: 304`, `last_floor: 16`.
- Status: clean completed loss, synthetic `GAME_OVER` after a likely lethal Act 1 boss transition.
- Action results: 301 `ok`, 2 recovered `preflight_mismatch`, no unrecovered action failure.
- Evidence: Neow GRID no longer looped; run reached F16 boss. The two preflight mismatches were recovered transition races (`choose` after shop purchase state changed to `proceed`, and `proceed` during a reward/rest transition).

Learning after probe53:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `slay_ai.train_card_model`: loaded 213 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 47 logs, applied 36 completed runs, skipped 11 incomplete runs.
- `unittest discover`: 131 tests OK after retraining.
- `compileall`: OK.

2026-07-05: Stage 4 one-turn combat local search was introduced and probe54 validated it in live play.

Implementation:

- Added `slay_ai.combat_search.find_best_combat_sequence`.
- The search enumerates bounded one-turn sequences of currently visible, pure numeric cards and returns only the first `play_card` action.
- The runner still executes one action at a time and rereads state after each action.
- The policy falls back to the old single-card heuristic for unsupported hand-changing cards, protected control cards, Gremlin Nob nonlethal skill sequences, reflect-risk attacks, and still-lethal projected outcomes.

Validation before live probe:

```powershell
python -m unittest tests.test_policy.PolicyTests.test_combat_local_search_blocks_with_two_cards_to_avoid_lethal tests.test_policy.PolicyTests.test_combat_local_search_plays_energy_setup_before_x_cost_sequence tests.test_policy.PolicyTests.test_combat_local_search_does_not_override_disarm_without_lethal_gain tests.test_policy.PolicyTests.test_combat_local_search_does_not_sequence_skills_into_gremlin_nob tests.test_policy.PolicyTests.test_combat_local_search_does_not_plan_through_second_wind_hand_change tests.test_policy.PolicyTests.test_combat_local_search_does_not_open_with_reflect_lethal_attack
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Stage 4 focused tests: 6 tests OK.
- `unittest discover`: 137 tests OK.
- `compileall`: OK.

Probe54 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe54.json --log-dir ai_runs_strategy_probe54 --use-all-hardware
```

Probe54 results:

- Target: `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe54/20260705_102155_ironclad_a4.jsonl`.
- Progress file: `data/campaign_strategy_probe54.json`, `last_status: game_over`, `last_steps: 192`, `last_floor: 10`.
- Status: clean completed loss, synthetic `GAME_OVER` after a likely lethal Sentries transition.
- Action results: 190 `ok`, 1 recovered `recoverable_error`, no unrecovered action failure.
- `One-turn search` decisions: 22.
- Evidence: the search executed repeatedly without a loop or MCP command mismatch spike. The run exposed a next route/deck-evaluation issue: at 9/72 HP the path had only an elite next node after the chest, so future route planning must evaluate low-HP path commitments earlier, not only the immediate next node.

Learning after probe54:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `slay_ai.train_card_model`: loaded 221 card-pick examples and wrote 68 card deltas.
- `slay_ai.learn --reset`: read 48 logs, applied 37 completed runs, skipped 11 incomplete runs.
- `unittest discover`: 137 tests OK after retraining.
- `compileall`: OK.

2026-07-05: probe55 validated the probe54 low-HP rest-over-elite route patch enough for inclusion, then a review-driven P0 stabilization patch fixed MCP initialization and incoming-damage consistency.

Probe55 command:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 420 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe55.json --log-dir ai_runs_strategy_probe55 --use-all-hardware
```

Probe55 results:

- Target: `IRONCLAD:A4`.
- Log: `ai_runs_strategy_probe55/20260705_110914_ironclad_a4.jsonl`.
- Status: clean completed loss at F8 against Sentries.
- Action results: 135 `ok`, no recoverable or unrecovered action failures.
- `One-turn search` decisions: 15.
- Route evidence: the F5 elite was chosen at 68/88 HP, above the new low-HP rest threshold. Later F7 had only an elite next node at 41/88 HP, so full-map route lookahead remains the next route/deck-evaluation bottleneck.

Review-driven P0 fixes:

- `MCPClient.initialize()` is now idempotent and cached; `campaign` and `runner` use `ensure_initialized()` so a reused MCP session is not initialized twice.
- Invalid non-JSON MCP HTTP responses are wrapped as `MCPError` with a short body preview instead of leaking `JSONDecodeError`.
- Added `slay_ai.combat_math` so `combat_search`, policy, and runner snapshots all compute incoming damage from `move.damage * move.hits`, with stable fallbacks for `intent_damage`, `move_damage`, and `attack`.
- Regression tests cover idempotent initialization, invalid JSON wrapping, `move.damage * hits` local-search pressure, and runner snapshot incoming damage.

Validation:

```powershell
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- `unittest discover`: 141 tests OK.
- `compileall`: OK.

Learning after probe55:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_strategy_probe55 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_strategy_probe55 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

Results:

- `slay_ai.train_card_model`: loaded 227 card-pick examples and wrote 69 card deltas.
- `slay_ai.learn --reset`: read 49 logs, applied 38 completed runs, skipped 11 incomplete runs.

2026-07-05: The first behavior-preserving architecture split was applied after the structure review.

Scope:

- Created `slay_ai.mcp.client` as the implementation home for `MCPClient`, `MCPError`, and `MCPProbe`.
- Kept `slay_ai.mcp_client` as a compatibility shim for older scripts and docs.
- Created `slay_ai.core.state_reader` for state read retries, stable-frame checks, transient null-state detection, and state-read recovery.
- Created `slay_ai.domain.monsters` for shared monster incoming-damage interpretation.
- Kept `slay_ai.combat_math` as a compatibility shim.
- Updated `runner`, `campaign`, `mcp_watchdog`, `combat_search`, and `policy` imports to use the new package homes.

Validation:

```powershell
python -m unittest tests.test_mcp_client tests.test_runner.RunnerTests.test_transient_null_state_read_retries tests.test_runner.RunnerTests.test_empty_hand_after_first_turn_is_not_stable tests.test_runner.RunnerTests.test_debug_monster_intent_before_actions_is_not_stable tests.test_policy.PolicyTests.test_combat_local_search_counts_move_damage_hits
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Focused MCP/state/domain tests: 8 tests OK.
- `unittest discover`: 142 tests OK.
- `compileall`: OK.

Next architecture steps:

1. Split the remaining policy screens (`combat`, `card_reward`, `shop`, `rest`, `event`, `grid`) without behavior changes.
2. Add character policy modules only after the screen split is stable.
3. Move learning files under `learning/` and campaign helpers under `campaign/` after policy boundaries are clearer.
4. Implement route full-map lookahead inside the route policy boundary.

2026-07-05: Route/map policy was extracted from the large policy file without intended behavior changes.

Scope:

- Added `slay_ai.policy_decision.Decision`.
- Added `slay_ai.policy_route.decide_route` and route scoring helpers.
- `HeuristicPolicy._map` now delegates to `decide_route(game, self.memory)`.
- Removed the old in-class route scoring methods and route-only helper leftovers from `policy.py`.
- `runner` imports `Decision` from `policy_decision`.

Validation:

```powershell
python -m unittest tests.test_policy -k "map"
python -m unittest tests.test_runner tests.test_policy.PolicyTests.test_map_probe54_low_hp_prefers_rest_over_elite_even_with_fire_potion tests.test_policy.PolicyTests.test_map_late_act1_low_hp_prefers_monster_over_question_at_probe47_risk
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Results:

- Focused map tests: 20 tests OK.
- Runner plus selected map regression tests: 27 tests OK.
- `unittest discover`: 142 tests OK.
- `compileall`: OK.

## Main Thread Operating Loop

1. Run the current unlocked frontier, not an assumed A20 target. Use `slay_ai.unlocks` / campaign frontier mode to read local unlocks.
2. Inspect the newest JSONL log and campaign progress.
3. Make one or two evidence-backed fixes only.
4. Add a regression test for each concrete failure mode.
5. Run tests and compile checks.
6. Continue or restart the game and verify the fix in a real run.
7. Send Faraday strategy bottlenecks and Hegel execution/MCP bottlenecks as separate, scoped tasks.

## Active Agent Roles

2026-07-05 active long-running roles:

- Main thread: integrates code, tests, commits, pushes, and keeps the project moving on the current evidence-backed bottleneck.
- Archimedes `019f30f8-ed4d-7bd3-9519-b3bcfda8116d`: flow/run-data agent. Runs probes, classifies logs, updates manifest/shadow data, and reports failure chains. It must not edit code or commit.
- Program optimization agent: requested but not currently spawned because the multi-agent tool returned `agent thread limit reached`. Until a slot is available, main thread owns code changes.
- AI algorithm agent: requested but not currently spawned for the same thread-limit reason. Until a slot is available, main thread owns algorithm-roadmap updates and shadow-model design.

Latest coordination command:

```text
send_input Archimedes: run probe82 after commit 576b209, generate data\training_manifest_probe82.json and data\shadow_probe82, and report clean/trainable status plus the next actionable bottleneck.
```

2026-07-05 probe81 result:

- Command family: current-frontier Ironclad A4 campaign probe, output in `ai_runs_strategy_probe81`.
- Manifest: `data\training_manifest_probe81.json`.
- Shadow data: `data\shadow_probe81`.
- Category: 1 clean trainable completed failure.
- Failure: F16 Hexaghost, HP 6, no potions, hand contained two `Burn+` cards. One-turn search selected `Uppercut -> Defend_R` and reported `loss 8->1`, but the game ended because unblocked attack plus end-turn Burn damage was lethal.
- Flow-agent note: probe81 also shows resource pressure before the boss. The F11 Sentries fight consumed `SkillPotion` plus `Explosive Potion` and left the run at 40/80; F13 rested to 64/80, then F15 smith left the deck entering Hexaghost at 68/80 with no potions. Boss T2 `SkillPotion -> Disarm` was a positive decision, but output was too low and Hexaghost still had 64/250 HP at death.
- Program fix: model end-turn Burn/Burn+ damage inside combat search projected loss before probe82.

Probe82 should run only after the Burn/Burn+ search fix is committed and pushed:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe82.json --log-dir ai_runs_strategy_probe82 --use-all-hardware
python -m slay_ai.training_manifest ai_runs_strategy_probe82 --output data\training_manifest_probe82.json --shadow-dir data\shadow_probe82 --knowledge-dir data\static_knowledge
```

2026-07-05 probe82 result:

- Command family: current-frontier Ironclad A4 campaign probe, output in `ai_runs_strategy_probe82`.
- Manifest: `data\training_manifest_probe82.json`.
- Shadow data: `data\shadow_probe82`.
- Category: 1 clean trainable completed failure.
- Failure: F6 Gremlin Nob, HP 20, no potions. Search accepted `Defend_R -> Defend_R -> Headbutt` as `loss 24->14`, but Nob's `Anger` triggered on each Skill and live incoming rose 24 -> 27 -> 30, making the line lethal.
- Burn/Burn+ note: not live-validated in probe82 because the run died before Hexaghost/Burn pressure.
- Program fix: model Gremlin Nob `Anger`/Rage attack gain inside combat search when Skill cards are played.

Probe83 should run only after the Nob Rage search fix is committed and pushed:

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 440 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe83.json --log-dir ai_runs_strategy_probe83 --use-all-hardware
python -m slay_ai.training_manifest ai_runs_strategy_probe83 --output data\training_manifest_probe83.json --shadow-dir data\shadow_probe83 --knowledge-dir data\static_knowledge
```

## Useful Commands

Check current MCP screen:

```powershell
python -c "from slay_ai.mcp_client import MCPClient; print(MCPClient().get_screen_state())"
```

Run tests:

```powershell
python -m unittest discover -s tests
python -m compileall slay_ai tests
```

Retrain the first-stage card-value model from accumulated run logs:

```powershell
python -m slay_ai.train_card_model ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_strategy_probe55 ai_runs_strategy_probe56 ai_runs_strategy_probe57 ai_runs_subject_probe ai_runs_subject_probe2 --min-count 1 --max-delta 6
python -m slay_ai.learn ai_runs_live_probe ai_runs_probe ai_runs_strategy_probe3 ai_runs_strategy_probe3_continue ai_runs_strategy_probe4 ai_runs_strategy_probe5 ai_runs_strategy_probe6 ai_runs_strategy_probe7 ai_runs_strategy_probe8 ai_runs_strategy_probe8_continue_rewardfix ai_runs_strategy_probe9 ai_runs_strategy_probe10 ai_runs_strategy_probe11 ai_runs_strategy_probe12 ai_runs_strategy_probe13 ai_runs_strategy_probe14 ai_runs_strategy_probe15 ai_runs_strategy_probe16 ai_runs_strategy_probe17 ai_runs_strategy_probe18 ai_runs_strategy_probe20 ai_runs_strategy_probe21 ai_runs_strategy_probe22 ai_runs_strategy_probe24 ai_runs_strategy_probe25 ai_runs_strategy_probe26 ai_runs_strategy_probe27 ai_runs_strategy_probe28 ai_runs_strategy_probe29 ai_runs_strategy_probe31 ai_runs_strategy_probe32 ai_runs_strategy_probe33 ai_runs_strategy_probe34 ai_runs_strategy_probe35 ai_runs_strategy_probe36 ai_runs_strategy_probe37 ai_runs_strategy_probe40 ai_runs_strategy_probe41 ai_runs_strategy_probe42 ai_runs_strategy_probe44 ai_runs_strategy_probe47 ai_runs_strategy_probe48 ai_runs_strategy_probe49 ai_runs_strategy_probe53 ai_runs_strategy_probe54 ai_runs_strategy_probe55 ai_runs_strategy_probe56 ai_runs_strategy_probe57 ai_runs_subject_probe ai_runs_subject_probe2 --reset
```

PowerShell wildcard arguments such as `ai_runs_strategy_probe*` may not expand the way these scripts expect. Prefer explicit directory arguments until script-side glob expansion is added.

Run one current-frontier Ironclad attempt. The `--ascension 20` value is only the upper requested target; campaign should use local unlocks and run the current frontier, such as `IRONCLAD:A4` when A4 is unlocked.

```powershell
python -m slay_ai.campaign --characters IRONCLAD --ascension 20 --attempts-per-target 1 --max-steps 260 --interval 0.08 --startup-timeout 20 --cooldown 0.5 --existing-save fail --progress-file data\campaign_strategy_probe.json --log-dir ai_runs_strategy_probe --use-all-hardware
```

Continue controlling an already in-dungeon state. Do not use `--continue` when the current MCP state is already inside combat/dungeon; `--continue` is only for the main-menu continue command.

```powershell
python -m slay_ai.runner --max-steps 180 --interval 0.08 --startup-timeout 8 --log-dir ai_runs_continue_probe
```

Restart ModTheSpire/MCP when the MCP server is stuck in `Internal error: null`:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'java*' -and $_.CommandLine -like '*ModTheSpire*' } | Select-Object ProcessId,CommandLine
Stop-Process -Id <PID> -Force
Start-Process -FilePath "E:\steamApp\steamapps\common\SlayTheSpire\jre\bin\java.exe" -ArgumentList @("-jar","E:\steamApp\steamapps\workshop\content\646570\1605060445\ModTheSpire.jar","--skip-launcher","--mods","basemod,MCPTheSpire") -WorkingDirectory "E:\steamApp\steamapps\common\SlayTheSpire" -WindowStyle Hidden
```

## When To Consider A Custom MCP Service

Do not rewrite MCP first. Prefer runner-side waits, retries, and state validation while MCPTheSpire remains usable.

Escalate to a custom or patched MCP service when at least one of these repeats:

- `Internal error: null` regularly prevents reading death, game-over, or transition states.
- The current service cannot expose enough legal-action metadata to prevent stale card/choice indices.
- The script needs richer combat data, such as powers, draw/discard piles, exact card flags, or available commands, and MCPTheSpire cannot provide it reliably.
- Long campaign automation is blocked more by MCP protocol failures than by AI strategy mistakes.





