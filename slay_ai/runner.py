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
from .model import COMBAT_SEARCH_MODEL_PATH, DECK_QUALITY_MODEL_PATH, POTION_TEMPO_MODEL_PATH, ROUTE_RISK_MODEL_PATH
from .policy import HeuristicPolicy
from .policy_decision import Decision
from .shadow_advice import ShadowModels, score_shadow_examples, write_advice
from .static_knowledge import StaticKnowledge
from .training_manifest import build_manifest, write_shadow_examples


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class EpisodeResult:
    status: str
    log_path: Path
    steps: int
    victory: bool | None = None
    floor: int | None = None
    score: int | None = None
    manifest_path: Path | None = None
    manifest_category: str | None = None
    manifest_reason: str | None = None
    shadow_advice_path: Path | None = None


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
    post_action_settle_reason: str | None = None
    post_action_settle_error: str | None = None


SLOW_ACTION_CONFIRM_THRESHOLD_MS = 2500
SLOW_ACTION_CONFIRM_ACTIONS = {"end_turn", "play_card", "use_potion"}


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
    parser.add_argument("--no-manifest", action="store_true", help="Do not write a per-run training manifest.")
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        help="Directory for per-run manifests. Defaults to <log-dir>/manifests.",
    )
    parser.add_argument(
        "--manifest-knowledge-dir",
        type=Path,
        default=ROOT / "data" / "static_knowledge",
        help="Static knowledge directory used to enrich automatic per-run manifests when present.",
    )
    parser.add_argument(
        "--manifest-shadow-dir",
        type=Path,
        help="Optional base directory for per-run shadow example JSONL files.",
    )
    parser.add_argument(
        "--manifest-advice-dir",
        type=Path,
        help="Optional base directory for per-run shadow advice JSONL files.",
    )
    parser.add_argument("--route-risk-model-path", type=Path, default=ROUTE_RISK_MODEL_PATH)
    parser.add_argument("--potion-tempo-model-path", type=Path, default=POTION_TEMPO_MODEL_PATH)
    parser.add_argument("--deck-quality-model-path", type=Path, default=DECK_QUALITY_MODEL_PATH)
    parser.add_argument("--combat-search-model-path", type=Path, default=COMBAT_SEARCH_MODEL_PATH)
    parser.add_argument(
        "--model-authority",
        choices=["shadow", "assist", "pilot"],
        default="shadow",
        help="How much runtime authority learned signals may take. assist/pilot currently affect card rewards only.",
    )
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
            write_manifest=not args.no_manifest,
            manifest_dir=args.manifest_dir,
            manifest_knowledge_dir=args.manifest_knowledge_dir,
            manifest_shadow_dir=args.manifest_shadow_dir,
            manifest_advice_dir=args.manifest_advice_dir,
            route_risk_model_path=args.route_risk_model_path,
            potion_tempo_model_path=args.potion_tempo_model_path,
            deck_quality_model_path=args.deck_quality_model_path,
            combat_search_model_path=args.combat_search_model_path,
            model_authority=args.model_authority,
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
    write_manifest: bool = True,
    manifest_dir: Path | None = None,
    manifest_knowledge_dir: Path | None = ROOT / "data" / "static_knowledge",
    manifest_shadow_dir: Path | None = None,
    manifest_advice_dir: Path | None = None,
    route_risk_model_path: Path = ROUTE_RISK_MODEL_PATH,
    potion_tempo_model_path: Path = POTION_TEMPO_MODEL_PATH,
    deck_quality_model_path: Path = DECK_QUALITY_MODEL_PATH,
    combat_search_model_path: Path = COMBAT_SEARCH_MODEL_PATH,
    model_authority: str = "shadow",
    echo: bool = False,
    client: MCPClient | None = None,
    memory: StrategyMemory | None = None,
) -> EpisodeResult:
    memory = memory or StrategyMemory.load()
    policy = HeuristicPolicy(
        memory,
        character=character,
        model_authority=model_authority,
        route_risk_model_path=route_risk_model_path,
        potion_tempo_model_path=potion_tempo_model_path,
    )
    client = client or MCPClient(endpoint)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{character.lower()}_a{ascension}.jsonl"

    def finish(result: EpisodeResult) -> EpisodeResult:
        if not write_manifest:
            return result
        return _write_episode_manifest(
            result,
            manifest_dir=manifest_dir,
            knowledge_dir=manifest_knowledge_dir,
            shadow_dir=manifest_shadow_dir,
            advice_dir=manifest_advice_dir,
            route_model_path=route_risk_model_path,
            potion_model_path=potion_tempo_model_path,
            deck_model_path=deck_quality_model_path,
            combat_model_path=combat_search_model_path,
            echo=echo,
        )

    if dry_run and (start or continue_run):
        return finish(EpisodeResult("dry_run", log_path, 0))

    if hasattr(client, "ensure_initialized"):
        client.ensure_initialized()
    else:
        client.initialize()
    launched_or_continued = False
    if start:
        start_args: dict[str, Any] = {"character": character, "ascension": ascension}
        if seed:
            start_args["seed"] = seed
        message, should_start = _prepare_existing_save_for_start(client, existing_save)
        try:
            if should_start:
                message = client.call_tool("start_game", start_args)
        except MCPError as exc:
            if "Possible commands" in str(exc) and _recover_terminal_game_over_before_start(client):
                if _current_run_matches_start(client, character, ascension):
                    message = "Continuing target run after clearing terminal screen"
                else:
                    message = client.call_tool("start_game", start_args)
            elif "Possible commands" not in str(exc):
                raise
            else:
                possible_commands = _possible_command_names_from_error(str(exc))
                message, should_start = _prepare_existing_save_for_start(
                    client,
                    existing_save,
                    available_hint=possible_commands or None,
                )
                if should_start:
                    message = client.call_tool("start_game", start_args)
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
    repeated_shop_purchase_signature: tuple[int, int, str] | None = None
    repeated_shop_purchase_count = 0
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
                        return finish(
                            EpisodeResult(
                                status="game_over",
                                log_path=log_path,
                                steps=step,
                                victory=outcome.get("victory"),
                                floor=outcome.get("floor"),
                                score=outcome.get("score"),
                            )
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
                    return finish(EpisodeResult("read_failed", log_path, step))
                _write_event(log_path, step, "state_read_recovered", last_error=str(exc))
            else:
                _write_error(log_path, step, f"read_state_failed: {exc}", [])
                if echo:
                    print(f"State read failed at step {step}: {exc}", file=sys.stderr)
                return finish(EpisodeResult("read_failed", log_path, step))

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
                return finish(
                    EpisodeResult(
                        status="game_over",
                        log_path=log_path,
                        steps=step,
                        victory=outcome.get("victory"),
                        floor=outcome.get("floor"),
                        score=outcome.get("score"),
                    )
                )

        last_state = state
        game = state.get("game_state", {})
        floor = int(game.get("floor", -1) or -1)
        if game.get("screen_type") == "SHOP_ROOM" and floor in shop_left_floors:
            decision = Decision([{"action": "proceed"}], "Shop already left; proceed.")
        else:
            decision = policy.decide(state)
        shop_purchase_signature = _shop_purchase_signature(game, decision)
        if shop_purchase_signature is None:
            repeated_shop_purchase_signature = None
            repeated_shop_purchase_count = 0
        elif shop_purchase_signature == repeated_shop_purchase_signature:
            repeated_shop_purchase_count += 1
            if repeated_shop_purchase_count >= 3:
                decision = Decision(
                    [{"action": "cancel"}],
                    "Repeated shop purchase guard; leave shop to avoid stale inventory loop.",
                )
                shop_purchase_signature = None
                repeated_shop_purchase_signature = None
                repeated_shop_purchase_count = 0
        else:
            repeated_shop_purchase_signature = shop_purchase_signature
            repeated_shop_purchase_count = 0
        record = _write_state_record(
            log_path,
            step,
            state,
            decision.actions,
            decision.reason,
            should_stop=decision.should_stop,
            learn_card_pick=decision.learn_card_pick,
            metadata=decision.metadata,
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
            return finish(
                EpisodeResult(
                    status="game_over",
                    log_path=log_path,
                    steps=step,
                    victory=outcome.get("victory"),
                    floor=outcome.get("floor"),
                    score=outcome.get("score"),
                )
            )

        if dry_run:
            if echo:
                print(json.dumps(decision.actions, ensure_ascii=False, indent=2))
                print(f"Dry run log: {log_path}")
            return finish(EpisodeResult("dry_run", log_path, step))

        if decision.actions:
            try:
                action_result = _execute_actions_with_settle(client, decision.actions, interval, before_state=state)
            except MCPError as exc:
                _write_error(log_path, step, str(exc), decision.actions)
                if echo:
                    print(f"Action failed at step {step}: {exc}", file=sys.stderr)
                return finish(EpisodeResult("action_failed", log_path, step))
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
    return finish(EpisodeResult("max_steps", log_path, max_steps))


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


def _prepare_existing_save_for_start(
    client: MCPClient,
    existing_save: str,
    available_hint: set[str] | None = None,
) -> tuple[str | None, bool]:
    if available_hint is None:
        try:
            commands = client.call_tool("get_available_commands", {})
        except MCPError:
            return None, True
        available = _available_tool_names(commands)
    else:
        available = {str(command).lower() for command in available_hint}
    if available & {"start", "start_game"}:
        return None, True
    has_continue = bool(available & {"continue", "continue_game"})
    has_abandon = bool(available & {"abandon", "abandon_run"})
    if not has_continue and not has_abandon:
        if existing_save == "continue" and _is_in_game(client):
            return "Continuing current in-dungeon run", False
        if existing_save == "abandon" and _is_in_game(client):
            _return_to_menu_for_abandon(client)
            try:
                refreshed = client.call_tool("get_available_commands", {})
            except MCPError:
                return None, True
            refreshed_available = _available_tool_names(refreshed)
            if refreshed_available & {"abandon", "abandon_run"}:
                client.call_tool("abandon_run", {})
                return None, True
            if refreshed_available & {"start", "start_game"}:
                return None, True
        return None, True
    if existing_save == "fail":
        raise MCPError("Existing save blocks new run; use --existing-save continue or --existing-save abandon.")
    if existing_save == "continue":
        if _is_in_game(client):
            return "Continuing current in-dungeon run", False
        return str(client.call_tool("continue_game", {})), False
    if existing_save == "abandon":
        _return_to_menu_for_abandon(client)
        client.call_tool("abandon_run", {})
        return None, True
    return None, True


def _possible_command_names_from_error(text: str) -> set[str]:
    marker = "Possible commands:"
    if marker not in text:
        return set()
    tail = text.split(marker, 1)[1].strip()
    if tail.startswith("[") and "]" in tail:
        tail = tail[1 : tail.index("]")]
    names = set()
    for raw in tail.replace(";", ",").split(","):
        name = raw.strip().strip("'\"").lower()
        if name:
            names.add(name)
    return names


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
        stale_combat_result = _settle_stale_combat_command_surface(
            client,
            actions_to_execute,
            before_state,
            available_commands,
            interval,
            preflight_started,
        )
        if stale_combat_result is not None:
            return stale_combat_result
        stale_reward_result = _settle_stale_reward_proceed(
            client,
            actions_to_execute,
            before_state,
            available_commands,
            interval,
            preflight_started,
        )
        if stale_reward_result is not None:
            return stale_reward_result
        stale_grid_result = _settle_stale_grid_choose_command_surface(
            client,
            actions_to_execute,
            before_state,
            available_commands,
            interval,
            preflight_started,
        )
        if stale_grid_result is not None:
            return stale_grid_result
        stale_map_result = _settle_stale_map_choose_proceed(
            client,
            actions,
            actions_to_execute,
            before_state,
            available_commands,
            interval,
            preflight_started,
            rewrite_reason,
        )
        if stale_map_result is not None:
            return stale_map_result
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

    stale_map_result = _settle_stale_map_choose_proceed(
        client,
        actions,
        actions_to_execute,
        before_state,
        available_commands,
        interval,
        preflight_started,
        rewrite_reason,
    )
    if stale_map_result is not None:
        return stale_map_result

    started = time.monotonic()
    try:
        client.execute_actions(actions_to_execute)
    except MCPError as exc:
        latency_ms = _elapsed_ms(started)
        if not _is_recoverable_action_error(exc):
            raise
        stale_transition_result = _settle_stale_combat_transition_after_action_error(
            client,
            actions_to_execute,
            before_state,
            exc,
            interval,
            started,
        )
        if stale_transition_result is not None:
            return stale_transition_result
        stale_chest_result = _settle_stale_chest_proceed_after_action_error(
            client,
            actions_to_execute,
            before_state,
            exc,
            interval,
            started,
        )
        if stale_chest_result is not None:
            return stale_chest_result
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
    if before_state is not None and _has_action(actions_to_execute, "use_potion"):
        settle += _wait_for_potion_resolution(client, before_state, actions_to_execute, interval)
    if before_state is not None and _has_action(actions_to_execute, "play_card"):
        settle += _wait_for_targeted_attack_resolution(client, before_state, actions_to_execute, interval)
    if before_state is not None and _has_action(actions_to_execute, "choose"):
        settle += _wait_for_rest_transition(client, before_state, interval)
    if before_state is not None and (
        _has_action(actions_to_execute, "choose")
        or _has_action(actions_to_execute, "confirm")
        or _has_action(actions, "confirm")
    ):
        settle += _wait_for_grid_transition(client, before_state, actions, interval)
    post_action_settle_reason = None
    post_action_settle_error = None
    if before_state is not None:
        extra_settle, post_action_settle_reason, post_action_settle_error = _wait_for_slow_action_confirmation(
            client,
            before_state,
            actions_to_execute,
            interval,
            latency_ms,
        )
        settle += extra_settle
    return ActionExecution(
        status="ok",
        latency_ms=latency_ms,
        settle_ms=int(settle * 1000),
        executed_actions=actions_to_execute if rewrite_reason else None,
        rewrite_reason=rewrite_reason,
        available_commands=available_commands if rewrite_reason else None,
        post_action_settle_reason=post_action_settle_reason,
        post_action_settle_error=post_action_settle_error,
    )


def _settle_stale_combat_command_surface(
    client: MCPClient,
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
    interval: float,
    preflight_started: float,
) -> ActionExecution | None:
    if not _can_wait_for_stale_combat_command_surface(actions, before_state, available_commands):
        return None
    settle = _settle_delay_for_actions([{"action": "wait"}], interval, recoverable=True)
    time.sleep(settle)
    try:
        refreshed = _read_stable_game_state(client, attempts=12, delay=0.2)
    except MCPError:
        return None
    available = set(available_commands or [])
    if _same_combat_frame(before_state, refreshed) and not _is_reward_transition_command_surface(available):
        return None
    return ActionExecution(
        status="ok",
        latency_ms=_elapsed_ms(preflight_started),
        settle_ms=int(settle * 1000),
        executed_actions=[{"action": "wait"}],
        rewrite_reason="Preflight wait: stale combat command surface settled.",
        available_commands=available_commands,
    )


def _can_wait_for_stale_combat_command_surface(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
) -> bool:
    actionable = [str(action.get("action", "")).lower() for action in actions if str(action.get("action", "")).lower() != "wait"]
    if not actionable or not any(action in {"play_card", "end_turn"} for action in actionable):
        return False
    available = set(available_commands or [])
    if available & {"play_card", "play", "end_turn", "end"}:
        return False
    if not (available & {"choose", "confirm", "select_cards"} or _is_reward_transition_command_surface(available)):
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    return game.get("screen_type") == "NONE" and game.get("room_phase") == "COMBAT"


def _same_combat_frame(before_state: dict[str, Any] | None, refreshed_state: dict[str, Any]) -> bool:
    if before_state is None:
        return False
    before_game = before_state.get("game_state", {})
    refreshed_game = refreshed_state.get("game_state", {})
    before_combat = before_game.get("combat_state") or {}
    refreshed_combat = refreshed_game.get("combat_state") or {}
    return (
        bool(refreshed_state.get("in_game"))
        and refreshed_game.get("screen_type") == "NONE"
        and refreshed_game.get("room_phase") == "COMBAT"
        and refreshed_game.get("floor") == before_game.get("floor")
        and refreshed_combat.get("turn") == before_combat.get("turn")
    )


def _settle_stale_combat_transition_after_action_error(
    client: MCPClient,
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    exc: MCPError,
    interval: float,
    started: float,
) -> ActionExecution | None:
    if not _can_wait_for_stale_combat_transition_after_action_error(actions, before_state, exc):
        return None
    settle = _settle_delay_for_actions([{"action": "wait"}], interval, recoverable=True)
    time.sleep(settle)
    try:
        refreshed = _read_stable_game_state(client, attempts=12, delay=0.2)
    except MCPError:
        return None
    possible = set(_possible_commands_from_error(exc))
    if _same_combat_frame(before_state, refreshed) and not _is_reward_transition_command_surface(possible):
        return None
    return ActionExecution(
        status="ok",
        latency_ms=_elapsed_ms(started),
        settle_ms=int(settle * 1000),
        executed_actions=[{"action": "wait"}],
        rewrite_reason="Action wait: stale combat transition settled.",
        available_commands=_possible_commands_from_error(exc),
    )


def _can_wait_for_stale_combat_transition_after_action_error(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    exc: MCPError,
) -> bool:
    actionable = [str(action.get("action", "")).lower() for action in actions if str(action.get("action", "")).lower() != "wait"]
    if not actionable or not any(action in {"play_card", "end_turn"} for action in actionable):
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return False
    possible = set(_possible_commands_from_error(exc))
    if possible & {"play_card", "play", "end_turn", "end"}:
        return False
    return bool(possible & {"choose", "proceed", "confirm", "select_cards"})


def _settle_stale_chest_proceed_after_action_error(
    client: MCPClient,
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    exc: MCPError,
    interval: float,
    started: float,
) -> ActionExecution | None:
    if not _can_rewrite_chest_proceed_to_choose(actions, set(_possible_commands_from_error(exc)), before_state):
        return None
    rewritten = [{"action": "choose", "choice_index": 1}]
    try:
        client.execute_actions(rewritten)
    except MCPError:
        return None
    settle = _settle_delay_for_actions(rewritten, interval)
    time.sleep(settle)
    return ActionExecution(
        status="ok",
        latency_ms=_elapsed_ms(started),
        settle_ms=int(settle * 1000),
        executed_actions=rewritten,
        rewrite_reason="Action rewrite: chest proceed->choose after stale proceed failed.",
        available_commands=_possible_commands_from_error(exc),
    )


def _is_reward_transition_command_surface(commands: set[str]) -> bool:
    if commands & {"play_card", "play", "end_turn", "end"}:
        return False
    return "proceed" in commands


def _possible_commands_from_error(exc: MCPError) -> list[str]:
    text = str(exc)
    marker = "Possible commands:"
    if marker not in text:
        return []
    tail = text.split(marker, 1)[1].strip()
    if tail.startswith("[") and "]" in tail:
        tail = tail[1 : tail.index("]")]
    commands = []
    for item in tail.split(","):
        command = item.strip().strip("'\"").lower()
        if command:
            commands.append(command)
    return commands


def _settle_stale_reward_proceed(
    client: MCPClient,
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
    interval: float,
    preflight_started: float,
) -> ActionExecution | None:
    if not _can_wait_for_stale_reward_proceed(actions, before_state, available_commands):
        return None
    settle = _settle_delay_for_actions(actions, interval, recoverable=True)
    time.sleep(settle)
    try:
        refreshed = _read_stable_game_state(client, attempts=12, delay=0.2)
    except MCPError:
        return None
    if _same_reward_frame(before_state, refreshed):
        return None
    return ActionExecution(
        status="ok",
        latency_ms=_elapsed_ms(preflight_started),
        settle_ms=int(settle * 1000),
        executed_actions=[{"action": "wait"}],
        rewrite_reason="Preflight wait: stale reward proceed settled.",
        available_commands=available_commands,
    )


def _settle_stale_grid_choose_command_surface(
    client: MCPClient,
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
    interval: float,
    preflight_started: float,
) -> ActionExecution | None:
    if not _can_wait_for_stale_grid_choose_command_surface(actions, before_state, available_commands):
        return None
    settle = _settle_delay_for_actions([{"action": "wait"}], interval, recoverable=True)
    time.sleep(settle)
    try:
        refreshed = _read_stable_game_state(client, attempts=12, delay=0.2)
    except MCPError:
        return None
    refreshed_available: set[str] = set()
    try:
        refreshed_commands = client.call_tool("get_available_commands", {})
        refreshed_available = _available_tool_names(refreshed_commands)
    except MCPError:
        pass
    if not _same_grid_frame(before_state, refreshed) or "choose" in refreshed_available:
        return ActionExecution(
            status="ok",
            latency_ms=_elapsed_ms(preflight_started),
            settle_ms=int(settle * 1000),
            executed_actions=[{"action": "wait"}],
            rewrite_reason="Preflight wait: stale grid choose command surface settled.",
            available_commands=available_commands,
        )
    return None


def _settle_stale_map_choose_proceed(
    client: MCPClient,
    original_actions: list[dict[str, Any]],
    actions_to_execute: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
    interval: float,
    preflight_started: float,
    rewrite_reason: str | None,
) -> ActionExecution | None:
    if not _can_wait_for_stale_map_choose_proceed(
        original_actions,
        actions_to_execute,
        before_state,
        available_commands,
        rewrite_reason,
    ):
        return None
    settle = _settle_delay_for_actions([{"action": "wait"}], interval, recoverable=True)
    time.sleep(settle)
    try:
        refreshed = _read_stable_game_state(client, attempts=12, delay=0.2)
    except MCPError:
        return None
    return ActionExecution(
        status="ok",
        latency_ms=_elapsed_ms(preflight_started),
        settle_ms=int(settle * 1000),
        executed_actions=[{"action": "wait"}],
        rewrite_reason="Preflight wait: stale map choose/proceed settled.",
        available_commands=available_commands,
    )


def _can_wait_for_stale_map_choose_proceed(
    original_actions: list[dict[str, Any]],
    actions_to_execute: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
    rewrite_reason: str | None,
) -> bool:
    if len(original_actions) != 1 or len(actions_to_execute) != 1:
        return False
    if str(original_actions[0].get("action", "")).lower() != "choose":
        return False
    execute_action = str(actions_to_execute[0].get("action", "")).lower()
    if execute_action == "proceed" and rewrite_reason != "Preflight action rewrite: map choose->proceed.":
        return False
    if execute_action not in {"choose", "proceed"}:
        return False
    available = set(available_commands or [])
    if "choose" in available or "proceed" not in available:
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    return game.get("screen_type") == "MAP"


def _can_wait_for_stale_grid_choose_command_surface(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
) -> bool:
    actionable = [action for action in actions if str(action.get("action", "")).lower() != "wait"]
    if len(actionable) != 1:
        return False
    if str(actionable[0].get("action", "")).lower() != "choose":
        return False
    available = set(available_commands or [])
    if "choose" in available or "proceed" not in available:
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    return game.get("screen_type") == "GRID"


def _same_grid_frame(before_state: dict[str, Any] | None, refreshed_state: dict[str, Any]) -> bool:
    if before_state is None:
        return False
    before_game = before_state.get("game_state", {})
    refreshed_game = refreshed_state.get("game_state", {})
    return (
        bool(refreshed_state.get("in_game"))
        and refreshed_game.get("screen_type") == "GRID"
        and refreshed_game.get("floor") == before_game.get("floor")
    )


def _can_wait_for_stale_reward_proceed(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
    available_commands: list[str] | None,
) -> bool:
    actionable = [action for action in actions if str(action.get("action", "")).lower() != "wait"]
    if len(actionable) != 1:
        return False
    if str(actionable[0].get("action", "")).lower() != "proceed":
        return False
    available = set(available_commands or [])
    if "proceed" in available or not (available & {"choose", "cancel"}):
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    return game.get("screen_type") == "COMBAT_REWARD" and game.get("room_phase") == "COMPLETE"


def _same_reward_frame(before_state: dict[str, Any] | None, refreshed_state: dict[str, Any]) -> bool:
    if before_state is None:
        return False
    before_game = before_state.get("game_state", {})
    refreshed_game = refreshed_state.get("game_state", {})
    return (
        bool(refreshed_state.get("in_game"))
        and refreshed_game.get("screen_type") == "COMBAT_REWARD"
        and refreshed_game.get("floor") == before_game.get("floor")
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
        stale_action_error = _targeted_action_preflight_error(actions_to_execute, before_state)
        if stale_action_error:
            return actions_to_execute, rewrite_reason, stale_action_error, available_commands
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
    if _can_rewrite_chest_proceed_to_choose(actions, available, before_state):
        return [{"action": "choose", "choice_index": 1}], "Preflight action rewrite: chest proceed->choose."
    if _can_rewrite_chest_choose_to_proceed(actions, available, before_state):
        return [{"action": "proceed"}], "Preflight action rewrite: chest choose->proceed."
    if _can_rewrite_map_choose_to_proceed(actions, available, before_state):
        return [{"action": "proceed"}], "Preflight action rewrite: map choose->proceed."
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


def _can_rewrite_chest_proceed_to_choose(
    actions: list[dict[str, Any]],
    available: set[str],
    before_state: dict[str, Any] | None,
) -> bool:
    if len(actions) != 1:
        return False
    if str(actions[0].get("action", "")).lower() != "proceed":
        return False
    if "choose" not in available or "proceed" in available:
        return False
    if before_state is None:
        return False
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "CHEST" or game.get("room_phase") != "COMPLETE":
        return False
    screen_state = game.get("screen_state") or {}
    return screen_state.get("chest_open") is not True


def _can_rewrite_map_choose_to_proceed(
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
    if game.get("screen_type") != "MAP":
        return False
    screen_state = game.get("screen_state") or {}
    return bool(game.get("map_options") or screen_state.get("next_nodes"))


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


def _targeted_play_card_preflight_error(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
) -> str | None:
    if before_state is None:
        return None
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return None
    combat = game.get("combat_state", {})
    hand = combat.get("hand") or []
    monsters = combat.get("monsters") or []
    for action in actions:
        if str(action.get("action", "")).lower() != "play_card" or action.get("target_index") is None:
            continue
        try:
            card_index = int(action.get("card_index", 0))
            target_index = int(action.get("target_index", 0))
        except (TypeError, ValueError):
            return (
                "Preflight stale targeted play_card: non-numeric "
                f"card_index={action.get('card_index')!r}, target_index={action.get('target_index')!r}."
            )
        if card_index <= 0 or card_index > len(hand):
            return (
                "Preflight stale targeted play_card: "
                f"card_index={card_index} outside current hand size {len(hand)}."
            )
        if target_index <= 0 or target_index > len(monsters):
            return (
                "Preflight stale target_index: "
                f"target_index={target_index} outside current monster count {len(monsters)}."
            )
        monster = monsters[target_index - 1]
        if monster.get("is_dead") or monster.get("is_gone") or _monster_current_hp(monster) <= 0:
            return (
                "Preflight stale target_index: "
                f"target_index={target_index} points to defeated monster {_monster_signature(monster)}."
            )
    return None


def _targeted_action_preflight_error(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
) -> str | None:
    play_card_error = _targeted_play_card_preflight_error(actions, before_state)
    if play_card_error:
        return play_card_error
    return _use_potion_preflight_error(actions, before_state)


def _use_potion_preflight_error(
    actions: list[dict[str, Any]],
    before_state: dict[str, Any] | None,
) -> str | None:
    if before_state is None:
        return None
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return None
    combat = game.get("combat_state", {})
    monsters = combat.get("monsters") or []
    potions = game.get("potions") or []
    for action in actions:
        if str(action.get("action", "")).lower() != "use_potion":
            continue
        try:
            potion_slot = int(action.get("potion_slot", action.get("slot", 0)))
        except (TypeError, ValueError):
            return f"Preflight stale use_potion: non-numeric potion_slot={action.get('potion_slot', action.get('slot'))!r}."
        if potion_slot <= 0 or potion_slot > len(potions):
            return (
                "Preflight stale use_potion: "
                f"potion_slot={potion_slot} outside current potion count {len(potions)}."
            )
        potion = potions[potion_slot - 1]
        if isinstance(potion, dict) and (potion.get("is_empty") or potion.get("can_use") is False):
            return (
                "Preflight stale use_potion: "
                f"potion_slot={potion_slot} points to unavailable potion {_potion_signature(potion)}."
            )
        if action.get("target_index") is None:
            continue
        try:
            target_index = int(action.get("target_index", 0))
        except (TypeError, ValueError):
            return (
                "Preflight stale targeted use_potion: non-numeric "
                f"potion_slot={potion_slot}, target_index={action.get('target_index')!r}."
            )
        if target_index <= 0 or target_index > len(monsters):
            return (
                "Preflight stale target_index: "
                f"target_index={target_index} outside current monster count {len(monsters)} for use_potion."
            )
        monster = monsters[target_index - 1]
        if monster.get("is_dead") or monster.get("is_gone") or _monster_current_hp(monster) <= 0:
            return (
                "Preflight stale target_index: "
                f"target_index={target_index} points to defeated monster {_monster_signature(monster)} for use_potion."
            )
    return None


def _potion_signature(potion: dict[str, Any]) -> tuple[Any, Any]:
    return (potion.get("id"), potion.get("name"))


def _has_action(actions: list[dict[str, Any]], name: str) -> bool:
    return any(str(action.get("action", "")).lower() == name for action in actions)


def _wait_for_slow_action_confirmation(
    client: MCPClient,
    before_state: dict[str, Any],
    actions: list[dict[str, Any]],
    interval: float,
    latency_ms: int,
) -> tuple[float, str | None, str | None]:
    if latency_ms < SLOW_ACTION_CONFIRM_THRESHOLD_MS:
        return 0.0, None, None
    if not any(str(action.get("action", "")).lower() in SLOW_ACTION_CONFIRM_ACTIONS for action in actions):
        return 0.0, None, None
    before_signature = _post_action_state_signature(before_state)
    if before_signature is None:
        return 0.0, None, None

    deadline = time.monotonic() + 2.0
    waited = 0.0
    delay = max(interval, 0.15)
    last_error = None
    while time.monotonic() < deadline:
        time.sleep(delay)
        waited += delay
        try:
            state = _read_game_state(client, attempts=2, delay=0.1)
        except MCPError as exc:
            last_error = str(exc)
            if _is_transient_state_error(exc):
                continue
            return waited, "slow_action_read_failed", last_error
        if _post_action_state_signature(state) != before_signature:
            return waited, "slow_action_state_changed", None
    return waited, "slow_action_confirmation_timeout", last_error


def _post_action_state_signature(state: dict[str, Any]) -> tuple[Any, ...] | None:
    if not state.get("in_game"):
        return ("not_in_game",)
    game = state.get("game_state", {})
    screen = game.get("screen_type")
    phase = game.get("room_phase")
    if screen != "NONE" or phase != "COMBAT":
        return (
            "screen",
            screen,
            phase,
            game.get("floor"),
            game.get("current_hp"),
            game.get("gold"),
        )
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    return (
        "combat",
        game.get("floor"),
        combat.get("turn"),
        player.get("current_hp", game.get("current_hp")),
        player.get("block"),
        player.get("current_energy"),
        tuple(_combat_card_signature(card) for card in combat.get("hand") or []),
        tuple(_combat_monster_signature(monster) for monster in combat.get("monsters") or []),
        tuple(_combat_potion_signature(potion) for potion in game.get("potions") or [] if not potion.get("is_empty")),
    )


def _shop_purchase_signature(game: dict[str, Any], decision: Decision) -> tuple[int, int, str] | None:
    if game.get("screen_type") != "SHOP_SCREEN":
        return None
    if not decision.actions or len(decision.actions) != 1:
        return None
    action = decision.actions[0]
    if str(action.get("action", "")).lower() != "choose":
        return None
    reason = str(decision.reason or "")
    if not reason.startswith("Shop buy "):
        return None
    try:
        choice_index = int(action.get("choice_index"))
    except (TypeError, ValueError):
        return None
    floor = int(game.get("floor", -1) or -1)
    return (floor, choice_index, reason)


def _wait_for_targeted_attack_resolution(
    client: MCPClient,
    before_state: dict[str, Any],
    actions: list[dict[str, Any]],
    interval: float,
) -> float:
    expected = _targeted_attack_expectation(before_state, actions)
    if expected is None:
        return 0.0
    deadline = time.monotonic() + 2.0
    waited = 0.0
    delay = max(interval, 0.15)
    while time.monotonic() < deadline:
        time.sleep(delay)
        waited += delay
        try:
            state = _read_game_state(client, attempts=2, delay=0.1)
        except MCPError:
            continue
        if _targeted_attack_has_resolved(state, expected):
            return waited
    return waited


def _targeted_attack_expectation(
    before_state: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    action = next((item for item in actions if str(item.get("action", "")).lower() == "play_card"), None)
    if action is None or action.get("target_index") is None:
        return None
    game = before_state.get("game_state", {})
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return None
    combat = game.get("combat_state", {})
    hand = combat.get("hand") or []
    monsters = combat.get("monsters") or []
    try:
        card_index = int(action.get("card_index", 0))
        target_index = int(action.get("target_index", 0))
    except (TypeError, ValueError):
        return None
    if card_index <= 0 or target_index <= 0 or card_index > len(hand) or target_index > len(monsters):
        return None
    card = hand[card_index - 1]
    monster = monsters[target_index - 1]
    damage = _as_int(card.get("damage"))
    if damage <= 0:
        return None
    hp = _monster_current_hp(monster)
    block = _as_int(monster.get("block"))
    hp_damage = max(0, damage - block)
    if hp_damage <= 0:
        return None
    if damage < hp + block and not _attack_crosses_split_threshold(monster, hp_damage):
        return None
    return {
        "floor": game.get("floor"),
        "turn": combat.get("turn"),
        "monster_count": len(monsters),
        "target_index": target_index,
        "target_hp": hp,
        "target_signature": _monster_signature(monster),
    }


def _targeted_attack_has_resolved(state: dict[str, Any], expected: dict[str, Any]) -> bool:
    if not state.get("in_game"):
        return True
    game = state.get("game_state", {})
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return True
    combat = game.get("combat_state", {})
    if combat.get("turn") != expected.get("turn"):
        return True
    monsters = combat.get("monsters") or []
    if len(monsters) != expected.get("monster_count"):
        return True
    target_index = int(expected.get("target_index", 0) or 0)
    if target_index <= 0 or target_index > len(monsters):
        return True
    monster = monsters[target_index - 1]
    if _monster_signature(monster) != expected.get("target_signature"):
        return True
    if monster.get("is_dead") or monster.get("is_gone"):
        return True
    return _monster_current_hp(monster) < int(expected.get("target_hp", 0) or 0)


def _wait_for_potion_resolution(
    client: MCPClient,
    before_state: dict[str, Any],
    actions: list[dict[str, Any]],
    interval: float,
) -> float:
    expected = _potion_resolution_expectation(before_state, actions)
    if expected is None:
        return 0.0
    deadline = time.monotonic() + 2.0
    waited = 0.0
    delay = max(interval, 0.15)
    last_signature: tuple[Any, ...] | None = None
    stable_reads = 0
    while time.monotonic() < deadline:
        time.sleep(delay)
        waited += delay
        try:
            state = _read_game_state(client, attempts=2, delay=0.1)
        except MCPError:
            continue
        signature = _potion_resolution_signature(state)
        if signature is None:
            return waited
        if signature == expected.get("signature"):
            continue
        if signature == last_signature:
            stable_reads += 1
        else:
            last_signature = signature
            stable_reads = 1
        game = state.get("game_state", {})
        combat = game.get("combat_state", {})
        if stable_reads >= 2 and not _combat_snapshot_is_settling(combat):
            return waited
    return waited


def _potion_resolution_expectation(
    before_state: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not any(str(action.get("action", "")).lower() == "use_potion" for action in actions):
        return None
    signature = _potion_resolution_signature(before_state)
    if signature is None:
        return None
    return {"signature": signature}


def _potion_resolution_signature(state: dict[str, Any]) -> tuple[Any, ...] | None:
    if not state.get("in_game"):
        return None
    game = state.get("game_state", {})
    if game.get("screen_type") != "NONE" or game.get("room_phase") != "COMBAT":
        return None
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    return (
        game.get("floor"),
        combat.get("turn"),
        player.get("current_energy"),
        player.get("block"),
        tuple(_combat_card_signature(card) for card in combat.get("hand") or []),
        tuple(_combat_monster_signature(monster) for monster in combat.get("monsters") or []),
        tuple(_combat_potion_signature(potion) for potion in game.get("potions") or [] if not potion.get("is_empty")),
    )


def _combat_card_signature(card: dict[str, Any]) -> tuple[Any, ...]:
    return (
        card.get("uuid"),
        card.get("id"),
        card.get("name"),
        card.get("cost"),
        card.get("damage"),
        card.get("block"),
        card.get("is_playable"),
    )


def _combat_monster_signature(monster: dict[str, Any]) -> tuple[Any, ...]:
    return (
        monster.get("id"),
        monster.get("name"),
        monster.get("max_hp"),
        _monster_current_hp(monster),
        monster.get("block"),
        monster.get("intent"),
        monster.get("is_dead"),
        monster.get("is_gone"),
    )


def _combat_potion_signature(potion: dict[str, Any]) -> tuple[Any, ...]:
    return (
        potion.get("id"),
        potion.get("name"),
        potion.get("can_use"),
        potion.get("requires_target"),
    )


def _attack_crosses_split_threshold(monster: dict[str, Any], hp_damage: int) -> bool:
    hp = _monster_current_hp(monster)
    max_hp = _as_int(monster.get("max_hp"))
    if max_hp <= 0 or hp <= max_hp / 2:
        return False
    if hp - hp_damage <= 0 or hp - hp_damage > max_hp / 2:
        return False
    if any(str(power.get("id", "")).lower() == "split" for power in monster.get("powers") or []):
        return True
    label = f"{monster.get('id', '')} {monster.get('name', '')}".lower()
    return "slime" in label


def _monster_signature(monster: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (monster.get("id"), monster.get("name"), monster.get("max_hp"))


def _monster_current_hp(monster: dict[str, Any]) -> int:
    return _as_int(monster.get("current_hp", monster.get("hp")))


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


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


def _snapshot_fields(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: item.get(field) for field in fields}


_CARD_SNAPSHOT_FIELDS = (
    "name",
    "id",
    "card_id",
    "type",
    "cost",
    "damage",
    "block",
    "upgrades",
)
_RELIC_SNAPSHOT_FIELDS = ("name", "id", "relic_id", "choice_index", "price")
_POTION_SNAPSHOT_FIELDS = ("name", "id", "potion_id", "price")
_REWARD_SNAPSHOT_FIELDS = (
    "reward_type",
    "type",
    "kind",
    "name",
    "id",
    "card_id",
    "relic_id",
    "potion_id",
    "choice_index",
    "amount",
    "price",
)


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
        "deck_cards": [_snapshot_fields(card, _CARD_SNAPSHOT_FIELDS) for card in game.get("deck", [])],
        "relics": [relic.get("name", relic.get("id")) for relic in game.get("relics", [])],
        "relic_items": [_snapshot_fields(relic, _RELIC_SNAPSHOT_FIELDS) for relic in game.get("relics", [])],
        "potions": [
            {
                **_snapshot_fields(potion, _POTION_SNAPSHOT_FIELDS),
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
                **_snapshot_fields(card, _CARD_SNAPSHOT_FIELDS),
                "upgrades": card.get("upgrades", 0),
            }
            for card in screen_state.get("cards", [])
        ]
    if game.get("screen_type") == "BOSS_REWARD":
        snapshot["boss_relic_options"] = [
            _snapshot_fields(relic, _RELIC_SNAPSHOT_FIELDS)
            for relic in screen_state.get("relics", [])
        ]
    if game.get("screen_type") == "CHEST":
        snapshot["chest"] = {
            "chest_open": screen_state.get("chest_open"),
            "rewards": [
                _snapshot_fields(reward, _REWARD_SNAPSHOT_FIELDS)
                for reward in screen_state.get("rewards", [])
            ],
        }
    if game.get("screen_type") == "SHOP_SCREEN":
        snapshot["shop"] = {
            "cards": [
                {
                    **_snapshot_fields(card, _CARD_SNAPSHOT_FIELDS),
                    "price": card.get("price"),
                }
                for card in screen_state.get("cards", [])
            ],
            "relics": [
                _snapshot_fields(relic, _RELIC_SNAPSHOT_FIELDS)
                for relic in screen_state.get("relics", [])
            ],
            "potions": [
                _snapshot_fields(potion, _POTION_SNAPSHOT_FIELDS)
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
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision_payload = {
        "actions": actions,
        "reason": reason,
        "should_stop": should_stop,
        "learn_card_pick": learn_card_pick,
    }
    if metadata:
        decision_payload["metadata"] = metadata
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "step": step,
        "summary": _summarize_state(state),
        "state": _snapshot_state(state),
        "decision": decision_payload,
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
    if result.post_action_settle_reason is not None:
        payload["post_action_settle_reason"] = result.post_action_settle_reason
    if result.post_action_settle_error is not None:
        payload["post_action_settle_error"] = result.post_action_settle_error
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


def _write_episode_manifest(
    result: EpisodeResult,
    *,
    manifest_dir: Path | None,
    knowledge_dir: Path | None,
    shadow_dir: Path | None,
    advice_dir: Path | None,
    route_model_path: Path,
    potion_model_path: Path,
    deck_model_path: Path,
    combat_model_path: Path,
    echo: bool = False,
) -> EpisodeResult:
    if not result.log_path.exists() or result.log_path.stat().st_size == 0:
        return result

    knowledge = None
    loaded_knowledge_dir = None
    if knowledge_dir is not None and knowledge_dir.exists():
        try:
            knowledge = StaticKnowledge.load(knowledge_dir)
            loaded_knowledge_dir = knowledge_dir
        except Exception as exc:  # pragma: no cover - manifest writing should not fail a live run.
            if echo:
                print(f"Could not load static knowledge for manifest: {exc}", file=sys.stderr)

    try:
        manifest, shadow_examples = build_manifest(
            [result.log_path],
            knowledge=knowledge,
            knowledge_dir=loaded_knowledge_dir,
        )
        manifest["episode"] = {
            "status": result.status,
            "steps": result.steps,
            "victory": result.victory,
            "floor": result.floor,
            "score": result.score,
            "log_path": str(result.log_path),
        }
        output_dir = manifest_dir or (result.log_path.parent / "manifests")
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / f"{result.log_path.stem}.manifest.json"
        if advice_dir is not None:
            advice_output_dir = advice_dir / result.log_path.stem
            try:
                models = ShadowModels.load(
                    route_model_path=route_model_path,
                    potion_model_path=potion_model_path,
                    deck_model_path=deck_model_path,
                    combat_model_path=combat_model_path,
                )
                advice_summary = write_advice(advice_output_dir, score_shadow_examples(shadow_examples, models=models))
                manifest["shadow_advice"] = {
                    **advice_summary,
                    "path": str(advice_output_dir),
                }
                result.shadow_advice_path = advice_output_dir
            except Exception as exc:  # pragma: no cover - advice scoring should not fail a live run.
                if echo:
                    print(f"Could not write shadow advice: {exc}", file=sys.stderr)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if shadow_dir is not None:
            write_shadow_examples(shadow_dir / result.log_path.stem, shadow_examples)
        category, reason = _single_log_manifest_result(manifest)
        result.manifest_path = manifest_path
        result.manifest_category = category
        result.manifest_reason = reason
        if echo and category:
            print(f"Run manifest: {manifest_path} ({category}: {reason})")
        if echo and result.shadow_advice_path is not None:
            print(f"Shadow advice: {result.shadow_advice_path}")
    except Exception as exc:  # pragma: no cover - preserve the episode result even if reporting breaks.
        if echo:
            print(f"Could not write run manifest: {exc}", file=sys.stderr)
    return result


def _single_log_manifest_result(manifest: dict[str, Any]) -> tuple[str | None, str | None]:
    for category in ("clean_trainable", "diagnostic_excluded", "infra_blocked"):
        rows = manifest.get("categories", {}).get(category, [])
        if rows:
            return category, rows[0].get("reason")
    return None, None


if __name__ == "__main__":
    raise SystemExit(main())
