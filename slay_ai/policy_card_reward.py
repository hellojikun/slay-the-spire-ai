"""Card reward policy and Act 1 deck-gap scoring."""

from __future__ import annotations

from typing import Any

from .memory import StrategyMemory, normalize_card_name
from .policy_decision import Decision


EXHAUST_PAYOFF_CARDS = {"Dark Embrace", "Feel No Pain"}
EXHAUST_ENABLER_CARDS = {
    "Burning Pact",
    "Disarm",
    "Fiend Fire",
    "Ghostly Armor",
    "Impervious",
    "Intimidate",
    "Offering",
    "Second Wind",
    "Sentinel",
    "Sever Soul",
    "Shockwave",
    "True Grit",
}
EXHAUST_PAYOFF_UNSUPPORTED_PENALTY = 24.0
EARLY_UNSUPPORTED_EXHAUST_PAYOFF_PENALTY = 18.0
EARLY_UNSUPPORTED_ENGINE_PENALTY = 24.0
EARLY_DUPLICATE_EXHAUST_ENABLER_PENALTY = 16.0
ACT1_LOW_HP_SURVIVAL_CARD_BONUS = 14.0
ACT1_AOE_GAP_CARD_BONUS = 28.0
ACT1_BOSS_PREP_DAMAGE_BONUS = 12.0
ACT1_BOSS_PREP_DEFENSE_BONUS = 8.0
ACT1_BOSS_PREP_SLOW_ENGINE_PENALTY = 14.0
ACT1_BOSS_PREP_SELF_DAMAGE_ENGINE_PENALTY = 24.0
TEMPORARY_COMBAT_SLOW_POWER_PENALTY = 44.0
TEMPORARY_COMBAT_LETHAL_POWER_PENALTY = 24.0
TEMPORARY_COMBAT_DEFENSIVE_POWER_BONUS = 18.0
MODEL_TIEBREAKER_HEURISTIC_MARGIN = 6.0
MODEL_TIEBREAKER_DELTA_MARGIN = 4.0
MODEL_TIEBREAKER_MIN_SIGNAL = 0.0
SLOW_ENGINE_CARDS = {"Burning Pact", "Dark Embrace", "Havoc"}
SELF_DAMAGE_SLOW_ENGINE_CARDS = {"Brutality", "Combust"}
SLOW_COMBAT_POWER_CARDS = {"Barricade", "Corruption", "Dark Embrace", "Demon Form", "Inflame"}
DEFENSIVE_COMBAT_POWER_CARDS = {"Metallicize"}
AOE_ATTACK_CARDS = {"Cleave", "Immolate", "Reaper", "Thunderclap", "Whirlwind"}
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
ACT1_LOW_HP_SURVIVAL_CARDS = ACT1_BLOCK_STABILIZER_CARDS | {"Iron Wave"}
ACT1_BOSS_PREP_DAMAGE_CARDS = {
    "Carnage",
    "Clothesline",
    "Dropkick",
    "Hemokinesis",
    "Inflame",
    "Perfected Strike",
    "Pommel Strike",
    "Pummel",
    "Rampage",
    "Shockwave",
    "Spot Weakness",
    "Thunderclap",
    "Twin Strike",
    "Uppercut",
    "Wild Strike",
}
ACT1_BOSS_PREP_DEFENSE_CARDS = ACT1_BLOCK_STABILIZER_CARDS | {"Clothesline", "Disarm", "Intimidate", "Shockwave", "Uppercut"}
BLOCK_CARDS = ACT1_BLOCK_STABILIZER_CARDS | {"Defend", "Iron Wave"}
IRONCLAD_ATTACK_CARDS = {
    "Anger",
    "Bash",
    "Cleave",
    "Clothesline",
    "Hemokinesis",
    "Perfected Strike",
    "Pommel Strike",
    "Strike",
    "Thunderclap",
    "Twin Strike",
    "Whirlwind",
}
ATTACK_DUPLICATE_SOFT_CAPS = {
    "Anger": 1,
    "Cleave": 1,
    "Clothesline": 2,
    "Hemokinesis": 1,
    "Thunderclap": 1,
}


