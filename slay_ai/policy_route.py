"""Route/map policy for the heuristic bot."""

from __future__ import annotations

from typing import Any

from .memory import StrategyMemory, normalize_card_name
from .policy_decision import Decision
from .readiness import act1_readiness


ACT1_REST_OVER_ELITE_HP_RATIO = 0.72
ACT1_FORCED_ELITE_NO_BUFFER_PENALTY = 32.0
ACT1_HEALTHY_FORCED_ELITE_NO_BUFFER_PENALTY = 55.0
ACT1_DEEP_FORCED_ELITE_NO_BUFFER_PENALTY = 24.0
ACT1_FORCED_ELITE_BUFFER_BONUS = 18.0
ACT1_ELITE_CHAIN_LOW_BUFFER_PENALTY = 38.0
ACT2_LOW_HP_MONSTER_OVER_QUESTION_PENALTY = 35.0
ACT2_INJURED_MONSTER_OVER_QUESTION_PENALTY = 18.0
ACT2_LOW_HP_ROUTE_RISK_CAP = 95.0
ROUTE_LOOKAHEAD_HORIZON = 6
ROUTE_PATH_CAP = 128
ROUTE_CHILD_KEYS = ("children", "next_nodes", "edges", "connected_nodes", "connections", "links")
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
    has_question_choice = any(str(node.get("symbol", "")) == "?" for node in nodes)
    has_safe_choice = has_rest_choice or has_shop_choice or any(str(node.get("symbol", "")) == "?" for node in nodes)
    route_context = _build_route_context(game)
    ranked: list[tuple[float, int, dict[str, Any], dict[str, Any]]] = []
    route_options = []
    for index, node in enumerate(nodes, start=1):
        base_score = _map_node_score(
            memory,
            game,
            node,
            hp_ratio,
            floor,
            has_rest_choice,
            has_safe_choice,
            has_shop_choice,
            has_question_choice,
        )
        lookahead_adjustment, lookahead_features = _route_lookahead_adjustment(game, node, hp_ratio, route_context)
        score = base_score + lookahead_adjustment
        option_record = {
            "choice_index": index,
            "symbol": node.get("symbol"),
            "x": node.get("x"),
            "y": node.get("y"),
            "base_score": round(base_score, 1),
            "lookahead_adjustment": round(lookahead_adjustment, 1),
            "score": round(score, 1),
        }
        if lookahead_features:
            option_record["lookahead"] = lookahead_features
        route_options.append(option_record)
        ranked.append((score, index, node, option_record))
    score, index, node, selected_record = max(ranked, key=lambda item: item[0])
    game["route_evaluation"] = {
        "map_status": _map_observation_status(game),
        "horizon": ROUTE_LOOKAHEAD_HORIZON if route_context else 0,
        "node_count": len(route_context["nodes"]) if route_context else 0,
        "edge_count": sum(len(edges) for edges in route_context["edges"].values()) if route_context else 0,
        "options": route_options,
    }
    reason = f"Route to {node.get('symbol')} at x={node.get('x')} score {score:.1f}."
    if selected_record.get("lookahead_adjustment"):
        reason = (
            f"Route to {node.get('symbol')} at x={node.get('x')} score {score:.1f} "
            f"(map risk {selected_record['lookahead_adjustment']:+.1f})."
        )
    return Decision([{"action": "choose", "choice_index": index}], reason)


