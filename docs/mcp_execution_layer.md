# MCP Execution Layer Notes

## Current Assessment

The repeated `Internal error: null` after death or room transition is most likely a MCPTheSpire state-conversion null dereference, not just an HTTP/client timing issue.

Evidence:

- `ai_runs_strategy_probe3_continue/20260705_022859_ironclad_a0.jsonl` reaches F14 combat at 2 HP, sends `end_turn`, then every `get_game_state` retry returns `Internal error: null`.
- MCPTheSpire `GameStateConverter.getCommunicationState()` and `getScreenOnlyState()` both dereference live Slay the Spire objects such as `AbstractDungeon.getCurrRoom()`, `AbstractDungeon.player`, and screen objects.
- During death, save/quit, and dungeon teardown, those objects can be temporarily or permanently null even though the HTTP MCP server still responds.

Low-risk client fixes are still useful:

- Retry transient null reads.
- Add action-aware settle waits.
- Probe protocol health separately from game-state health.
- If the previous frame was a likely lethal combat state and MCP state reads are stuck, synthesize a terminal death state so the run is recorded as `game_over` rather than `read_failed`.
- Keep `MCPClient.initialize()` idempotent when a campaign passes the same client into `run_episode()`, so a live `Mcp-Session-Id` is not initialized twice.
- Wrap non-JSON HTTP responses as `MCPError` with a short response preview, so CLI entrypoints can handle MCP/proxy output failures consistently.

Implemented client safeguards:

- `MCPClient.initialize()` caches its result and returns it on later calls.
- `MCPClient.ensure_initialized()` is the preferred call site for campaign and runner startup.
- JSON-RPC response decoding errors are converted to `MCPError`.
- The implementation now lives in `slay_ai.mcp.client`; `slay_ai.mcp_client` is only a compatibility import layer.
- State read retry/stability logic now lives in `slay_ai.core.state_reader`.
- State reads now validate the returned payload shape before the runner uses it. `None`, empty objects, or `in_game=true` payloads without `game_state` are treated like transient `read_state_failed` responses and enter the same retry/recovery path as `Internal error: null`.
- Runner startup now treats an already in-dungeon state as a valid continuation instead of calling MCP `continue_game` or retrying `start_game` into a live combat.
- Runner preflight can rewrite a completed CHEST `choose` probe to `proceed` when MCP exposes only `proceed`, keeping the bot from crashing while still making the skipped-relic case visible in logs.
- Runner preflight can also rewrite an unverified CHEST `proceed` to `choose 1` when MCP exposes `choose` instead of `proceed`, and the same narrow rewrite is retried if `proceed` fails with possible command `choose`.
- Runner preflight rejects stale targeted `play_card` and targeted `use_potion` actions before MCP execution, including dead/missing monsters and unavailable potion slots.

## 2026-07-05 A0 Execution Findings

The current short-term milestone is stable A0 Act 1 boss completion before returning to ascension climbing. Probe91 (`runs/ai_runs_strategy_probe91_a0/20260705_201624_ironclad_a0.jsonl`) reached F19 after beating Slime Boss, but was manually stopped and classified as `diagnostic_excluded` / `no_terminal_outcome`. Probe94 (`runs/ai_runs_strategy_probe94_a0/20260705_204121_ironclad_a0.jsonl`) repeated the Act 1 clear, reached Act 2 F31, and is classified clean/trainable in `data/training_manifest_probe94_a0.json`. Probe95 (`runs/ai_runs_strategy_probe95_a0/20260705_205543_ironclad_a0.jsonl`) again cleared Act 1, reached Act 2 F21, and is classified clean/trainable in `data/training_manifest_probe95_a0.json`.

Two MCP/execution issues are now P0 for stability:

- CHEST states can report `room_phase=COMPLETE`, `screen_state.chest_open=false`, and `screen_state.rewards=[]`. In probe91, F9 and F17 both proceeded with only Burning Blood in the relic list, so this was a real skipped-relic failure, not merely missing log text. Probe94 live-validated the runner-side guard: F9, F17, and F26 all probed the chest before proceeding, and F9/F26 explicitly logged `Collect RELIC`.
- Start/continue transitions can race against the live dungeon state. A new A0 run may already be inside combat after clearing a terminal/passive screen, and `--continue` may be called while already in dungeon. Runner now checks the current state before issuing MCP start/continue commands.
- `GAME_OVER` can still leave MCP state tools broken even when protocol and available-command probes respond. Probe94 ended at F31 with `get_available_commands` reporting `GAME_OVER` + `proceed`, while `get_screen_state` and `get_game_state` returned `Internal error: null`; watchdog `--recover-terminal` recovered the terminal screen and the runner recorded a synthetic game-over. Runner now performs the same terminal `proceed` recovery inside the read-failure path. Probe95 live-validated this: the synthetic terminal event records `terminal_recovery_attempted=true` and `terminal_recovery_succeeded=true`, and a follow-up watchdog showed MCP healthy at `MAIN_MENU`.

Immediate runner-side fixes:

- CHEST policy is split into `slay_ai.policy_chest` and treats `chest_open != true` with empty rewards as an unverified chest. It probes `choose 1` once per floor before allowing `proceed`.
- CHEST snapshots now record `chest_open` and visible rewards, so future logs can prove whether MCP exposed a relic reward.
- Runner execution now guards the opposite CHEST race too: if a stale `proceed` reaches an unverified CHEST whose live command surface has fallen back to `choose`, it rewrites to `choose 1` rather than recording a recovered action race.
- State-read terminal recovery now sends `proceed` when health diagnostics show a terminal `GAME_OVER` with `proceed`, then waits briefly for available commands to leave `GAME_OVER` before writing post-recovery diagnostics.
- Action preflight now rejects stale targeted `play_card` and targeted `use_potion` actions before MCP execution when the card/potion slot is gone or unavailable, the monster index is out of range, or the target monster is already dead/gone/0 HP. The runner records these as recoverable `preflight_mismatch` events and lets the next stable frame replan.
- Reward policy now waits once on an empty, incomplete combat-reward screen before proceeding, and normalizes reward-type names before deciding whether to collect gold, relics, potions, or card rewards.
- Runner execution now treats post-combat transition errors as a wait/re-read instead of a recovered action race when a stale combat frame sends `end_turn` or `play_card` after MCP has already moved to reward commands such as `choose`/`proceed`. Probe102 exposed both shapes in Act 2 after a clean Guardian clear.
- Training manifests summarize action-race recovery kinds in both per-run `action_recovery_summary` and batch `summary.action_recovery`, keeping unrecovered races separate from recovered ones, so diagnostic handoffs and Act 1 boss gate reports can tell whether a run or batch involved stale card targets, stale potion targets, stale potion slots, rewritten UI actions, unavailable commands, or an unresolved execution issue.
- Training manifests now also classify repeated no-terminal `COMBAT_REWARD`, `CARD_REWARD`, `BOSS_REWARD`, and `CHEST` tails as `infra_blocked` screen stalls with reward/relic/chest evidence, so reward-collection failures are not mistaken for strategy deaths or clean training data.
- Training manifests preserve compact infra evidence for MCP read/null failures and action/preflight errors in `failure_evidence.mcp_read`, `failure_evidence.synthetic_terminal`, and `failure_evidence.action_error`, and `summary.terminal_recovery` rolls synthetic terminal recovery into attempted/succeeded/failed/unhealthy counters. `run_diagnosis`, `run_status`, `offline_batch`, and `act1_boss_gate` surface the same context in status lines and engineering handoffs, so monitors can distinguish normal strategy deaths from MCP terminal-recovery instability without opening every JSONL.

Next MCP-side investigation:

1. Patch or wrap MCPTheSpire terminal/GameOver serialization so `get_screen_state` and `get_game_state` return a null-safe terminal object instead of `Internal error: null`.
2. Add service-side fields for `available_choices`, `choice_count`, and chest/reward identifiers so Python can reject stale empty-choice states before losing a relic.
3. Keep the runner-side CHEST probe guard even after a service patch; it is now live-validated and low risk.
4. Consider a narrow custom MCP patch if terminal-state or CHEST reward visibility remains impossible to recover from runner-side probing.

## Watchdog

Run:

```powershell
python -m slay_ai.mcp_watchdog --attempts 3 --delay 1 --json
```

If the protocol is alive and `get_available_commands` reports a terminal `GAME_OVER` screen with `proceed`, the state tools can often be recovered without restarting:

```powershell
python -m slay_ai.mcp_watchdog --attempts 3 --delay 1 --recover-terminal --json
```

Statuses:

- `healthy`: state tools are responding.
- `state_broken`: MCP protocol responds, but state tools fail. Try `--recover-terminal` if the available commands show a terminal `GAME_OVER`; otherwise restart ModTheSpire/MCPTheSpire.
- `unreachable`: endpoint is down, refused, or blocked. Treat the current run as infrastructure-blocked, restart ModTheSpire/MCPTheSpire, and exclude the split log from training unless a later clean terminal result is produced in a clearly named continuation.

## Probe51 Unreachable Case

`ai_runs_strategy_probe51/20260705_093933_ironclad_a4.jsonl` reached F16 Hexaghost, then failed at step 168 with `Cannot reach MCPTheSpire at http://127.0.0.1:8080/mcp`. A Java ModTheSpire process was still present, so this should be logged as endpoint-level `unreachable`, not as a strategy death.

Before the next live probe, verify recovery with:

```powershell
python -m slay_ai.mcp_watchdog --attempts 5 --delay 2 --recover-terminal --json
python -c "from slay_ai.mcp_client import MCPClient; print(MCPClient().get_screen_state())"
```

Only after both are healthy should the main loop run probe52 or a clearly named continuation. Do not expand combat policy based on a run that ended as `read_failed`.

## Minimal MCP Patch Plan

If patching MCPTheSpire, the smallest viable service-side fix is:

1. Add a null-safe snapshot guard at the start of `GameStateConverter.getCommunicationState()` and `getScreenOnlyState()`.
2. Treat these as first-class terminal/transition states:
   - `CardCrawlGame.mode != GAMEPLAY`
   - `AbstractDungeon.player == null`
   - `AbstractDungeon.currMapNode == null`
   - `AbstractDungeon.getCurrRoom() == null`
   - `AbstractDungeon.deathScreen != null`
   - `AbstractDungeon.victoryScreen != null`
3. Return a valid JSON object instead of throwing:
   - `in_game`
   - `ready_for_command`
   - `game_state.screen_type`
   - `game_state.room_phase`
   - `game_state.floor`
   - `game_state.current_hp`
   - `game_state.screen_state.victory`
   - `game_state.screen_state.score`
   - `transition_state` or `terminal_state`
4. Wrap per-section conversion so one broken section returns `{error: ...}` instead of failing the entire tool.
5. Make `get_available_commands` safe in terminal/transition states and return only `state`, `start`, `continue`, or no mutating commands as appropriate.

Recommended new/changed tools:

- Keep `get_game_state`, but make it null-safe.
- Keep `get_screen_state`, but make it the safest endpoint.
- Add `get_protocol_status` or `get_runtime_status` with no `AbstractDungeon.getCurrRoom()` dependency.
- Keep `execute_actions`, but return partial execution metadata: executed count, failed action index, current command list, and whether the error is recoverable.

## Recommendation

Do not fully self-rewrite MCP yet. A small MCPTheSpire patch is justified if `state_broken` repeats during long campaign runs, because the current failure happens below the Python runner. The best next step is a forked MCPTheSpire patch focused only on null-safe state serialization and terminal GameOver handling, not a new protocol or large mod rewrite.