class CardRewardPolicy:
    def __init__(self, memory: StrategyMemory, character: str = "IRONCLAD", model_authority: str = "shadow") -> None:
        self.memory = memory
        self.character = character
        self.model_authority = _normalize_model_authority(model_authority)

    def decide_card_reward(self, game: dict[str, Any]) -> Decision:
        cards = game.get("screen_state", {}).get("cards", [])
        if not cards:
            return Decision([{"action": "skip"}], "No card reward cards visible.")
        temporary_combat_choice = _is_temporary_combat_card_choice(game)
        ranked = []
        for index, card in enumerate(cards, start=1):
            score = self.score_card_reward(card, game)
            breakdown = self._card_score_breakdown(_card_key(card))
            ranked.append((score, index, card, breakdown))
        score, index, card, breakdown = max(ranked, key=lambda item: item[0])
        selection_source = "heuristic_with_memory_model_score"
        model_choice = self._model_tiebreaker_choice(ranked, score, index, game)
        if model_choice is not None:
            score, index, card, breakdown = model_choice
            selection_source = "model_authority_tiebreaker"
        if score < 28 and game.get("floor", 0) > 8:
            return Decision(
                [{"action": "skip"}],
                f"Skip low-impact card reward; best was {card.get('name')} ({score:.1f}).",
                metadata={"model_authority": self._model_authority_metadata(ranked, None, selection_source)},
            )
        name = card.get("id") or card.get("name", "")
        display_name = card.get("name", name)
        learn_pick = None if temporary_combat_choice else name
        reason_prefix = "Choose temporary" if temporary_combat_choice else "Pick"
        return Decision(
            [{"action": "choose", "choice_index": index}],
            f"{reason_prefix} {display_name}; reward score {score:.1f}.",
            learn_card_pick=learn_pick,
            metadata={"model_authority": self._model_authority_metadata(ranked, index, selection_source)},
        )

    def _card_score_breakdown(self, name: str) -> dict[str, Any]:
        if hasattr(self.memory, "card_score_breakdown"):
            return self.memory.card_score_breakdown(name, self.character)
        score = float(self.memory.card_score(name, self.character))
        return {
            "card": normalize_card_name(name),
            "base_score": score,
            "learned_delta": 0.0,
            "model_delta": 0.0,
            "total": score,
        }

    def _model_tiebreaker_choice(
        self,
        ranked: list[tuple[float, int, dict[str, Any], dict[str, Any]]],
        heuristic_score: float,
        heuristic_index: int,
        game: dict[str, Any],
    ) -> tuple[float, int, dict[str, Any], dict[str, Any]] | None:
        if self.model_authority not in {"assist", "pilot"}:
            return None
        if not ranked:
            return None
        heuristic_choice = next((item for item in ranked if item[1] == heuristic_index), None)
        if heuristic_choice is None:
            return None
        eligible = [
            item
            for item in ranked
            if item[1] != heuristic_index
            and heuristic_score - item[0] <= MODEL_TIEBREAKER_HEURISTIC_MARGIN
            and item[0] >= 28
            and _model_authority_card_allowed(_card_key(item[2]), game)
        ]
        if not eligible:
            return None
        model_choice = max(eligible, key=lambda item: (self._model_signal(item[3]), item[0]))
        if model_choice[1] == heuristic_index:
            return None
        if self._model_signal(model_choice[3]) <= MODEL_TIEBREAKER_MIN_SIGNAL:
            return None
        signal_gap = self._model_signal(model_choice[3]) - self._model_signal(heuristic_choice[3])
        if signal_gap < MODEL_TIEBREAKER_DELTA_MARGIN:
            return None
        return model_choice

    def _model_authority_metadata(
        self,
        ranked: list[tuple[float, int, dict[str, Any], dict[str, Any]]],
        selected_index: int | None,
        selection_source: str,
    ) -> dict[str, Any]:
        return {
            "surface": "card_reward",
            "level": self.model_authority,
            "selection_source": selection_source,
            "selected_choice_index": selected_index,
            "runtime_authority": selection_source == "model_authority_tiebreaker",
            "options": [
                {
                    "choice_index": index,
                    "card": breakdown.get("card") or _card_key(card),
                    "name": card.get("name") or card.get("id") or "",
                    "heuristic_total_score": float(score),
                    "base_score": float(breakdown.get("base_score", 0.0)),
                    "learned_delta": float(breakdown.get("learned_delta", 0.0)),
                    "model_delta": float(breakdown.get("model_delta", 0.0)),
                    "memory_model_total": float(breakdown.get("total", 0.0)),
                    "model_signal": self._model_signal(breakdown),
                }
                for score, index, card, breakdown in ranked
            ],
        }

    @staticmethod
    def _model_signal(breakdown: dict[str, Any]) -> float:
        return float(breakdown.get("learned_delta", 0.0)) + float(breakdown.get("model_delta", 0.0))

    def score_card_reward(self, card: dict[str, Any], game: dict[str, Any]) -> float:
        name = _card_key(card)
        score = self.memory.card_score(name, self.character)
        if _is_temporary_combat_card_choice(game):
            score += _temporary_combat_card_adjustment(card, game)
        if self.character == "IRONCLAD" and name in EXHAUST_PAYOFF_CARDS and not _deck_has_exhaust_enabler(game):
            score -= EXHAUST_PAYOFF_UNSUPPORTED_PENALTY
            if _is_early_act1(game):
                score -= EARLY_UNSUPPORTED_EXHAUST_PAYOFF_PENALTY
        if (
            self.character == "IRONCLAD"
            and name in SLOW_ENGINE_CARDS
            and _is_early_act1(game)
            and not _deck_has_exhaust_payoff(game)
        ):
            score -= EARLY_UNSUPPORTED_ENGINE_PENALTY
        if (
            self.character == "IRONCLAD"
            and name in EXHAUST_ENABLER_CARDS
            and _is_early_act1(game)
            and not _deck_has_exhaust_payoff(game)
        ):
            enabler_count = _deck_exhaust_enabler_count(game)
            if enabler_count >= 1:
                score -= EARLY_DUPLICATE_EXHAUST_ENABLER_PENALTY
            if enabler_count >= 2:
                score -= EARLY_DUPLICATE_EXHAUST_ENABLER_PENALTY
        if (
            self.character == "IRONCLAD"
            and _is_early_act1(game)
            and _hp_ratio(game) < 0.55
            and name in ACT1_LOW_HP_SURVIVAL_CARDS
        ):
            score += ACT1_LOW_HP_SURVIVAL_CARD_BONUS
        if self.character == "IRONCLAD" and _act1_deck_needs_aoe(game) and name in AOE_ATTACK_CARDS:
            score += ACT1_AOE_GAP_CARD_BONUS
        if self.character == "IRONCLAD" and _act1_deck_needs_block_stabilizer(game):
            if name in ACT1_BLOCK_STABILIZER_CARDS:
                score += 16
            if name in ATTACK_DUPLICATE_SOFT_CAPS and _deck_is_attack_heavy(game):
                copies = _deck_card_count(game, name)
                soft_cap = ATTACK_DUPLICATE_SOFT_CAPS[name]
                if copies >= soft_cap:
                    score -= 12 + max(0, copies - soft_cap) * 4
        if self.character == "IRONCLAD" and _act1_boss_prep_needed(game):
            if name in ACT1_BOSS_PREP_DAMAGE_CARDS:
                score += ACT1_BOSS_PREP_DAMAGE_BONUS
            if name in ACT1_BOSS_PREP_DEFENSE_CARDS:
                score += ACT1_BOSS_PREP_DEFENSE_BONUS
            if name in SLOW_ENGINE_CARDS and not _deck_has_exhaust_enabler(game):
                score -= ACT1_BOSS_PREP_SLOW_ENGINE_PENALTY
            if name in SELF_DAMAGE_SLOW_ENGINE_CARDS:
                score -= ACT1_BOSS_PREP_SELF_DAMAGE_ENGINE_PENALTY
        return score

    def act1_boss_prep_needs_potion(self, game: dict[str, Any]) -> bool:
        return _act1_boss_prep_needs_potion(game)


