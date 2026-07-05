"""Act 1 readiness features for shadow learning and route gates.

The readiness gate is intentionally read-only: it summarizes whether the
current deck, HP, and potions look ready for an Act 1 elite or boss. Policy can
consume these features later, but this module does not issue game actions.
"""

from __future__ import annotations

import re
from typing import Any


VERSION = 3

ATTACK_DAMAGE = {
    "anger": 6,
    "bash": 8,
    "bloodforblood": 14,
    "carnage": 20,
    "clash": 14,
    "cleave": 8,
    "clothesline": 12,
    "headbutt": 9,
    "heavyblade": 14,
    "hemokinesis": 15,
    "immolate": 21,
    "ironwave": 5,
    "perfectedstrike": 10,
    "pommelstrike": 9,
    "pummel": 10,
    "rampage": 8,
    "reaper": 4,
    "recklesscharge": 7,
    "strike": 6,
    "swordboomerang": 9,
    "thunderclap": 4,
    "twinstrike": 10,
    "uppercut": 13,
    "whirlwind": 12,
    "wildstrike": 12,
}

BLOCK_VALUE = {
    "armaments": 5,
    "defend": 5,
    "flamebarrier": 12,
    "ghostlyarmor": 10,
    "impervious": 30,
    "ironwave": 5,
    "metallicize": 3,
    "powerthrough": 15,
    "secondwind": 8,
    "shrugitoff": 8,
    "truegrit": 7,
}

PREMIUM_BLOCK = {
    "armaments",
    "disarm",
    "flamebarrier",
    "ghostlyarmor",
    "impervious",
    "metallicize",
    "powerthrough",
    "secondwind",
    "shockwave",
    "shrugitoff",
    "truegrit",
}
PREMIUM_BLOCK_TAGS = {
    "boss_defense",
    "defense_core",
    "elite_defense",
    "multi_hit_defense",
    "panic_block",
    "premium_block",
    "premium_defense",
}

AOE_CARDS = {"cleave", "immolate", "reaper", "thunderclap", "whirlwind"}
WEAK_CARDS = {"clothesline", "intimidate", "shockwave", "uppercut"}
VULNERABLE_CARDS = {"bash", "shockwave", "thunderclap", "uppercut"}
SCALING_CARDS = {"demonform", "inflame", "limitbreak", "metallicize", "spotweakness"}
RISKY_ENGINE_CARDS = {"bloodletting", "burningpact", "combust", "darkembrace", "offering"}

HIGH_IMPACT_POTION_TOKENS = {
    "attack",
    "block",
    "cultist",
    "dexterity",
    "distilledchaos",
    "duplication",
    "energy",
    "essenceofsteel",
    "explosive",
    "fear",
    "fire",
    "heartofiron",
    "liquidbronze",
    "power",
    "skill",
    "speed",
    "steroid",
    "strength",
    "swift",
    "weak",
}

DEFENSIVE_POTION_TOKENS = {"block", "dexterity", "essenceofsteel", "heartofiron", "speed", "weak"}
OFFENSIVE_POTION_TOKENS = {"attack", "cultist", "explosive", "fear", "fire", "steroid", "strength"}
AOE_POTION_TOKENS = {"explosive"}
WEAK_POTION_TOKENS = {"weak"}
IMMEDIATE_POTION_TOKENS = {
    "attack",
    "block",
    "dexterity",
    "distilledchaos",
    "duplication",
    "energy",
    "essenceofsteel",
    "explosive",
    "fear",
    "fire",
    "heartofiron",
    "liquidbronze",
    "skill",
    "speed",
    "steroid",
    "strength",
    "swift",
    "weak",
}
IMMEDIATE_POTION_ROLES = {
    "aoe",
    "aoe_clear",
    "burst_turn",
    "damage",
    "defense",
    "emergency",
    "frontload",
    "hand_fix",
    "lethal_prevention",
    "sentries",
    "tempo",
}
SCALING_POTION_ROLES = {"boss_damage", "long_fight", "scaling"}


