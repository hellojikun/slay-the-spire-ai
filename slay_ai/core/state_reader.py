"""State reading, retry, and stability checks for episode execution."""

from __future__ import annotations

import time
from typing import Any

from ..mcp.client import MCPClient, MCPError


BASE_GAME_STATE_INCLUDE = ["player", "deck", "relics", "potions", "combat", "screen"]
MAP_GAME_STATE_INCLUDE = ["player", "screen", "map"]
MAP_OBSERVATION_TIMEOUT = 2.5
MAP_OBSERVATION_FAILURE_LIMIT = 2


def read_game_state(client: MCPClient, attempts: int = 8, delay: float = 0.25) -> dict[str, Any]:
    last_error: MCPError | None = None
    for attempt in range(attempts):
        try:
            return client.get_game_state(BASE_GAME_STATE_INCLUDE)
        except MCPError as exc:
            last_error = exc
            if is_transient_state_error(exc):
                time.sleep(min(max(delay, 0.15) * (1 + attempt * 0.25), 0.75))
            else:
                time.sleep(delay)
    assert last_error is not None
    raise last_error


def read_stable_game_state(client: MCPClient, attempts: int = 12, delay: float = 0.25) -> dict[str, Any]:
    state = read_game_state(client, attempts=attempts, delay=delay)
    for _ in range(attempts):
        if looks_stable(client, state):
            return augment_map_state(client, state)
        time.sleep(delay)
        state = read_game_state(client, attempts=2, delay=delay)
    return augment_map_state(client, state)


def augment_map_state(client: MCPClient, state: dict[str, Any]) -> dict[str, Any]:
    game = state.get("game_state", {})
    if not state.get("in_game") or game.get("screen_type") != "MAP":
        return state
    failures = int(getattr(client, "_map_observation_failures", 0) or 0)
    if failures >= MAP_OBSERVATION_FAILURE_LIMIT:
        return _state_with_map_observation(
            state,
            {
                "status": "disabled",
                "source": "get_game_state_map",
                "include": MAP_GAME_STATE_INCLUDE,
                "failures": failures,
                "reason": "map observation disabled after repeated failures",
            },
        )
    started = time.monotonic()
    old_timeout = getattr(client, "timeout", None)
    try:
        if isinstance(old_timeout, (int, float)):
            client.timeout = min(float(old_timeout), MAP_OBSERVATION_TIMEOUT)
        map_state = client.get_game_state(MAP_GAME_STATE_INCLUDE)
    except MCPError as exc:
        setattr(client, "_map_observation_failures", failures + 1)
        return _state_with_map_observation(
            state,
            {
                "status": "error",
                "source": "get_game_state_map",
                "include": MAP_GAME_STATE_INCLUDE,
                "latency_ms": _elapsed_ms(started),
                "error": str(exc),
                "failures": failures + 1,
            },
        )
    finally:
        if isinstance(old_timeout, (int, float)):
            client.timeout = old_timeout

    if _map_screen_type(map_state) not in {None, "MAP"}:
        setattr(client, "_map_observation_failures", failures + 1)
        return _state_with_map_observation(
            state,
            {
                "status": "stale",
                "source": "get_game_state_map",
                "include": MAP_GAME_STATE_INCLUDE,
                "latency_ms": _elapsed_ms(started),
                "screen_type": _map_screen_type(map_state),
                "failures": failures + 1,
            },
        )

    payload = _extract_map_payload(map_state)
    setattr(client, "_map_observation_failures", 0)
    observation: dict[str, Any] = {
        "status": "success" if payload is not None else "unavailable",
        "source": "get_game_state_map",
        "include": MAP_GAME_STATE_INCLUDE,
        "latency_ms": _elapsed_ms(started),
        "node_count": _count_map_nodes(payload),
    }
    if payload is not None:
        observation["map"] = payload
    else:
        observation["keys"] = _top_level_keys(map_state)
    return _state_with_map_observation(state, observation)


