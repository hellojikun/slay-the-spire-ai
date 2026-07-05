"""Route/map policy for the heuristic bot."""

from __future__ import annotations

from typing import Any

from .memory import StrategyMemory, normalize_card_name
from .policy_decision import Decision


ACT1_REST_OVER_ELITE_HP_RATIO = 0.72
ACT1_BLOCK_STABILIZER_CARDS = {
    "Armaments",
    "Disarm",
    "Flame Barrier",
    "Ghostly Armor",
    "Impervious",
    "Power Through",
    "Second Wind",
    "Shockwave",
    "Shrug It Off",
    "True Grit",
}


def decide_route(game: dict[str, Any], memory: StrategyMemory) -> Decision:
    nodes = game.get("screen_state", {}).get("next_nodes", [])
    hp_ratio = _hp_ratio(game)
    floor = int(game.get("floor", 0))
    if not nodes and game.get("screen_state", {}).get("boss_available"):
        return Decision([{"action": "choose", "choice_index": 1}], "Boss node available.")
    if not nodes:
        return Decision([], "No map choices visible.")
    has_rest_choice = any(str(node.get("symbol", "")) == "R" for node in nodes)
    has_shop_choice = any(str(node.get("symbol", "")) == "$" for node in nodes)
    has_safe_choice = has_rest_choice or has_shop_choice or any(str(node.get("symbol", "")) == "?" for node in nodes)
    ranked = [
        (_map_node_score(memory, game, node, hp_ratio, floor, has_rest_choice, has_safe_choice, has_shop_choice), index, node)
        for index, node in enumerate(nodes, start=1)
    ]
    score, index, node = max(ranked, key=lambda item: item[0])
    return Decision([{"action": "choose", "choice_index": index}], f"Route to {node.get('symbol')} at x={node.get('x')} score {score:.1f}.")


def _map_node_score(
    memory: StrategyMemory,
    game: dict[str, Any],
    node: dict[str, Any],
    hp_ratio: float,
    floor: int,
    has_rest_choice: bool,
    has_safe_choice: bool,
    has_shop_choice: bool,
) -> float:
    symbol = str(node.get("symbol", ""))
    score = memory.route_score(symbol, hp_ratio, floor)
    late_event_risk = _act1_late_event_risk_state(game, hp_ratio, floor, has_rest_choice, has_shop_choice)
    if (
        symbol == "E"
        and has_rest_choice
        and int(game.get("act", 1) or 1) == 1
        and floor >= 5
        and hp_ratio < ACT1_REST_OVER_ELITE_HP_RATIO
    ):
        score -= 42
    if (
        symbol == "E"
        and has_rest_choice
        and int(game.get("act", 1) or 1) == 1
        and floor >= 5
        and hp_ratio <= 0.95
        and not _has_elite_tempo_potion(game)
    ):
        score -= 35
    if (
        symbol == "E"
        and has_safe_choice
        and not has_rest_choice
        and int(game.get("act", 1) or 1) == 1
        and floor >= 9
        and hp_ratio < 0.88
        and not _has_high_impact_elite_potion(game)
    ):
        score -= 34
    if (
        symbol == "M"
        and has_safe_choice
        and int(game.get("act", 1) or 1) == 1
        and floor >= 5
        and hp_ratio < 0.45
    ):
        score -= 45 if has_shop_choice else (24 if has_rest_choice else 18)
    elif (
        symbol == "M"
        and has_safe_choice
        and int(game.get("act", 1) or 1) == 1
        and floor >= 2
        and hp_ratio < 0.75
    ):
        score -= 12 if late_event_risk else 28
    if (
        symbol == "M"
        and has_shop_choice
        and int(game.get("act", 1) or 1) == 1
        and floor >= 4
        and hp_ratio < 0.85
    ):
        score -= 55
    elif (
        symbol == "M"
        and any(str(node.get("symbol", "")) == "?" for node in game.get("screen_state", {}).get("next_nodes", []))
        and int(game.get("act", 1) or 1) == 1
        and floor >= 3
        and hp_ratio < 0.90
        and _act1_deck_lacks_premium_block(game)
    ):
        score -= 12 if late_event_risk else 30
    if (
        symbol == "?"
        and late_event_risk
        and any(str(node.get("symbol", "")) == "M" for node in game.get("screen_state", {}).get("next_nodes", []))
    ):
        score -= 18
    return score


def _hp_ratio(obj: dict[str, Any]) -> float:
    current = float(obj.get("current_hp", 1) or 1)
    max_hp = float(obj.get("max_hp", current) or current)
    return current / max(max_hp, 1.0)


def _act1_deck_lacks_premium_block(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    names = _deck_card_names(game)
    if not names:
        return False
    return not any(name in ACT1_BLOCK_STABILIZER_CARDS for name in names)


def _deck_card_names(game: dict[str, Any]) -> list[str]:
    names = []
    for card in game.get("deck", []):
        if isinstance(card, dict):
            names.append(_card_key(card))
        else:
            names.append(normalize_card_name(str(card)))
    return names


def _card_key(card: dict[str, Any]) -> str:
    return normalize_card_name(str(card.get("id") or card.get("name") or ""))


def _potion_key(potion: dict[str, Any]) -> str:
    return "".join(ch for ch in str(potion.get("id") or potion.get("name") or "").lower() if ch.isalnum())


def _has_elite_tempo_potion(game: dict[str, Any]) -> bool:
    tempo_tokens = {
        "attack",
        "bronze",
        "distilledchaos",
        "duplication",
        "essenceofsteel",
        "explosive",
        "fear",
        "fire",
        "forge",
        "heartofiron",
        "liquidbronze",
        "power",
        "steroid",
        "strength",
        "thorn",
        "weak",
        "blessingoftheforge",
    }
    for potion in game.get("potions", []):
        if potion.get("is_empty"):
            continue
        key = _potion_key(potion)
        if any(token in key for token in tempo_tokens):
            return True
    return False


def _has_high_impact_elite_potion(game: dict[str, Any]) -> bool:
    high_impact_tokens = {
        "attack",
        "distilledchaos",
        "duplication",
        "explosive",
        "fear",
        "fire",
        "forge",
        "power",
        "steroid",
        "strength",
        "blessingoftheforge",
    }
    for potion in game.get("potions", []):
        if potion.get("is_empty"):
            continue
        key = _potion_key(potion)
        if any(token in key for token in high_impact_tokens):
            return True
    return False


def _act1_late_event_risk_state(
    game: dict[str, Any],
    hp_ratio: float,
    floor: int,
    has_rest_choice: bool,
    has_shop_choice: bool,
) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    if floor < 11:
        return False
    if has_rest_choice or has_shop_choice:
        return False
    if hp_ratio < 0.35 or hp_ratio >= 0.65:
        return False
    if _has_high_impact_elite_potion(game):
        return False
    return True