def act1_readiness(state: dict[str, Any], knowledge: Any | None = None) -> dict[str, Any]:
    """Return Act 1 readiness scores, gaps, and risk flags.

    The function accepts either a runner-style wrapper with ``game_state`` or a
    raw game-state dictionary. Optional ``knowledge`` may be an object exposing
    ``card_for`` and ``potion_for`` methods like ``StaticKnowledge``.
    """

    game = _game_state(state)
    deck = _as_list(game.get("deck"))
    potions = [potion for potion in _as_list(game.get("potions")) if not _is_empty_potion(potion)]
    floor = _safe_int(game.get("floor"))
    act = _safe_int(game.get("act"), default=1)
    character = str(game.get("class") or game.get("character") or "IRONCLAD").upper()
    current_hp, max_hp = _hp(game)
    hp_ratio = current_hp / max(max_hp, 1)
    screen_state = game.get("screen_state") if isinstance(game.get("screen_state"), dict) else {}
    next_nodes = _as_list(screen_state.get("next_nodes") or game.get("map_options"))
    boss_available = bool(screen_state.get("boss_available") or game.get("boss_available"))
    elite_available = any(str(node.get("symbol", "")).upper() == "E" for node in next_nodes if isinstance(node, dict))
    monster_available = any(str(node.get("symbol", "")).upper() == "M" for node in next_nodes if isinstance(node, dict))
    forced_elite_within_3 = _forced_elite_context(game, screen_state, "forced_elite_within_3")
    forced_elite_within_5 = _forced_elite_context(game, screen_state, "forced_elite_within_5")
    route_context = _route_context(game, screen_state)
    sentries_threat = _sentries_threat(game)
    elite_threat = elite_available or forced_elite_within_3 or sentries_threat

    deck_features = _deck_features(deck, knowledge)
    potion_features = _potion_features(potions, knowledge)

    hp_score = _clamp_score(hp_ratio * 115)
    output_score = _output_score(deck_features)
    defense_score = _defense_score(deck_features)
    aoe_score = _aoe_score(deck_features, potion_features)
    debuff_score = _debuff_score(deck_features, potion_features)
    potion_score = _potion_score(potion_features)

    elite_score = _clamp_score(
        hp_score * 0.24
        + output_score * 0.28
        + defense_score * 0.18
        + potion_score * 0.20
        + max(aoe_score, debuff_score) * 0.10
    )
    boss_score = _clamp_score(
        hp_score * 0.20
        + output_score * 0.20
        + defense_score * 0.30
        + potion_score * 0.16
        + aoe_score * 0.08
        + debuff_score * 0.06
    )
    overall_score = _clamp_score(
        hp_score * 0.20
        + output_score * 0.22
        + defense_score * 0.24
        + potion_score * 0.16
        + aoe_score * 0.10
        + debuff_score * 0.08
    )

    scores = {
        "hp": round(hp_score, 1),
        "output": round(output_score, 1),
        "defense": round(defense_score, 1),
        "aoe": round(aoe_score, 1),
        "debuff": round(debuff_score, 1),
        "potion": round(potion_score, 1),
        "elite": round(elite_score, 1),
        "boss": round(boss_score, 1),
        "overall": round(overall_score, 1),
    }

    gaps = _gaps(scores, deck_features, potion_features)
    risk_flags = _risk_flags(
        scores,
        act=act,
        hp_ratio=hp_ratio,
        boss_available=boss_available,
        elite_available=elite_available,
        forced_elite_within_3=forced_elite_within_3,
        sentries_threat=sentries_threat,
        monster_available=monster_available,
        route_context=route_context,
        deck_features=deck_features,
        potion_features=potion_features,
    )
    recommendations = _recommendations(
        gaps,
        risk_flags,
        boss_available=boss_available,
        elite_available=elite_available,
    )

    features = {
        "act": act,
        "floor": floor,
        "character": character,
        "current_hp": current_hp,
        "max_hp": max_hp,
        "hp_ratio": round(hp_ratio, 3),
        "boss_available": boss_available,
        "elite_available": elite_available,
        "forced_elite_within_3": forced_elite_within_3,
        "forced_elite_within_5": forced_elite_within_5,
        "sentries_threat": sentries_threat,
        "elite_threat": elite_threat,
        "monster_available": monster_available,
        **route_context,
        "deck_size": deck_features["deck_size"],
        **deck_features,
        **potion_features,
    }

    return {
        "version": VERSION,
        "scope": "act1",
        "scores": scores,
        "gaps": gaps,
        "risk_flags": risk_flags,
        "features": features,
        "recommendations": recommendations,
    }


def _game_state(state: dict[str, Any]) -> dict[str, Any]:
    game = state.get("game_state") if isinstance(state.get("game_state"), dict) else None
    return game if game is not None else state