def looks_stable(client: MCPClient, state: dict[str, Any]) -> bool:
    if not state.get("in_game"):
        return True
    game = state.get("game_state", {})
    if game.get("screen_type") == "NONE" and game.get("room_phase") == "COMBAT":
        combat = game.get("combat_state", {})
        if combat_snapshot_is_settling(combat):
            return False
        try:
            commands = client.call_tool("get_available_commands", {})
        except MCPError:
            return False
        tools = {tool.get("tool") for tool in commands.get("available_tools", [])}
        return bool({"play_card", "end_turn"} & tools)
    return True


def combat_snapshot_is_settling(combat: dict[str, Any]) -> bool:
    monsters = combat.get("monsters") or []
    if not monsters:
        return True

    hand = combat.get("hand") or []
    if not hand:
        return True

    if combat_intents_are_settling(combat):
        return True

    player = combat.get("player", {})
    try:
        energy = int(player.get("current_energy", 0) or 0)
    except (TypeError, ValueError):
        energy = 0
    if energy <= 0 or len(hand) >= 3:
        return False
    if combat_has_current_turn_activity(combat):
        return False
    return True


def combat_intents_are_settling(combat: dict[str, Any]) -> bool:
    if combat_has_current_turn_activity(combat):
        return False
    for monster in combat.get("monsters", []) or []:
        intent = str(monster.get("intent") or "").upper()
        move = monster.get("move")
        if intent == "DEBUG" and not move:
            return True
    return False


def combat_has_current_turn_activity(combat: dict[str, Any]) -> bool:
    for key in ("cards_played_this_turn", "cards_discarded_this_turn", "cards_exhausted_this_turn"):
        value = combat.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
        if isinstance(value, (list, tuple, set, dict)) and bool(value):
            return True
    return False


def recover_state_read(client: MCPClient, interval: float) -> dict[str, Any]:
    time.sleep(max(interval, 0.5))
    return read_stable_game_state(client, attempts=24, delay=0.2)


def _state_with_map_observation(state: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    updated = dict(state)
    updated_game = dict(state.get("game_state", {}))
    updated_game["map_observation"] = observation
    updated["game_state"] = updated_game
    return updated


def _extract_map_payload(state: dict[str, Any]) -> Any | None:
    if not isinstance(state, dict):
        return None
    game = state.get("game_state")
    if isinstance(game, dict):
        for key in ("map", "map_state", "map_nodes"):
            if key in game and game.get(key) is not None:
                return game[key]
    for key in ("map", "map_state", "map_nodes"):
        if key in state and state.get(key) is not None:
            return state[key]
    return None


def _map_screen_type(state: dict[str, Any]) -> str | None:
    if not isinstance(state, dict):
        return None
    game = state.get("game_state")
    if isinstance(game, dict) and game.get("screen_type") is not None:
        return str(game.get("screen_type"))
    if state.get("screen_type") is not None:
        return str(state.get("screen_type"))
    return None


def _count_map_nodes(payload: Any) -> int:
    count = 0

    def visit(value: Any) -> None:
        nonlocal count
        if isinstance(value, dict):
            if "x" in value and "y" in value:
                count += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return count


def _top_level_keys(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    keys = [str(key) for key in value.keys()]
    game = value.get("game_state")
    if isinstance(game, dict):
        keys.extend(f"game_state.{key}" for key in game.keys())
    return sorted(keys)[:20]


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def is_transient_state_error(exc: MCPError) -> bool:
    text = str(exc).lower()
    return ("internal error" in text and "null" in text) or "read_state_failed" in text


def probe_mcp_health(client: MCPClient) -> dict[str, Any] | None:
    probe = getattr(client, "probe_health", None)
    if not callable(probe):
        return None
    try:
        return probe()
    except Exception as exc:
        return {"status": "probe_failed", "error": repr(exc)}
