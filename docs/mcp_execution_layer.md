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