def _map_node_score(
    memory: StrategyMemory,
    game: dict[str, Any],
    node: dict[str, Any],
    hp_ratio: float,
    floor: int,
    has_rest_choice: bool,
    has_safe_choice: bool,
    has_shop_choice: bool,
    has_question_choice: bool,
) -> float:
    symbol = str(node.get("symbol", ""))
    score = memory.route_score(symbol, hp_ratio, floor)
    act = int(game.get("act", 1) or 1)
    late_event_risk = _act1_late_event_risk_state(game, hp_ratio, floor, has_rest_choice, has_shop_choice)
    if (
        symbol == "E"
        and has_rest_choice
        and act == 1
        and floor >= 5
        and hp_ratio < ACT1_REST_OVER_ELITE_HP_RATIO
    ):
        score -= 42
    if (
        symbol == "E"
        and has_rest_choice
        and act == 1
        and floor >= 5
        and hp_ratio <= 0.95
        and not _has_elite_tempo_potion(game)
    ):
        score -= 35
    if (
        symbol == "E"
        and has_safe_choice
        and not has_rest_choice
        and act == 1
        and floor >= 9
        and hp_ratio < 0.88
        and not _has_high_impact_elite_potion(game)
    ):
        score -= 34
    if (
        symbol == "M"
        and has_safe_choice
        and act == 1
        and floor >= 5
        and hp_ratio < 0.45
    ):
        score -= 45 if has_shop_choice else (24 if has_rest_choice else 18)
    elif (
        symbol == "M"
        and has_safe_choice
        and act == 1
        and floor >= 2
        and hp_ratio < 0.75
    ):
        score -= 12 if late_event_risk else 28
    if (
        symbol == "M"
        and has_shop_choice
        and act == 1
        and floor >= 4
        and hp_ratio < 0.85
    ):
        score -= 55
    elif (
        symbol == "M"
        and any(str(node.get("symbol", "")) == "?" for node in game.get("screen_state", {}).get("next_nodes", []))
        and act == 1
        and floor >= 3
        and hp_ratio < 0.90
        and _act1_deck_lacks_premium_block(game)
    ):
        score -= 12 if late_event_risk else 30
    if symbol == "M" and has_question_choice and act >= 2 and hp_ratio < 0.35:
        score -= ACT2_LOW_HP_MONSTER_OVER_QUESTION_PENALTY
    elif symbol == "M" and has_question_choice and act >= 2 and hp_ratio < 0.45:
        score -= ACT2_INJURED_MONSTER_OVER_QUESTION_PENALTY
    if (
        symbol == "?"
        and late_event_risk
        and any(str(node.get("symbol", "")) == "M" for node in game.get("screen_state", {}).get("next_nodes", []))
    ):
        score -= 18
    return score


def _route_lookahead_adjustment(
    game: dict[str, Any],
    choice_node: dict[str, Any],
    hp_ratio: float,
    route_context: dict[str, Any] | None,
) -> tuple[float, dict[str, Any]]:
    if route_context is None:
        return 0.0, {}
    node_id = _find_route_node_id(route_context, choice_node)
    if node_id is None:
        return 0.0, {"map_match": False}

    paths = _enumerate_symbol_paths(route_context, node_id, ROUTE_LOOKAHEAD_HORIZON)
    if not paths:
        return 0.0, {"map_match": True, "paths": 0}

    features = _route_path_features(paths)
    adjustment = 0.0
    if hp_ratio < 0.35 and features["forced_elite_within_3"]:
        adjustment -= 100
    elif hp_ratio < 0.45 and features["forced_elite_within_3"]:
        adjustment -= 35 if _has_high_impact_elite_potion(game) else 70
    if hp_ratio < 0.35 and features["forced_combat_within_2"]:
        adjustment -= 45
    nearest_rest = features.get("nearest_rest")
    nearest_shop = features.get("nearest_shop")
    if int(game.get("act", 1) or 1) == 1 and features["forced_elite_within_3"]:
        if nearest_rest is None and nearest_shop is None:
            if hp_ratio >= 0.75 and int(game.get("floor", 0) or 0) >= 3:
                adjustment -= ACT1_HEALTHY_FORCED_ELITE_NO_BUFFER_PENALTY
            else:
                adjustment -= ACT1_FORCED_ELITE_NO_BUFFER_PENALTY
        elif (nearest_rest is not None and nearest_rest <= 2) or (nearest_shop is not None and nearest_shop <= 1):
            adjustment += ACT1_FORCED_ELITE_BUFFER_BONUS
        elif str(choice_node.get("symbol", "")).upper() == "E" and hp_ratio < 0.80:
            adjustment -= ACT1_ELITE_CHAIN_LOW_BUFFER_PENALTY
            features["act1_elite_chain_penalty"] = -ACT1_ELITE_CHAIN_LOW_BUFFER_PENALTY
    elif int(game.get("act", 1) or 1) == 1 and features["forced_elite_within_5"]:
        if nearest_rest is None and nearest_shop is None:
            adjustment -= ACT1_DEEP_FORCED_ELITE_NO_BUFFER_PENALTY
        elif (nearest_rest is not None and nearest_rest <= 4) or (nearest_shop is not None and nearest_shop <= 3):
            adjustment += ACT1_FORCED_ELITE_BUFFER_BONUS * 0.5
    if hp_ratio < 0.50 and nearest_rest is not None and nearest_rest <= 2:
        adjustment += 35
    if hp_ratio < 0.50 and nearest_shop is not None and nearest_shop <= 2 and int(game.get("gold", 0) or 0) >= 80:
        adjustment += 25

    act2_adjustment, act2_features = _route_act2_risk_adjustment(game, choice_node, hp_ratio, features)
    adjustment += act2_adjustment
    features.update(act2_features)

    readiness_adjustment, readiness_features = _route_readiness_adjustment(game, choice_node, features)
    adjustment += readiness_adjustment
    features.update(readiness_features)

    return adjustment, features


