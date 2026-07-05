"""Command line runner for the growing Slay the Spire AI."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .core.state_reader import (
    combat_snapshot_is_settling as _combat_snapshot_is_settling,
    is_transient_state_error as _is_transient_state_error,
    looks_stable as _looks_stable,
    probe_mcp_health as _probe_mcp_health,
    read_game_state as _read_game_state,
    read_stable_game_state as _read_stable_game_state,
    recover_state_read as _recover_state_read,
)
from .domain.monsters import incoming_damage
from .memory import StrategyMemory
from .mcp.client import MCPClient, MCPError
from .policy import HeuristicPolicy
from .policy_decision import Decision


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class EpisodeResult:
    status: str
    log_path: Path
    steps: int
    victory: bool | None = None
    floor: int | None = None
    score: int | None = None


@dataclass
class ActionExecution:
    status: str
    latency_ms: int
    settle_ms: int
    last_error: str | None = None
    recovered: bool | None = None
    recovery_error: str | None = None
    executed_actions: list[dict[str, Any]] | None = None
    rewrite_reason: str | None = None
    available_commands: list[str] | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a growing AI against MCPTheSpire.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/mcp")
    parser.add_argument("--character", default="IRONCLAD", choices=["IRONCLAD", "SILENT", "DEFECT", "WATCHER"])
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--seed")
    parser.add_argument("--start", action="store_true", help="Start a new run from main menu.")
    parser.add_argument("--continue", dest="continue_run", action="store_true", help="Continue an existing save.")
    parser.add_argument(
        "--existing-save",
        choices=["fail", "continue", "abandon"],
        default="fail",
        help="What to do if --start is blocked by an existing save.",
    )
    parser.add_argument("--max-steps", type=int, default=300)
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--startup-timeout", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true", help="Print one decision without executing it.")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runs" / "ai_runs")
    args = parser.parse_args(argv)

    try:
        result = run_episode(
            endpoint=args.endpoint,
            character=args.character,
            ascension=args.ascension,
            seed=args.seed,
            start=args.start,
            continue_run=args.continue_run,
            existing_save=args.existing_save,
            max_steps=args.max_steps,
            interval=args.interval,
            startup_timeout=args.startup_timeout,
            dry_run=args.dry_run,
            log_dir=args.log_dir,
            echo=True,
        )
    except MCPError as exc:
        print(f"MCP error: {exc}", file=sys.stderr)
        return 2
    if result.status == "action_failed":
        return 3
    return 0


def run_episode(
    *,
    endpoint: str = "http://127.0.0.1:8080/mcp",
    character: str = "IRONCLAD",
    ascension: int = 0,
    seed: str | None = None,
    start: bool = False,
    continue_run: bool = False,
    existing_save: str = "fail",
    max_steps: int = 300,
    interval: float = 0.15,
    startup_timeout: float = 15.0,
    dry_run: bool = False,
    log_dir: Path = ROOT / "runs" / "ai_runs",
    echo: bool = False,
    client: MCPClient | None = None,
    memory: StrategyMemory | None = None,
) -> EpisodeResult:
    memory = memory or StrategyMemory.load()
    policy = HeuristicPolicy(memory, character=character)
    client = client or MCPClient(endpoint)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{character.lower()}_a{ascension}.jsonl"

    if dry_run and (start or continue_run):
        return EpisodeResult("dry_run", log_path, 0)

    if hasattr(client, "ensure_initialized"):
        client.ensure_initialized()
    else:
        client.initialize()
    launched_or_continued = False
    if start:
        start_args: dict[str, Any] = {"character": character, "ascension": ascension}
        if seed:
            start_args["seed"] = seed
        try:
            message = client.call_tool("start_game", start_args)
        except MCPError as exc:
            if "Possible commands" in str(exc) and _recover_terminal_game_over_before_start(client):
                if _current_run_matches_start(client, character, ascension):
                    message = "Continuing target run after clearing terminal screen"
                else:
                    message = client.call_tool("start_game", start_args)
            elif "Possible commands" not in str(exc) or existing_save == "fail":
                raise
            elif existing_save == "continue":
                if _is_in_game(client):
                    message = "Continuing current in-dungeon run"
                else:
                    message = client.call_tool("continue_game", {})
            elif existing_save == "abandon":
                _return_to_menu_for_abandon(client)
                client.call_tool("abandon_run", {})
                message = client.call_tool("start_game", start_args)
            else:
                raise
        if echo:
            print(message)
        launched_or_continued = True
    elif continue_run:
        if _is_in_game(client):
            message = "Continuing current in-dungeon run"
        else:
            message = client.call_tool("continue_game", {})
        if echo:
            print(message)
        launched_or_continued = True

    episode_card_picks: list[str] = []

    if launched_or_continued:
        _wait_for_game_ready(client, timeout=startup_timeout)

    last_state: dict[str, Any] | None = None
    last_actions: list[dict[str, Any]] = []
    shop_left_floors: set[int] = set()
    for step in range(1, max_steps + 1):
        try:
            state = _read_stable_game_state(client)
        except MCPError as exc:
            if _is_transient_state_error(exc):
                try:
                    state = _recover_state_read(client, interval)
                except MCPError as final_exc:
                    diagnostics = _probe_mcp_health(client)
                    terminal_recovery_attempted = _diagnostics_show_game_over(diagnostics)
                    terminal_recovery_succeeded = False
                    post_recovery_diagnostics = None
                    if terminal_recovery_attempted:
                        terminal_recovery_succeeded = _recover_terminal_game_over_after_state_failure(client)
                        if terminal_recovery_succeeded:
                            post_recovery_diagnostics = _probe_mcp_health(client)
                    synthetic_state = _synthetic_terminal_state_after_read_failure(last_state, last_actions, diagnostics)
                    if synthetic_state is not None:
                        _write_event(
                            log_path,
                            step,
                            "synthetic_terminal_state",
                            last_error=str(final_exc),
                            previous_error=str(exc),
                            previous_actions=last_actions,
                            diagnostics=diagnostics,
                            terminal_recovery_attempted=terminal_recovery_attempted,
                            terminal_recovery_succeeded=terminal_recovery_succeeded,
                            post_recovery_diagnostics=post_recovery_diagnostics,
                        )
                        _write_state_record(
                            log_path,
                            step,
                            synthetic_state,
                            [],
                            "MCP state reads failed after a likely lethal transition; synthesize game over.",
                            should_stop=True,
                        )
                        memory.record_outcome(synthetic_state, episode_card_picks)
                        outcome = _outcome_from_state(synthetic_state)
                        if echo:
                            print(
                                f"Run likely ended during MCP null state at step {step}; recorded synthetic game over.",
                                file=sys.stderr,
                            )
                        return EpisodeResult(
                            status="game_over",
                            log_path=log_path,
                            steps=step,
                            victory=outcome.get("victory"),
                            floor=outcome.get("floor"),
                            score=outcome.get("score"),
                        )
                    _write_error(
                        log_path,
                        step,
                        f"read_state_failed: {final_exc}",
                        [],
                        last_error=str(exc),
                        diagnostics=diagnostics,
                    )
                    if echo:
                        print(f"State read failed at step {step}: {final_exc}", file=sys.stderr)
                    return EpisodeResult("read_failed", log_path, step)
                _write_event(log_path, step, "state_read_recovered", last_error=str(exc))
            else:
                _write_error(log_path, step, f"read_state_failed: {exc}", [])
                if echo:
                    print(f"State read failed at step {step}: {exc}", file=sys.stderr)
                return EpisodeResult("read_failed", log_path, step)

        previous_state = last_state
        if not state.get("in_game") and previous_state and previous_state.get("in_game"):
            synthetic_state = _synthetic_terminal_state_after_main_menu(previous_state)
            if synthetic_state is not None:
                _write_event(
                    log_path,
                    step,
                    "synthetic_terminal_state",
                    previous_actions=last_actions,
                    source="main_menu_after_in_game",
                )
                _write_state_record(
                    log_path,
                    step,
                    synthetic_state,
                    [],
                    "Game returned to main menu after an in-game state; synthesize game over.",
                    should_stop=True,
                )
                memory.record_outcome(synthetic_state, episode_card_picks)
                outcome = _outcome_from_state(synthetic_state)
                if echo:
                    print(f"Run ended at main menu after in-game state at step {step}; recorded synthetic game over.")
                return EpisodeResult(
                    status="game_over",
                    log_path=log_path,
                    steps=step,
                    victory=outcome.get("victory"),
                    floor=outcome.get("floor"),
                    score=outcome.get("score"),
                )

        last_state = state
        game = state.get("game_state", {})
        floor = int(game.get("floor", -1) or -1)
        if game.get("screen_type") == "SHOP_ROOM" and floor in shop_left_floors:
            decision = Decision([{"action": "proceed"}], "Shop already left; proceed.")
        else:
            decision = policy.decide(state)
        record = _write_state_record(
            log_path,
            step,
            state,
            decision.actions,
            decision.reason,
            should_stop=decision.should_stop,
            learn_card_pick=decision.learn_card_pick,
        )

        if echo:
            print(f"[{step}] {record['summary']} -> {decision.reason}")
        if decision.learn_card_pick:
            memory.record_card_pick(decision.learn_card_pick)
            episode_card_picks.append(decision.learn_card_pick)
            memory.save()

        if decision.should_stop:
            memory.record_outcome(state, episode_card_picks)
            outcome = _outcome_from_state(state)
            if echo:
                print(f"Run ended. Learned memory updated: {memory.learned_path}")
            return EpisodeResult(
                status="game_over",
                log_path=log_path,
                steps=step,
                victory=outcome.get("victory"),
                floor=outcome.get("floor"),
                score=outcome.get("score"),
            )

        if dry_run:
            if echo:
                print(json.dumps(decision.actions, ensure_ascii=False, indent=2))
                print(f"Dry run log: {log_path}")
            return EpisodeResult("dry_run", log_path, step)

        if decision.actions:
            try:
                action_result = _execute_actions_with_settle(client, decision.actions, interval, before_state=state)
            except MCPError as exc:
                _write_error(log_path, step, str(exc), decision.actions)
                if echo:
                    print(f"Action failed at step {step}: {exc}", file=sys.stderr)
                return EpisodeResult("action_failed", log_path, step)
            _write_action_result(log_path, step, decision.actions, action_result)
            if state.get("game_state", {}).get("screen_type") == "SHOP_SCREEN" and _has_action(decision.actions, "cancel"):
                shop_left_floors.add(floor)
            last_actions = action_result.executed_actions or decision.actions
            if echo and action_result.status in {"recoverable_error", "preflight_mismatch"}:
                print(f"Recoverable action race at step {step}: {action_result.last_error}", file=sys.stderr)
        else:
            last_actions = []
            time.sleep(interval)

    if echo:
        print(f"Stopped after max steps. Log: {log_path}")
    return EpisodeResult("max_steps", log_path, max_steps)


def _wait_for_game_ready(client: MCPClient, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            state = _read_game_state(client, attempts=2, delay=0.2)
        except MCPError:
            time.sleep(0.3)
            continue
        last_state = state
        if state.get("in_game"):
            return
        time.sleep(0.3)
    raise MCPError(f"Game did not enter dungeon within {timeout:.1f}s after launch; last_state={last_state}")


def _is_in_game(client: MCPClient) -> bool:
    try:
        if hasattr(client, "get_screen_state"):
            state = client.get_screen_state()
        else:
            state = client.get_game_state()
        return bool(state.get("in_game"))
    except MCPError:
        return False


def _current_run_matches_start(client: MCPClient, character: str, ascension: int) -> bool:
    try:
        if hasattr(client, "get_screen_state"):
            state = client.get_screen_state()
        else:
            state = client.get_game_state()
    except MCPError:
        return False
    if not state.get("in_game"):
        return False
    game = state.get("game_state", {})
    try:
        current_ascension = int(game.get("ascension_level"))
    except (TypeError, ValueError):
        return False
    return str(game.get("class", "")).upper() == character.upper() and current_ascension == int(ascension)


def _recover_terminal_game_over_before_start(client: MCPClient) -> bool:
    try:
        commands = client.call_tool("get_available_commands", {})
    except MCPError:
        return False
    available = _available_tool_names(commands)
    if "proceed" not in available:
        return False
    if commands.get("screen_type") != "GAME_OVER":
        passive_commands = {"proceed", "key", "click", "wait", "save", "state"}
        if available - passive_commands:
            return False
    try:
        client.execute_actions([{"action": "proceed"}])
    except MCPError:
        return False
    _wait_for_terminal_commands_to_clear(client)
    return True


def _recover_terminal_game_over_after_state_failure(client: MCPClient) -> bool:
    try:
        commands = client.call_tool("get_available_commands", {})
    except MCPError:
        return False
    if commands.get("screen_type") != "GAME_OVER":
        return False
    available = _available_tool_names(commands)
    if "proceed" not in available:
        return False
    try:
        client.execute_actions([{"action": "proceed"}])
    except MCPError:
        return False
    _wait_for_terminal_commands_to_clear(client)
    return True


def _wait_for_terminal_commands_to_clear(client: MCPClient, attempts: int = 6, delay: float = 0.5) -> None:
    for _ in range(max(1, attempts)):
        time.sleep(delay)
        try:
            commands = client.call_tool("get_available_commands", {})
        except MCPError:
            continue
        if commands.get("screen_type") != "GAME_OVER" or not commands.get("in_game", True):
            return


def _return_to_menu_for_abandon(client: MCPClient, timeout: float = 15.0) -> None:
    if not _is_in_game(client):
        return
    client.call_tool("save_game", {})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_in_game(client):
            return
        time.sleep(0.3)
    raise MCPError("Could not return to main menu before abandoning existing save")


def _execute_actions_with_settle(
    client: MCPClient,
    actions: list[dict[str, Any]],
    interval: float,
    before_state: dict[str, Any] | None = None,
) -> ActionExecution:
    preflight_started = time.monotonic()
    actions_to_execute, rewrite_reason, preflight_error, available_commands = _preflight_actions(
        client,
        actions,
        before_state=before_state,
    )
    if preflight_error:
        latency_ms = _elapsed_ms(preflight_started)
        settle = _settle_delay_for_actions(actions_to_execute, interval, recoverable=True)
        time.sleep(settle)
        recovered = False
        recovery_error = None
        try:
            _read_stable_game_state(client, attempts=12, delay=0.2)
            recovered = True
        except MCPError as read_exc:
            recovery_error = str(read_exc)
        return ActionExecution(
            status="preflight_mismatch",
            latency_ms=latency_ms,
            settle_ms=int(settle * 1000),
            last_error=preflight_error,
            recovered=recovered,
            recovery_error=recovery_error,
            executed_actions=actions_to_execute if rewrite_reason else None,
            rewrite_reason=rewrite_reason,
            available_commands=available_commands,
        )

    started = time.monotonic()
    try:
        client.execute_actions(actions_to_execute)
    except MCPError as exc:
        latency_ms = _elapsed_ms(started)
        if not _is_recoverable_action_error(exc):
            raise
        settle = _settle_delay_for_actions(actions_to_execute, interval, recoverable=True)
        time.sleep(settle)
        recovered = False
        recovery_error = None
        try:
            _read_stable_game_state(client, attempts=12, delay=0.2)
            recovered = True
        except MCPError as read_exc:
            recovery_error = str(read_exc)
        return ActionExecution(
            status="recoverable_error",
            latency_ms=latency_ms,
            settle_ms=int(settle * 1000),
            last_error=str(exc),
            recovered=recovered,
            recovery_error=recovery_error,
            executed_actions=actions_to_execute if rewrite_reason else None,
            rewrite_reason=rewrite_reason,
            available_commands=available_commands if rewrite_reason else None,
        )

    latency_ms = _elapsed_ms(started)
    settle = _settle_delay_for_actions(actions_to_execute, interval)
    time.sleep(settle)
    if before_state is not None and _has_action(actions_to_execute, "end_turn"):
        settle += _wait_for_end_turn_transition(client, before_state, interval)
    if before_state is not None and _has_action(actions_to_execute, "choose"):
        settle += _wait_for_rest_transition(client, before_state, interval)
    if before_state is not None and (
        _has_action(actions_to_execute, "choose")
        or _has_action(actions_to_execute, "confirm")
        or _has_action(actions, "confirm")
    ):
        settle += _wait_for_grid_transition(client, before_state, actions, interval)
    return ActionExecution(
        status="ok",
        latency_ms=latency_ms,
        settle_ms=int(settle * 1000),
        executed_actions=actions_to_execute if rewrite_reason else None,
        rewrite_reason=rewrite_reason,
        available_commands=available_commands if rewrite_reason else None,
    )


def _preflight_actions(
    client: MCPClient,
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None, str | None, list[str] | None]:
    actionable = [action for action in actions if str(action.get("action", "")).lower() != "wait"]
    if not actionable:
        return actions, None, None, None
    try:
        commands = client.call_tool("get_available_commands", {})
    except MCPError:
        return actions, None, None, None
    available = _available_tool_names(commands)
    if not available:
        return actions, None, None, None
    available_commands = sorted(available)
    actions_to_execute, rewrite_reason = _rewrite_actions_for_available_commands(
        actions,
        available,
        before_state=before_state,
    )
    missing = _unavailable_action_names(actions_to_execute, available)
    if not missing:
        return actions_to_execute, rewrite_reason, None, available_commands if rewrite_reason else None
    error = f"Preflight unavailable action(s): {missing}; available commands: {sorted(available)}"
    return actions_to_execute, rewrite_reason, error, available_commands


def _unavailable_action_names(actions: list[dict[str, Any]], available: set[str]) -> list[str]:
    missing = []
    for action in actions:
        name = str(action.get("action", "")).lower()
        if name == "wait":
            continue
        aliases = _action_tool_aliases(name)
        if aliases and not (aliases & available):
            missing.append(name)
    return missing


def _rewrite_actions_for_available_commands(
    actions: list[dict[str, Any]],
    available: set[str],
    before_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    hand_select_rewrite = _rewrite_hand_select_to_choose(actions, available, before_state)
    if hand_select_rewrite is not None:
        return hand_select_rewrite, "Preflight action rewrite: hand select->choose."
    if _can_rewrite_grid_confirm_to_proceed(actions, available, before_state):
        return [{"action": "proceed"}], "Preflight action rewrite: grid confirm->proceed."
    if _can_rewrite_chest_choose_to_proceed(actions, available, before_state):
        return [{"action": "proceed"}], "Preflight action rewrite: chest choose->proceed."
    return actions, None


def _rewrite_hand_select_to_choose(
    actions: list[dict[str, Any]],
    available: set[str],
    before_state: dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    if len(actions) != 1:
        return None
    action = actions[0]
    if str(action.get("action", "")).lower() != "select_cards":
        return None
    if "choose" not in available or "select_cards" in available:
        return None
    if before_state is None:
        return None
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "HAND_SELECT":
        return None
    drop = action.get("drop") or action.get("cards") or action.get("indices")
    if not isinstance(drop, list) or not drop:
        return None
    choose_actions: list[dict[str, Any]] = []
    for item in drop:
        try:
            choice_index = int(item)
        except (TypeError, ValueError):
            return None
        if choice_index <= 0:
            return None
        choose_actions.append({"action": "choose", "choice_index": choice_index})
    if len(choose_actions) > 1:
        if "proceed" not in available:
            return None
        choose_actions.append({"action": "proceed"})
    return choose_actions


def _can_rewrite_grid_confirm_to_proceed(
    actions: list[dict[str, Any]],
    available: set[str],
    before_state: dict[str, Any] | None,
) -> bool:
    if len(actions) != 1:
        return False
    if str(actions[0].get("action", "")).lower() != "confirm":
        return False
    if "proceed" not in available or "confirm" in available:
        return False
    if "choose" in available or "select_cards" in available:
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "GRID":
        return False
    screen_state = game.get("screen_state") or {}
    return bool(screen_state.get("confirm_up") or _grid_selection_complete_for_runner(screen_state))


def _can_rewrite_chest_choose_to_proceed(
    actions: list[dict[str, Any]],
    available: set[str],
    before_state: dict[str, Any] | None,
) -> bool:
    if len(actions) != 1:
        return False
    if str(actions[0].get("action", "")).lower() != "choose":
        return False
    if "proceed" not in available or "choose" in available:
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    return game.get("screen_type") == "CHEST" and game.get("room_phase") == "COMPLETE"


def _grid_selection_complete_for_runner(screen_state: dict[str, Any]) -> bool:
    try:
        needed = int(screen_state.get("num_cards", 0) or 0)
    except (TypeError, ValueError):
        needed = 0
    if needed <= 0:
        return False
    selected = screen_state.get("selected_cards", [])
    return isinstance(selected, list) and len(selected) >= needed


def _available_tool_names(commands: dict[str, Any]) -> set[str]:
    tools = commands.get("available_tools", [])
    names: set[str] = set()
    for tool in tools:
        if isinstance(tool, dict):
            value = tool.get("tool") or tool.get("name") or tool.get("action")
        else:
            value = tool
        if value:
            names.add(str(value).lower())
    return names


def _action_tool_aliases(action: str) -> set[str]:
    aliases = {
        "cancel": {"cancel", "return"},
        "confirm": {"confirm"},
        "choose": {"choose"},
        "end_turn": {"end_turn", "end"},
        "play_card": {"play_card", "play"},
        "proceed": {"proceed"},
        "select_cards": {"select_cards", "select", "discard"},
        "use_potion": {"use_potion", "potion"},
    }
    return aliases.get(action, {action})


def _has_action(actions: list[dict[str, Any]], name: str) -> bool:
    return any(str(action.get("action", "")).lower() == name for action in actions)


def _wait_for_end_turn_transition(client: MCPClient, before_state: dict[str, Any], interval: float) -> float:
    before_game = before_state.get("game_state", {})
    before_combat = before_game.get("combat_state", {})
    if before_game.get("screen_type") != "NONE" or before_game.get("room_phase") != "COMBAT":
        return 0.0
    before_turn = before_combat.get("turn")
    deadline = time.monotonic() + 4.0
    waited = 0.0
    delay = max(interval, 0.15)
    while time.monotonic() < deadline:
        time.sleep(delay)
        waited += delay
        try:
            state = _read_game_state(client, attempts=2, delay=0.1)
        except MCPError:
            continue
        game = state.get("game_state", {})
        if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
            return waited
        combat = game.get("combat_state", {})
        if not combat.get("monsters"):
            return waited
        if combat.get("turn") != before_turn and not _combat_snapshot_is_settling(combat):
            return waited
    return waited


def _wait_for_rest_transition(client: MCPClient, before_state: dict[str, Any], interval: float) -> float:
    before_game = before_state.get("game_state", {})
    if before_game.get("screen_type") != "REST":
        return 0.0
    deadline = time.monotonic() + 3.0
    waited = 0.0
    delay = max(interval, 0.15)
    while time.monotonic() < deadline:
        time.sleep(delay)
        waited += delay
        try:
            state = _read_game_state(client, attempts=2, delay=0.1)
        except MCPError:
            continue
        game = state.get("game_state", {})
        if game.get("screen_type") != "REST" or game.get("room_phase") == "COMPLETE":
            return waited
        options = game.get("screen_state", {}).get("rest_options", [])
        if not options:
            return waited
    return waited


def _wait_for_grid_transition(client: MCPClient, before_state: dict[str, Any], actions: list[dict[str, Any]], interval: float) -> float:
    before_game = before_state.get("game_state", {})
    if before_game.get("screen_type") != "GRID":
        return 0.0
    before_screen = before_game.get("screen_state", {})
    before_selected = len(before_screen.get("selected_cards", []) or [])
    is_upgrade_grid = bool(before_screen.get("for_upgrade"))
    wait_for_confirm = _has_action(actions, "choose")
    wait_for_exit = _has_action(actions, "confirm")
    deadline = time.monotonic() + 3.0
    waited = 0.0
    delay = max(interval, 0.15)
    while time.monotonic() < deadline:
        time.sleep(delay)
        waited += delay
        try:
            state = _read_game_state(client, attempts=2, delay=0.1)
        except MCPError:
            continue
        game = state.get("game_state", {})
        screen = game.get("screen_type")
        if screen == "GRID":
            screen_state = game.get("screen_state", {})
            selected = len(screen_state.get("selected_cards", []) or [])
            if wait_for_confirm and (screen_state.get("confirm_up") or selected > before_selected):
                return waited
            continue
        if wait_for_exit and is_upgrade_grid and _is_stale_rest_after_upgrade(game):
            continue
        return waited
    return waited


def _is_stale_rest_after_upgrade(game: dict[str, Any]) -> bool:
    if game.get("screen_type") != "REST":
        return False
    if game.get("room_phase") == "COMPLETE":
        return False
    options = game.get("screen_state", {}).get("rest_options", [])
    return bool(options)


def _is_recoverable_action_error(exc: MCPError) -> bool:
    text = str(exc)
    return "Invalid command" in text or "Possible commands" in text or "Error at action" in text


def _synthetic_terminal_state_after_read_failure(
    last_state: dict[str, Any] | None,
    last_actions: list[dict[str, Any]],
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _is_likely_death_transition(last_state, last_actions) and not _diagnostics_show_game_over(diagnostics):
        return None
    assert last_state is not None
    return _synthetic_terminal_state_from_previous(last_state, "synthetic_after_mcp_null")


def _synthetic_terminal_state_after_main_menu(last_state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not last_state or not last_state.get("in_game"):
        return None
    return _synthetic_terminal_state_from_previous(last_state, "synthetic_after_main_menu")


def _synthetic_terminal_state_from_previous(last_state: dict[str, Any], source: str) -> dict[str, Any]:
    game = last_state.get("game_state", {})
    return {
        "in_game": True,
        "ready_for_command": False,
        "synthetic": True,
        "game_state": {
            "screen_type": "GAME_OVER",
            "room_phase": "COMPLETE",
            "floor": game.get("floor"),
            "act": game.get("act"),
            "class": game.get("class"),
            "ascension_level": game.get("ascension_level"),
            "current_hp": min(0, int(game.get("current_hp", 0) or 0)),
            "max_hp": game.get("max_hp"),
            "gold": game.get("gold"),
            "deck": game.get("deck", []),
            "relics": game.get("relics", []),
            "potions": game.get("potions", []),
            "screen_state": {
                "victory": False,
                "score": None,
                "source": source,
            },
        },
    }


def _diagnostics_show_game_over(diagnostics: dict[str, Any] | None) -> bool:
    if not diagnostics:
        return False
    for probe in diagnostics.get("probes", []):
        if probe.get("name") != "get_available_commands" or not probe.get("ok"):
            continue
        summary = probe.get("summary") or {}
        if summary.get("screen_type") == "GAME_OVER":
            return True
    return False


def _is_likely_death_transition(last_state: dict[str, Any] | None, last_actions: list[dict[str, Any]]) -> bool:
    if not last_state or not last_state.get("in_game"):
        return False
    game = last_state.get("game_state", {})
    if game.get("screen_type") == "GAME_OVER":
        return True
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return False
    hp = _current_hp(game)
    if hp <= 0:
        return True
    if not any(str(action.get("action", "")).lower().startswith("end") for action in last_actions):
        return False
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    block = int(player.get("block", 0) or 0)
    return _incoming_damage(combat) >= hp + block


def _current_hp(game: dict[str, Any]) -> int:
    combat_player = game.get("combat_state", {}).get("player", {})
    return int(combat_player.get("current_hp", game.get("current_hp", 0)) or 0)


def _incoming_damage(combat: dict[str, Any]) -> int:
    return incoming_damage(combat)


def _settle_delay_for_actions(actions: list[dict[str, Any]], interval: float, recoverable: bool = False) -> float:
    delay = max(interval, 0.0)
    for action in actions:
        delay = max(delay, _settle_delay_for_action(action, interval))
    if recoverable:
        delay = max(delay, 0.9)
    return delay


def _settle_delay_for_action(action: dict[str, Any], interval: float) -> float:
    name = str(action.get("action", "")).lower()
    if name == "wait":
        return max(interval, float(action.get("ms", 250) or 250) / 1000.0)
    if name == "choose":
        return 0.65
    if name == "proceed":
        return 0.8
    if name.startswith("play"):
        return 0.35
    if name.startswith("end"):
        return 0.9
    if "potion" in name:
        return 0.5
    if name in {"rest", "sleep", "smith", "recall"}:
        return 0.9
    if name in {"confirm", "select_cards"} or "grid" in name:
        return 0.6
    if name in {"skip", "cancel"}:
        return 0.4
    return max(interval, 0.25)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _summarize_state(state: dict[str, Any]) -> str:
    if not state.get("in_game"):
        return "MAIN_MENU"
    game = state.get("game_state", {})
    screen = game.get("screen_type")
    floor = game.get("floor", "?")
    hp = f"{game.get('current_hp', '?')}/{game.get('max_hp', '?')}"
    if screen == "NONE" and game.get("room_phase") == "COMBAT":
        combat = game.get("combat_state", {})
        monsters = ", ".join(m.get("name", "?") for m in combat.get("monsters", []))
        turn = combat.get("turn", "?")
        return f"F{floor} combat T{turn} HP {hp} vs {monsters}"
    return f"F{floor} {screen} HP {hp}"


def _snapshot_state(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("in_game"):
        return {"in_game": False}
    game = state.get("game_state", {})
    snapshot: dict[str, Any] = {
        "in_game": True,
        "screen_type": game.get("screen_type"),
        "room_phase": game.get("room_phase"),
        "floor": game.get("floor"),
        "act": game.get("act"),
        "class": game.get("class"),
        "ascension_level": game.get("ascension_level"),
        "current_hp": game.get("current_hp"),
        "max_hp": game.get("max_hp"),
        "gold": game.get("gold"),
        "deck": [card.get("name") for card in game.get("deck", [])],
        "relics": [relic.get("name", relic.get("id")) for relic in game.get("relics", [])],
        "potions": [
            {
                "name": potion.get("name"),
                "id": potion.get("id"),
                "can_use": potion.get("can_use"),
                "requires_target": potion.get("requires_target"),
            }
            for potion in game.get("potions", [])
            if not potion.get("is_empty")
        ],
    }
    combat = game.get("combat_state", {})
    if combat:
        player = combat.get("player", {})
        snapshot["combat"] = {
            "turn": combat.get("turn"),
            "incoming_damage": _incoming_damage(combat),
            "player": {
                "current_hp": player.get("current_hp"),
                "max_hp": player.get("max_hp"),
                "block": player.get("block"),
                "current_energy": player.get("current_energy"),
                "powers": player.get("powers"),
            },
            "hand": [card.get("name") for card in combat.get("hand", [])],
            "hand_cards": [
                {
                    "name": card.get("name"),
                    "id": card.get("id"),
                    "type": card.get("type"),
                    "cost": card.get("cost"),
                    "damage": card.get("damage"),
                    "block": card.get("block"),
                    "is_playable": card.get("is_playable"),
                    "has_target": card.get("has_target"),
                }
                for card in combat.get("hand", [])
            ],
            "monsters": [
                {
                    "name": monster.get("name"),
                    "id": monster.get("id"),
                    "hp": monster.get("current_hp"),
                    "max_hp": monster.get("max_hp"),
                    "block": monster.get("block"),
                    "intent": monster.get("intent"),
                    "move": monster.get("move"),
                    "powers": monster.get("powers"),
                    "is_dead": monster.get("is_dead"),
                    "is_gone": monster.get("is_gone"),
                }
                for monster in combat.get("monsters", [])
            ],
        }
    screen_state = game.get("screen_state", {})
    if game.get("screen_type") == "MAP":
        snapshot["map_options"] = [
            {
                "symbol": node.get("symbol"),
                "x": node.get("x"),
                "y": node.get("y"),
            }
            for node in screen_state.get("next_nodes", [])
        ]
        snapshot["boss_available"] = screen_state.get("boss_available")
        if game.get("map_observation"):
            snapshot["map_observation"] = _snapshot_map_observation(game.get("map_observation"))
        if game.get("route_evaluation"):
            snapshot["route_evaluation"] = game.get("route_evaluation")
    if game.get("screen_type") == "REST":
        snapshot["rest_options"] = screen_state.get("rest_options", [])
    if game.get("screen_type") == "GRID":
        snapshot["grid"] = {
            "num_cards": screen_state.get("num_cards"),
            "selected_cards": [
                {"name": card.get("name"), "id": card.get("id"), "uuid": card.get("uuid")}
                for card in screen_state.get("selected_cards", [])
            ],
            "for_transform": screen_state.get("for_transform"),
            "confirm_up": screen_state.get("confirm_up"),
            "any_number": screen_state.get("any_number"),
            "for_upgrade": screen_state.get("for_upgrade"),
            "for_purge": screen_state.get("for_purge"),
        }
    if game.get("screen_type") == "CARD_REWARD":
        snapshot["card_reward_options"] = [
            {
                "name": card.get("name"),
                "id": card.get("id"),
                "type": card.get("type"),
                "cost": card.get("cost"),
                "damage": card.get("damage"),
                "block": card.get("block"),
                "upgrades": card.get("upgrades", 0),
            }
            for card in screen_state.get("cards", [])
        ]
    if game.get("screen_type") == "BOSS_REWARD":
        snapshot["boss_relic_options"] = [
            {"name": relic.get("name"), "id": relic.get("id")}
            for relic in screen_state.get("relics", [])
        ]
    if game.get("screen_type") == "CHEST":
        snapshot["chest"] = {
            "chest_open": screen_state.get("chest_open"),
            "rewards": [
                {
                    "reward_type": reward.get("reward_type"),
                    "name": reward.get("name"),
                    "id": reward.get("id"),
                }
                for reward in screen_state.get("rewards", [])
            ],
        }
    if game.get("screen_type") == "SHOP_SCREEN":
        snapshot["shop"] = {
            "cards": [
                {
                    "name": card.get("name"),
                    "id": card.get("id"),
                    "type": card.get("type"),
                    "cost": card.get("cost"),
                    "damage": card.get("damage"),
                    "block": card.get("block"),
                    "price": card.get("price"),
                }
                for card in screen_state.get("cards", [])
            ],
            "relics": [
                {"name": relic.get("name"), "id": relic.get("id"), "price": relic.get("price")}
                for relic in screen_state.get("relics", [])
            ],
            "potions": [
                {"name": potion.get("name"), "id": potion.get("id"), "price": potion.get("price")}
                for potion in screen_state.get("potions", [])
            ],
            "purge_available": screen_state.get("purge_available"),
            "purge_cost": screen_state.get("purge_cost"),
        }
    if game.get("screen_type") == "EVENT":
        snapshot["event_options"] = [
            {
                "label": option.get("label"),
                "text": option.get("text"),
                "disabled": option.get("disabled"),
                "choice_index": option.get("choice_index"),
            }
            for option in screen_state.get("options", [])
        ]
    if game.get("screen_type") == "GAME_OVER":
        victory = screen_state.get("victory")
        if victory is None:
            victory = False
        snapshot["outcome"] = {
            "victory": victory,
            "score": screen_state.get("score"),
            "source": screen_state.get("source"),
        }
    return snapshot


def _snapshot_map_observation(observation: Any) -> dict[str, Any]:
    if not isinstance(observation, dict):
        return {"status": "invalid", "type": type(observation).__name__}
    snapshot: dict[str, Any] = {
        "status": observation.get("status"),
        "source": observation.get("source"),
        "include": observation.get("include"),
        "latency_ms": observation.get("latency_ms"),
        "node_count": observation.get("node_count"),
    }
    if observation.get("error") is not None:
        snapshot["error"] = observation.get("error")
    if observation.get("failures") is not None:
        snapshot["failures"] = observation.get("failures")
    if observation.get("reason") is not None:
        snapshot["reason"] = observation.get("reason")
    if observation.get("screen_type") is not None:
        snapshot["screen_type"] = observation.get("screen_type")
    if observation.get("keys") is not None:
        snapshot["keys"] = observation.get("keys")
    return snapshot


def _outcome_from_state(state: dict[str, Any]) -> dict[str, Any]:
    game = state.get("game_state", {})
    screen_state = game.get("screen_state", {})
    return {
        "victory": bool(screen_state.get("victory")),
        "score": screen_state.get("score"),
        "floor": game.get("floor"),
    }


def _write_state_record(
    log_path: Path,
    step: int,
    state: dict[str, Any],
    actions: list[dict[str, Any]],
    reason: str,
    *,
    should_stop: bool = False,
    learn_card_pick: str | None = None,
) -> dict[str, Any]:
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "step": step,
        "summary": _summarize_state(state),
        "state": _snapshot_state(state),
        "decision": {
            "actions": actions,
            "reason": reason,
            "should_stop": should_stop,
            "learn_card_pick": learn_card_pick,
        },
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _write_action_result(log_path: Path, step: int, actions: list[dict[str, Any]], result: ActionExecution) -> None:
    payload: dict[str, Any] = {
        "actions": actions,
        "action_status": result.status,
        "action_latency_ms": result.latency_ms,
        "settle_ms": result.settle_ms,
    }
    if result.last_error is not None:
        payload["last_error"] = result.last_error
    if result.recovered is not None:
        payload["recovered"] = result.recovered
    if result.recovery_error is not None:
        payload["recovery_error"] = result.recovery_error
    if result.executed_actions is not None:
        payload["executed_actions"] = result.executed_actions
    if result.rewrite_reason is not None:
        payload["rewrite_reason"] = result.rewrite_reason
    if result.available_commands is not None:
        payload["available_commands"] = result.available_commands
    _write_event(log_path, step, "action_result", **payload)


def _write_error(
    log_path: Path,
    step: int,
    error: str,
    actions: list[dict[str, Any]],
    last_error: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "error": error,
        "actions": actions,
    }
    if last_error is not None:
        payload["last_error"] = last_error
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    _write_event(log_path, step, "error", **payload)


def _write_event(log_path: Path, step: int, event: str, **payload: Any) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "step": step,
                    "event": event,
                    **payload,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