def _card_key(card: dict[str, Any]) -> str:
    return normalize_card_name(str(card.get("id") or card.get("name") or ""))


def _normalize_model_authority(value: str | None) -> str:
    normalized = str(value or "shadow").lower()
    if normalized in {"shadow", "assist", "pilot"}:
        return normalized
    return "shadow"


def _model_authority_card_allowed(name: str, game: dict[str, Any]) -> bool:
    if name in SELF_DAMAGE_SLOW_ENGINE_CARDS and _act1_boss_prep_needed(game):
        return False
    return True


def _deck_card_names(game: dict[str, Any]) -> list[str]:
    names = []
    for card in game.get("deck", []):
        if isinstance(card, dict):
            names.append(_card_key(card))
        else:
            names.append(normalize_card_name(str(card)))
    return names


def _deck_card_count(game: dict[str, Any], name: str) -> int:
    return _deck_card_names(game).count(normalize_card_name(name))


def _act1_deck_needs_block_stabilizer(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    floor = int(game.get("floor", 0) or 0)
    if floor < 6 or floor > 15:
        return False
    names = _deck_card_names(game)
    if not names:
        return False
    premium_block = sum(1 for name in names if name in ACT1_BLOCK_STABILIZER_CARDS)
    total_block = sum(1 for name in names if name in BLOCK_CARDS)
    if premium_block == 0:
        return True
    return floor >= 10 and total_block < max(5, int(len(names) * 0.30))


def _act1_deck_needs_aoe(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    floor = int(game.get("floor", 0) or 0)
    if floor < 5 or floor > 15:
        return False
    names = _deck_card_names(game)
    if not names:
        return False
    return not any(name in AOE_ATTACK_CARDS for name in names)


def _act1_boss_prep_needed(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    floor = int(game.get("floor", 0) or 0)
    if floor < 7 or floor > 15:
        return False
    return (
        _act1_deck_lacks_boss_output(game)
        or _act1_deck_needs_block_stabilizer(game)
        or not _has_usable_potion(game)
    )


def _act1_boss_prep_needs_potion(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    floor = int(game.get("floor", 0) or 0)
    if floor < 5 or floor > 15:
        return False
    if _has_usable_potion(game):
        return False
    return _act1_deck_lacks_boss_output(game) or _act1_deck_needs_block_stabilizer(game)


def _act1_deck_lacks_boss_output(game: dict[str, Any]) -> bool:
    names = _deck_card_names(game)
    if not names:
        return False
    boss_cards = sum(1 for name in names if name in ACT1_BOSS_PREP_DAMAGE_CARDS and name not in {"Bash", "Strike"})
    weak_or_strength_down = sum(1 for name in names if name in {"Clothesline", "Disarm", "Intimidate", "Shockwave", "Uppercut"})
    return boss_cards < 2 and weak_or_strength_down <= 0


def _deck_is_attack_heavy(game: dict[str, Any]) -> bool:
    names = _deck_card_names(game)
    attacks = sum(1 for name in names if name in IRONCLAD_ATTACK_CARDS)
    blocks = sum(1 for name in names if name in BLOCK_CARDS)
    return attacks >= blocks + 4


def _is_early_act1(game: dict[str, Any]) -> bool:
    return int(game.get("act", 1) or 1) == 1 and int(game.get("floor", 0) or 0) <= 6


def _deck_has_exhaust_enabler(game: dict[str, Any]) -> bool:
    for name in _deck_card_names(game):
        if name in EXHAUST_ENABLER_CARDS:
            return True
    return False


def _deck_exhaust_enabler_count(game: dict[str, Any]) -> int:
    return sum(1 for name in _deck_card_names(game) if name in EXHAUST_ENABLER_CARDS)


def _deck_has_exhaust_payoff(game: dict[str, Any]) -> bool:
    for name in _deck_card_names(game):
        if name in EXHAUST_PAYOFF_CARDS:
            return True
    return False


def _has_usable_potion(game: dict[str, Any]) -> bool:
    return any(not potion.get("is_empty") for potion in game.get("potions", []))


def _hp_ratio(obj: dict[str, Any]) -> float:
    current = float(obj.get("current_hp", 1) or 1)
    max_hp = float(obj.get("max_hp", current) or current)
    return current / max(max_hp, 1.0)


def _is_temporary_combat_card_choice(game: dict[str, Any]) -> bool:
    if game.get("screen_type") != "CARD_REWARD":
        return False
    if game.get("room_phase") != "COMBAT":
        return False
    combat = game.get("combat") or game.get("combat_state")
    return isinstance(combat, dict) and bool(combat.get("monsters"))


def _temporary_combat_card_adjustment(card: dict[str, Any], game: dict[str, Any]) -> float:
    combat = game.get("combat") or game.get("combat_state") or {}
    player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
    incoming = _safe_int(combat.get("incoming_damage"))
    if incoming <= 0:
        monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
        incoming = sum(_safe_int((monster.get("move") or {}).get("damage")) for monster in monsters if isinstance(monster, dict))
    block = _safe_int(player.get("block"))
    current_hp = _safe_int(player.get("current_hp") or game.get("current_hp"))
    pressure = max(0, incoming - block)
    hp_ratio = _hp_ratio({"current_hp": current_hp or game.get("current_hp"), "max_hp": player.get("max_hp") or game.get("max_hp")})
    if pressure <= 0:
        return 0.0

    name = _card_key(card)
    ctype = str(card.get("type") or "").upper()
    adjustment = 0.0
    lethal_pressure = current_hp > 0 and pressure >= current_hp
    high_pressure = lethal_pressure or hp_ratio < 0.40 or pressure >= max(14, int(max(current_hp, 1) * 0.35))
    if ctype == "POWER" and name in SLOW_COMBAT_POWER_CARDS and high_pressure:
        adjustment -= TEMPORARY_COMBAT_SLOW_POWER_PENALTY
        if lethal_pressure:
            adjustment -= TEMPORARY_COMBAT_LETHAL_POWER_PENALTY
    if name in DEFENSIVE_COMBAT_POWER_CARDS and high_pressure:
        adjustment += TEMPORARY_COMBAT_DEFENSIVE_POWER_BONUS
    return adjustment


def _safe_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