def _route_act2_risk_adjustment(
    game: dict[str, Any],
    choice_node: dict[str, Any],
    hp_ratio: float,
    lookahead_features: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    if int(game.get("act", 1) or 1) < 2 or not lookahead_features.get("map_match"):
        return 0.0, {}
    if hp_ratio >= 0.50 or lookahead_features.get("forced_elite_within_3"):
        return 0.0, {}
    symbol = str(choice_node.get("symbol", "")).upper()
    if symbol in {"$", "R", "T"}:
        return 0.0, {}
    immediate_combat = symbol in {"M", "E"}
    forced_combat = bool(lookahead_features.get("forced_combat_within_2")) or immediate_combat
    if not forced_combat:
        return 0.0, {}

    nearest_rest = lookahead_features.get("nearest_rest")
    nearest_shop = lookahead_features.get("nearest_shop")
    close_rest = nearest_rest is not None and nearest_rest <= 1
    close_shop = nearest_shop is not None and nearest_shop <= 1 and int(game.get("gold", 0) or 0) >= 80
    flags: list[str] = []
    gaps: list[str] = []
    penalty = 0.0

    if hp_ratio < 0.35:
        flags.append("act2_critical_hp_forced_combat")
        penalty -= 30.0 if immediate_combat else 22.0
    else:
        flags.append("act2_low_hp_forced_combat")
        penalty -= 18.0 if immediate_combat else 12.0
    if not (close_rest or close_shop):
        flags.append("act2_no_recovery_buffer")
        penalty -= 26.0 if hp_ratio < 0.35 else 18.0
    if not _has_act2_emergency_potion(game):
        flags.append("act2_no_emergency_potion")
        penalty -= 16.0
    if _act2_deck_lacks_premium_block(game):
        gaps.append("act2_premium_block_missing")
        penalty -= 12.0
    if _act2_deck_lacks_weak(game):
        gaps.append("act2_weak_missing")
        penalty -= 8.0

    penalty = max(-ACT2_LOW_HP_ROUTE_RISK_CAP, penalty)
    if not penalty:
        return 0.0, {}
    return penalty, {
        "act2_route_penalty": round(penalty, 1),
        "act2_route_flags": flags,
        "act2_route_gaps": gaps,
    }


def _route_readiness_adjustment(
    game: dict[str, Any],
    choice_node: dict[str, Any],
    lookahead_features: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    if int(game.get("act", 1) or 1) != 1 or not lookahead_features.get("map_match"):
        return 0.0, {}
    symbol = str(choice_node.get("symbol", "")).upper()
    if symbol in {"$", "R", "T"}:
        return 0.0, {}
    state = dict(game)
    screen_state = dict(game.get("screen_state") if isinstance(game.get("screen_state"), dict) else {})
    screen_state["next_nodes"] = [choice_node]
    state["screen_state"] = screen_state
    state["route_evaluation"] = {
        "options": [
            {
                "choice_index": 1,
                "symbol": choice_node.get("symbol"),
                "score": 0.0,
                "lookahead": lookahead_features,
            }
        ]
    }
    readiness = act1_readiness(state)
    flags = list(readiness.get("risk_flags") or [])
    gaps = list(readiness.get("gaps") or [])
    immediate_elite = symbol == "E"
    forced_elite_within_3 = bool(lookahead_features.get("forced_elite_within_3"))
    forced_elite_within_5 = bool(lookahead_features.get("forced_elite_within_5"))
    elite_path = immediate_elite or forced_elite_within_3 or forced_elite_within_5

    penalty = 0.0
    if elite_path:
        close_elite = immediate_elite or forced_elite_within_3
        if "elite_not_ready" in flags:
            penalty -= 30.0 if close_elite else 18.0
        if "forced_elite_no_tempo_potion" in flags:
            penalty -= 18.0 if close_elite else 10.0
        if "forced_elite_aoe_gap" in flags:
            penalty -= 12.0 if close_elite else 8.0
        if "forced_elite_weak_gap" in flags:
            penalty -= 10.0 if close_elite else 6.0
        if close_elite and "premium_block_missing" in gaps:
            penalty -= 12.0
    if "act1_low_buffer_no_recovery" in flags:
        penalty -= 20.0
        if "hallway_no_immediate_tempo_potion" in flags:
            penalty -= 10.0
        if "hallway_lacks_premium_block" in flags:
            penalty -= 10.0

    penalty = max(-85.0, penalty)
    if not penalty:
        return 0.0, {}
    return penalty, {
        "readiness_penalty": round(penalty, 1),
        "readiness_flags": flags,
        "readiness_gaps": gaps,
    }


def _build_route_context(game: dict[str, Any]) -> dict[str, Any] | None:
    observation = game.get("map_observation")
    if not isinstance(observation, dict) or observation.get("status") != "success":
        return None
    payload = observation.get("map")
    raw_nodes = _collect_map_nodes(payload)
    if not raw_nodes:
        return None

    nodes: dict[str, dict[str, Any]] = {}
    by_position: dict[tuple[Any, Any], str] = {}
    for raw_node in raw_nodes:
        node_id = _route_node_id(raw_node)
        if node_id is None:
            continue
        compact = {
            "id": node_id,
            "symbol": _route_node_symbol(raw_node),
            "x": raw_node.get("x"),
            "y": raw_node.get("y"),
        }
        nodes[node_id] = compact
        position = (_coord(raw_node.get("x")), _coord(raw_node.get("y")))
        if position[0] is not None and position[1] is not None:
            by_position[position] = node_id

    edges: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for raw_node in raw_nodes:
        node_id = _route_node_id(raw_node)
        if node_id not in nodes:
            continue
        for key in ROUTE_CHILD_KEYS:
            for child_ref in _iter_child_refs(raw_node.get(key)):
                child_id = _route_ref_id(child_ref)
                if child_id in nodes and child_id not in edges[node_id]:
                    edges[node_id].append(child_id)

    return {"nodes": nodes, "edges": edges, "by_position": by_position}


def _collect_map_nodes(payload: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen_objects: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            object_id = id(value)
            if object_id in seen_objects:
                return
            seen_objects.add(object_id)
            if _looks_like_route_node(value):
                nodes.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return nodes


def _looks_like_route_node(value: dict[str, Any]) -> bool:
    if "x" not in value or "y" not in value:
        return False
    if any(key in value for key in ("symbol", "room", "type", "room_type", "node_type", "map_symbol")):
        return True
    return any(key in value for key in ROUTE_CHILD_KEYS)


def _route_node_id(node: dict[str, Any]) -> str | None:
    for key in ("id", "node_id", "key"):
        if node.get(key) is not None:
            return str(node.get(key))
    x = _coord(node.get("x"))
    y = _coord(node.get("y"))
    if x is None or y is None:
        return None
    return f"{x}:{y}"


def _route_ref_id(value: Any) -> str | None:
    if isinstance(value, dict):
        return _route_node_id(value)
    if isinstance(value, (str, int)):
        return str(value)
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        x = _coord(value[0])
        y = _coord(value[1])
        if x is not None and y is not None:
            return f"{x}:{y}"
    return None


def _iter_child_refs(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        if _route_node_id(value) is not None:
            return [value]
        refs: list[Any] = []
        for child in value.values():
            refs.extend(_iter_child_refs(child))
        return refs
    if isinstance(value, list):
        refs = []
        for item in value:
            refs.extend(_iter_child_refs(item))
        return refs
    return [value]


def _route_node_symbol(node: dict[str, Any]) -> str:
    raw = None
    for key in ("symbol", "room", "type", "room_type", "node_type", "map_symbol"):
        if node.get(key) is not None:
            raw = node.get(key)
            break
    if isinstance(raw, dict):
        for key in ("symbol", "type", "name", "id"):
            if raw.get(key) is not None:
                raw = raw.get(key)
                break
    text = str(raw or "").strip()
    upper = text.upper()
    if upper in {"M", "?", "$", "R", "E", "T", "B"}:
        return upper
    if "ELITE" in upper:
        return "E"
    if "MONSTER" in upper or "ENEMY" in upper:
        return "M"
    if "REST" in upper or "CAMPFIRE" in upper:
        return "R"
    if "SHOP" in upper or "MERCHANT" in upper:
        return "$"
    if "TREASURE" in upper or "CHEST" in upper:
        return "T"
    if "BOSS" in upper:
        return "B"
    if "EVENT" in upper or "QUESTION" in upper or "UNKNOWN" in upper:
        return "?"
    return text[:1].upper() if text else ""


def _find_route_node_id(route_context: dict[str, Any], choice_node: dict[str, Any]) -> str | None:
    explicit = _route_ref_id(choice_node)
    if explicit in route_context["nodes"]:
        return explicit
    position = (_coord(choice_node.get("x")), _coord(choice_node.get("y")))
    return route_context["by_position"].get(position)


def _enumerate_symbol_paths(route_context: dict[str, Any], start_id: str, horizon: int) -> list[list[str]]:
    paths: list[list[str]] = []
    nodes = route_context["nodes"]
    edges = route_context["edges"]

    def visit(node_id: str, path: list[str], depth: int) -> None:
        if len(paths) >= ROUTE_PATH_CAP:
            return
        if depth >= horizon:
            paths.append(path)
            return
        children = [child for child in edges.get(node_id, []) if child in nodes]
        if not children:
            paths.append(path)
            return
        for child in children:
            visit(child, [*path, nodes[child]["symbol"]], depth + 1)

    if start_id not in nodes:
        return []
    visit(start_id, [nodes[start_id]["symbol"]], 0)
    return paths


def _route_path_features(paths: list[list[str]]) -> dict[str, Any]:
    return {
        "map_match": True,
        "paths": len(paths),
        "forced_elite_within_3": _all_paths_have_symbol(paths, {"E"}, 3),
        "forced_elite_within_5": _all_paths_have_symbol(paths, {"E"}, 5),
        "forced_combat_within_2": _all_paths_have_symbol(paths, {"M", "E"}, 2),
        "forced_combat_within_4": _all_paths_have_symbol(paths, {"M", "E"}, 4),
        "nearest_rest": _nearest_symbol_depth(paths, {"R"}),
        "nearest_shop": _nearest_symbol_depth(paths, {"$"}),
    }


def _all_paths_have_symbol(paths: list[list[str]], symbols: set[str], max_depth: int) -> bool:
    if not paths:
        return False
    return all(any(symbol in symbols for symbol in path[: max_depth + 1]) for path in paths)


def _nearest_symbol_depth(paths: list[list[str]], symbols: set[str]) -> int | None:
    nearest: int | None = None
    for path in paths:
        for depth, symbol in enumerate(path):
            if symbol in symbols:
                nearest = depth if nearest is None else min(nearest, depth)
                break
    return nearest


def _coord(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _map_observation_status(game: dict[str, Any]) -> str:
    observation = game.get("map_observation")
    if not isinstance(observation, dict):
        return "missing"
    return str(observation.get("status") or "unknown")


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
        "cultist",
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
        "cultist",
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


def _has_act2_emergency_potion(game: dict[str, Any]) -> bool:
    emergency_tokens = {
        "attack",
        "block",
        "dexterity",
        "distilledchaos",
        "duplication",
        "elixir",
        "energy",
        "essenceofsteel",
        "explosive",
        "fairy",
        "fear",
        "fire",
        "fruitjuice",
        "gamblersbrew",
        "heartofiron",
        "liquidmemories",
        "regen",
        "skill",
        "smoke",
        "speed",
        "steroid",
        "strength",
        "swift",
        "weak",
    }
    for potion in game.get("potions", []):
        if potion.get("is_empty"):
            continue
        key = _potion_key(potion)
        if any(token in key for token in emergency_tokens):
            return True
    return False


def _act2_deck_lacks_premium_block(game: dict[str, Any]) -> bool:
    names = _deck_card_names(game)
    if not names:
        return False
    return not any(name in ACT1_BLOCK_STABILIZER_CARDS for name in names)


def _act2_deck_lacks_weak(game: dict[str, Any]) -> bool:
    weak_cards = {"Clothesline", "Disarm", "Intimidate", "Shockwave", "Uppercut"}
    names = _deck_card_names(game)
    if not names:
        return False
    return not any(name in weak_cards for name in names)


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