def _deck_features(deck: list[Any], knowledge: Any | None) -> dict[str, Any]:
    features = {
        "deck_size": len(deck),
        "known_cards": 0,
        "unknown_cards": 0,
        "attack_cards": 0,
        "block_cards": 0,
        "premium_block_cards": 0,
        "aoe_cards": 0,
        "weak_sources": 0,
        "vulnerable_sources": 0,
        "scaling_sources": 0,
        "risky_engine_cards": 0,
        "total_base_damage": 0,
        "total_base_block": 0,
    }
    for item in deck:
        card = _card_row(item, knowledge)
        key = _card_key(item, card)
        tags = _tags(card)
        damage = _card_damage(key, card)
        block = _card_block(key, card)
        known = bool(key or card)
        if known:
            features["known_cards"] += 1
        else:
            features["unknown_cards"] += 1
        features["total_base_damage"] += damage
        features["total_base_block"] += block
        if damage > 0 or "attack" in tags or key in ATTACK_DAMAGE:
            features["attack_cards"] += 1
        if block > 0 or "block" in tags or key in BLOCK_VALUE:
            features["block_cards"] += 1
        if key in PREMIUM_BLOCK or tags.intersection(PREMIUM_BLOCK_TAGS):
            features["premium_block_cards"] += 1
        if key in AOE_CARDS or tags.intersection({"aoe", "multi_enemy"}):
            features["aoe_cards"] += 1
        if key in WEAK_CARDS or "weak" in tags:
            features["weak_sources"] += 1
        if key in VULNERABLE_CARDS or "vulnerable" in tags:
            features["vulnerable_sources"] += 1
        if key in SCALING_CARDS or tags.intersection({"scaling", "strength", "power"}):
            features["scaling_sources"] += 1
        if key in RISKY_ENGINE_CARDS or tags.intersection({"self_damage", "risky_engine", "slow_engine"}):
            features["risky_engine_cards"] += 1
    return features


def _potion_features(potions: list[Any], knowledge: Any | None) -> dict[str, Any]:
    features = {
        "potion_count": len(potions),
        "high_impact_potions": 0,
        "defensive_potions": 0,
        "offensive_potions": 0,
        "potion_damage_value": 0,
        "potion_block_value": 0,
        "potion_energy_value": 0,
        "aoe_potions": 0,
        "weak_potions": 0,
        "immediate_tempo_potions": 0,
        "scaling_potions": 0,
    }
    for item in potions:
        row = _potion_row(item, knowledge)
        key = _potion_key(item, row)
        roles = _roles(row)
        values = row.get("values") if isinstance(row, dict) and isinstance(row.get("values"), dict) else {}
        damage_value = _safe_int(values.get("damage"))
        block_value = _safe_int(values.get("block"))
        energy_value = _safe_int(values.get("energy"))
        features["potion_damage_value"] += damage_value
        features["potion_block_value"] += block_value
        features["potion_energy_value"] += energy_value
        if _has_token(key, HIGH_IMPACT_POTION_TOKENS) or roles.intersection({"elite_tempo", "boss_tempo", "lethal_save"}):
            features["high_impact_potions"] += 1
        if _has_token(key, DEFENSIVE_POTION_TOKENS) or roles.intersection({"block", "defense", "lethal_save"}):
            features["defensive_potions"] += 1
        if _has_token(key, OFFENSIVE_POTION_TOKENS) or roles.intersection({"damage", "aoe_clear", "elite_tempo"}):
            features["offensive_potions"] += 1
        if _has_token(key, AOE_POTION_TOKENS) or roles.intersection({"aoe", "aoe_clear", "sentries"}):
            features["aoe_potions"] += 1
        if _has_token(key, WEAK_POTION_TOKENS) or "weak" in roles:
            features["weak_potions"] += 1
        if _has_token(key, IMMEDIATE_POTION_TOKENS) or roles.intersection(IMMEDIATE_POTION_ROLES):
            features["immediate_tempo_potions"] += 1
        if roles.intersection(SCALING_POTION_ROLES):
            features["scaling_potions"] += 1
    return features


def _output_score(features: dict[str, Any]) -> float:
    deck_size = max(1, int(features["deck_size"]))
    density = features["total_base_damage"] / deck_size
    return _clamp_score(density * 8.0 + features["attack_cards"] * 3.0 + features["vulnerable_sources"] * 8.0)


def _defense_score(features: dict[str, Any]) -> float:
    deck_size = max(1, int(features["deck_size"]))
    density = features["total_base_block"] / deck_size
    return _clamp_score(
        density * 10.0
        + features["block_cards"] * 3.0
        + features["premium_block_cards"] * 12.0
        + features["weak_sources"] * 5.0
    )


