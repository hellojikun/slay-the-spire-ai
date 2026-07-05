"""State reading, retry, and stability checks for episode execution."""

from __future__ import annotations

import time
from typing import Any

from ..mcp.client import MCPClient, MCPError


BASE_GAME_STATE_INCLUDE = ["player", "deck", "relics", "potions", "combat", "screen"]


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
            return state
        time.sleep(delay)
        state = read_game_state(client, attempts=2, delay=delay)
    return state


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
