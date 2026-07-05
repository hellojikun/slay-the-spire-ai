"""Monster-state helpers shared by policy, search, and logging."""

from __future__ import annotations

from typing import Any


def monster_attack_damage(monster: dict[str, Any]) -> int:
    """Return current-turn attack damage for the MCP monster shape."""
    if not isinstance(monster, dict) or monster_gone(monster):
        return 0
    move = monster.get("move")
    if isinstance(move, dict) and "damage" in move:
        damage = max(0, _as_int(move.get("damage", 0)))
        hits = max(1, _as_int(move.get("hits", 1), default=1))
        return damage * hits
    if "damage" in monster:
        damage = max(0, _as_int(monster.get("damage", 0)))
        hits = max(1, _as_int(monster.get("hits", 1), default=1))
        return damage * hits
    for key in ("intent_damage", "move_damage", "attack"):
        if key in monster:
            return max(0, _as_int(monster.get(key, 0)))
    return 0


def incoming_damage(combat_or_monsters: dict[str, Any] | list[dict[str, Any]]) -> int:
    if isinstance(combat_or_monsters, dict):
        monsters = combat_or_monsters.get("monsters", [])
    else:
        monsters = combat_or_monsters
    if not isinstance(monsters, list):
        return 0
    return sum(monster_attack_damage(monster) for monster in monsters if isinstance(monster, dict))


def monster_gone(monster: dict[str, Any]) -> bool:
    return bool(monster.get("is_dead") or monster.get("is_gone"))


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default