def _aoe_score(deck_features: dict[str, Any], potion_features: dict[str, Any]) -> float:
    return _clamp_score(deck_features["aoe_cards"] * 30.0 + min(2, potion_features["offensive_potions"]) * 12.0)


def _debuff_score(deck_features: dict[str, Any], potion_features: dict[str, Any]) -> float:
    return _clamp_score(
        deck_features["weak_sources"] * 22.0
        + deck_features["vulnerable_sources"] * 12.0
        + min(2, potion_features["high_impact_potions"]) * 5.0
    )


def _potion_score(features: dict[str, Any]) -> float:
    return _clamp_score(
        features["high_impact_potions"] * 24.0
        + features["defensive_potions"] * 8.0
        + features["offensive_potions"] * 8.0
        + min(20, features["potion_damage_value"] * 0.5)
        + min(20, features["potion_block_value"] * 0.8)
        + min(15, features["potion_energy_value"] * 5.0)
    )


def _gaps(scores: dict[str, float], deck_features: dict[str, Any], potion_features: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if scores["hp"] < 55:
        gaps.append("low_hp")
    if scores["output"] < 50:
        gaps.append("frontload_damage_low")
    if scores["defense"] < 50:
        gaps.append("defense_density_low")
    if deck_features["premium_block_cards"] <= 0:
        gaps.append("premium_block_missing")
    if scores["aoe"] < 25:
        gaps.append("aoe_missing")
    if deck_features["weak_sources"] <= 0:
        gaps.append("weak_missing")
    if deck_features["vulnerable_sources"] <= 0:
        gaps.append("vulnerable_missing")
    if potion_features["high_impact_potions"] <= 0:
        gaps.append("elite_potion_missing")
    if deck_features["risky_engine_cards"] >= 2:
        gaps.append("risky_engine_density")
    return gaps


def _risk_flags(
    scores: dict[str, float],
    *,
    act: int,
    hp_ratio: float,
    boss_available: bool,
    elite_available: bool,
    forced_elite_within_3: bool,
    sentries_threat: bool,
    monster_available: bool,
    route_context: dict[str, Any],
    deck_features: dict[str, Any],
    potion_features: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    elite_context = elite_available or forced_elite_within_3 or sentries_threat
    if act != 1:
        flags.append("non_act1_context")
    if hp_ratio < 0.35:
        flags.append("critical_hp")
    elif hp_ratio < 0.55:
        flags.append("low_hp")
    if elite_context and scores["elite"] < 55:
        flags.append("elite_not_ready")
    if boss_available and scores["boss"] < 60:
        flags.append("boss_not_ready")
    if elite_context and hp_ratio < 0.65 and potion_features["high_impact_potions"] <= 0:
        flags.append("elite_low_hp_no_tempo_potion")
    if forced_elite_within_3 and scores["aoe"] < 25:
        flags.append("forced_elite_aoe_gap")
    if forced_elite_within_3 and deck_features["weak_sources"] <= 0 and potion_features["weak_potions"] <= 0:
        flags.append("forced_elite_weak_gap")
    if forced_elite_within_3 and potion_features["high_impact_potions"] <= 0:
        flags.append("forced_elite_no_tempo_potion")
    if sentries_threat and scores["aoe"] < 25:
        flags.append("sentries_no_aoe")
    if sentries_threat and deck_features["weak_sources"] <= 0 and potion_features["weak_potions"] <= 0:
        flags.append("sentries_no_weak")
    if sentries_threat and potion_features["high_impact_potions"] <= 0:
        flags.append("sentries_no_tempo_potion")
    if _act1_hallway_low_buffer_no_recovery(
        act=act,
        hp_ratio=hp_ratio,
        monster_available=monster_available,
        route_context=route_context,
    ):
        flags.append("act1_low_buffer_no_recovery")
        if potion_features["immediate_tempo_potions"] <= 0:
            flags.append("hallway_no_immediate_tempo_potion")
        if deck_features["premium_block_cards"] <= 0:
            flags.append("hallway_lacks_premium_block")
    if boss_available and deck_features["premium_block_cards"] <= 0:
        flags.append("boss_lacks_premium_block")
    if boss_available and potion_features["high_impact_potions"] <= 0:
        flags.append("boss_no_tempo_potion")
    if monster_available and hp_ratio < 0.35 and scores["defense"] < 55:
        flags.append("hallway_low_hp_defense_risk")
    if deck_features["risky_engine_cards"] >= 2 and hp_ratio < 0.65:
        flags.append("self_damage_engine_risk")
    return flags


def _recommendations(gaps: list[str], flags: list[str], *, boss_available: bool, elite_available: bool) -> list[str]:
    recommendations: list[str] = []
    if "critical_hp" in flags:
        recommendations.append("avoid_combat_until_rest_shop_or_event")
    elif "low_hp" in flags or "low_hp" in gaps:
        recommendations.append("prefer_rest_shop_or_safe_event")
    if "defense_density_low" in gaps or "premium_block_missing" in gaps:
        recommendations.append("prioritize_premium_block")
    if "frontload_damage_low" in gaps:
        recommendations.append("prioritize_frontload_damage")
    if "aoe_missing" in gaps:
        recommendations.append("prioritize_aoe_before_sentries_or_slime_boss")
    if "forced_elite_aoe_gap" in flags or "sentries_no_aoe" in flags:
        recommendations.append("prioritize_aoe_before_forced_elite")
    if "forced_elite_weak_gap" in flags or "sentries_no_weak" in flags:
        recommendations.append("prioritize_weak_or_strength_down_before_elite")
    if "elite_potion_missing" in gaps and elite_available:
        recommendations.append("avoid_elite_without_tempo_potion")
    if "forced_elite_no_tempo_potion" in flags or "sentries_no_tempo_potion" in flags:
        recommendations.append("seek_or_save_elite_tempo_potion")
    if "act1_low_buffer_no_recovery" in flags:
        recommendations.append("prefer_rest_shop_or_safe_event")
    if "hallway_no_immediate_tempo_potion" in flags:
        recommendations.append("seek_or_save_hallway_tempo_potion")
    if boss_available and ("boss_not_ready" in flags or "boss_no_tempo_potion" in flags):
        recommendations.append("prefer_rest_or_buy_potion_before_boss_if_possible")
    return _dedupe(recommendations)


def _act1_hallway_low_buffer_no_recovery(
    *,
    act: int,
    hp_ratio: float,
    monster_available: bool,
    route_context: dict[str, Any],
) -> bool:
    if act != 1 or hp_ratio >= 0.62:
        return False
    combat_pressure = monster_available or bool(route_context.get("forced_combat_within_2"))
    if not combat_pressure:
        return False
    nearest_rest = route_context.get("nearest_rest")
    nearest_shop = route_context.get("nearest_shop")
    rest_near = nearest_rest is not None and nearest_rest <= 2
    shop_near = nearest_shop is not None and nearest_shop <= 1
    return not rest_near and not shop_near


def _route_context(game: dict[str, Any], screen_state: dict[str, Any]) -> dict[str, Any]:
    direct = {
        "forced_combat_within_2": bool(game.get("forced_combat_within_2") or screen_state.get("forced_combat_within_2")),
        "forced_combat_within_4": bool(game.get("forced_combat_within_4") or screen_state.get("forced_combat_within_4")),
        "nearest_rest": _optional_int(game.get("nearest_rest", screen_state.get("nearest_rest"))),
        "nearest_shop": _optional_int(game.get("nearest_shop", screen_state.get("nearest_shop"))),
    }
    route = game.get("route_evaluation") if isinstance(game.get("route_evaluation"), dict) else {}
    options = [option for option in _as_list(route.get("options")) if isinstance(option, dict)]
    if not options:
        return direct
    best = max(options, key=lambda option: _optional_float(option.get("score")) or float("-inf"))
    lookahead = best.get("lookahead") if isinstance(best.get("lookahead"), dict) else {}
    return {
        "forced_combat_within_2": bool(direct["forced_combat_within_2"] or lookahead.get("forced_combat_within_2")),
        "forced_combat_within_4": bool(direct["forced_combat_within_4"] or lookahead.get("forced_combat_within_4")),
        "nearest_rest": _first_optional_int(direct["nearest_rest"], lookahead.get("nearest_rest")),
        "nearest_shop": _first_optional_int(direct["nearest_shop"], lookahead.get("nearest_shop")),
    }


def _forced_elite_context(game: dict[str, Any], screen_state: dict[str, Any], key: str) -> bool:
    if game.get(key) or screen_state.get(key):
        return True
    route = game.get("route_evaluation") if isinstance(game.get("route_evaluation"), dict) else {}
    options = [option for option in _as_list(route.get("options")) if isinstance(option, dict)]
    forced_options: list[tuple[bool, float | None]] = []
    for option in options:
        lookahead = option.get("lookahead") if isinstance(option.get("lookahead"), dict) else {}
        if key not in lookahead:
            continue
        forced_options.append((bool(lookahead.get(key)), _optional_float(option.get("score"))))
    if not forced_options:
        return False
    if all(forced for forced, _ in forced_options):
        return True
    scored = [(forced, score) for forced, score in forced_options if score is not None]
    if not scored:
        return len(forced_options) == 1 and forced_options[0][0]
    best_score = max(score for _, score in scored if score is not None)
    best_options = [forced for forced, score in scored if score == best_score]
    return bool(best_options) and all(best_options)


def _sentries_threat(game: dict[str, Any]) -> bool:
    ids = [str(item) for item in _as_list(game.get("enemy_ids"))]
    for combat_key in ("combat_state", "combat"):
        combat = game.get(combat_key) if isinstance(game.get(combat_key), dict) else {}
        for monster in _as_list(combat.get("monsters")):
            if isinstance(monster, dict):
                ids.append(str(monster.get("id") or monster.get("name") or ""))
            else:
                ids.append(str(monster))
    if any("sentry" in _norm(item) for item in ids):
        return True
    return (
        _safe_int(game.get("enemy_elite_count")) >= 2
        and _safe_int(game.get("enemy_tag_aoe_high_value")) >= 2
        and _safe_int(game.get("enemy_tag_status_pressure")) >= 2
    )


def _card_row(card: Any, knowledge: Any | None) -> dict[str, Any] | None:
    if knowledge is None or not hasattr(knowledge, "card_for"):
        return None
    try:
        row = knowledge.card_for(card)
    except Exception:
        return None
    return row if isinstance(row, dict) else None


def _potion_row(potion: Any, knowledge: Any | None) -> dict[str, Any] | None:
    if knowledge is None or not hasattr(knowledge, "potion_for"):
        return None
    try:
        row = knowledge.potion_for(potion)
    except Exception:
        return None
    return row if isinstance(row, dict) else None


def _card_key(card: Any, row: dict[str, Any] | None = None) -> str:
    if row is not None:
        key = _norm(row.get("id") or row.get("name"))
        if key:
            return _strip_suffix(key)
    if isinstance(card, dict):
        return _strip_suffix(_norm(card.get("id") or card.get("name")))
    return _strip_suffix(_norm(card))


def _potion_key(potion: Any, row: dict[str, Any] | None = None) -> str:
    if row is not None:
        key = _norm(row.get("id") or row.get("name"))
        if key:
            return key
    if isinstance(potion, dict):
        return _norm(potion.get("id") or potion.get("name"))
    return _norm(potion)


def _card_damage(key: str, row: dict[str, Any] | None) -> int:
    values = row.get("values") if isinstance(row, dict) and isinstance(row.get("values"), dict) else {}
    return max(ATTACK_DAMAGE.get(key, 0), _safe_int(values.get("base_damage")))


def _card_block(key: str, row: dict[str, Any] | None) -> int:
    values = row.get("values") if isinstance(row, dict) and isinstance(row.get("values"), dict) else {}
    return max(BLOCK_VALUE.get(key, 0), _safe_int(values.get("base_block")))


def _tags(row: dict[str, Any] | None) -> set[str]:
    return _string_set(row.get("tags") if isinstance(row, dict) else None)


def _roles(row: dict[str, Any] | None) -> set[str]:
    return _string_set(row.get("roles") if isinstance(row, dict) else None)


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item).strip().lower().replace(" ", "_") for item in value if str(item).strip()}


def _hp(game: dict[str, Any]) -> tuple[int, int]:
    combat_player = game.get("combat_state", {}).get("player", {}) if isinstance(game.get("combat_state"), dict) else {}
    current = _safe_int(combat_player.get("current_hp"), default=_safe_int(game.get("current_hp")))
    maximum = _safe_int(combat_player.get("max_hp"), default=_safe_int(game.get("max_hp"), default=max(current, 1)))
    return current, max(maximum, 1)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _is_empty_potion(potion: Any) -> bool:
    return isinstance(potion, dict) and bool(potion.get("is_empty"))


def _has_token(key: str, tokens: set[str]) -> bool:
    return any(token in key for token in tokens)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_optional_int(*values: Any) -> int | None:
    for value in values:
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    return None


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _strip_suffix(key: str) -> str:
    for suffix in ("r", "g", "b", "p"):
        if key.endswith(suffix) and len(key) > 1:
            base = key[: -len(suffix)]
            if base in {"strike", "defend"}:
                return base
    return key


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
