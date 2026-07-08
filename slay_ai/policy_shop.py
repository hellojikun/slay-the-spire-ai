"""Shop and relic reward policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .memory import StrategyMemory
from .memory import normalize_card_name
from .policy_card_reward import SLOW_ENGINE_CARDS
from .policy_decision import Decision


CardScoreFn = Callable[[dict[str, Any], dict[str, Any]], float]
GamePredicate = Callable[[dict[str, Any]], bool]


SHOP_BUY_CARD_THRESHOLD = 54.0
SHOP_PURGE_STRIKE_SCORE = 58.0
SHOP_HIGH_IMPACT_POTION_SCORE = 50.0
SHOP_BOSS_PREP_POTION_BONUS = 12.0
SHOP_ACT2_LOW_HP_SLOW_POWER_PENALTY = 22.0
SHOP_ACT2_LOW_HP_SLOW_POWER_NAMES = {"Barricade", "Dark Embrace", "Demon Form"}
SHOP_HIGH_IMPACT_POTION_TOKENS = {
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
    "gambler",
    "gamblersbrew",
    "heartofiron",
    "power",
    "regen",
    "speed",
    "strength",
    "swift",
    "weak",
}


class ShopRelicPolicy:
    def __init__(
        self,
        memory: StrategyMemory,
        *,
        card_reward_score: CardScoreFn,
        should_purge_strike: GamePredicate,
        boss_prep_needs_potion: GamePredicate,
        has_empty_potion_slot: GamePredicate,
    ) -> None:
        self.memory = memory
        self.card_reward_score = card_reward_score
        self.should_purge_strike = should_purge_strike
        self.boss_prep_needs_potion = boss_prep_needs_potion
        self.has_empty_potion_slot = has_empty_potion_slot

    def decide_shop_screen(self, game: dict[str, Any]) -> Decision:
        screen_state = game.get("screen_state", {})
        gold = int(game.get("gold", 0) or 0)
        if (
            not screen_state.get("cards")
            and not screen_state.get("relics")
            and not screen_state.get("potions")
            and screen_state.get("purge_available") is None
        ):
            return Decision([{"action": "wait", "ms": 250}], "Shop inventory is still loading.")
        candidates: list[tuple[float, int, str]] = []
        choice_index = 1

        purge_cost = int(screen_state.get("purge_cost", 0) or 0)
        if screen_state.get("purge_available") and purge_cost <= gold and self.should_purge_strike(game):
            candidates.append((SHOP_PURGE_STRIKE_SCORE - purge_cost * 0.05, choice_index, f"purge Strike for {purge_cost} gold"))
        if screen_state.get("purge_available") and purge_cost <= gold:
            choice_index += 1

        for card in screen_state.get("cards", []):
            price = _shop_price(card)
            if price > gold:
                continue
            score = self.card_reward_score(card, game) - price * 0.08
            score -= _act2_low_hp_slow_power_shop_penalty(card, game)
            if score >= SHOP_BUY_CARD_THRESHOLD:
                candidates.append((score, choice_index, f"buy {card.get('name', card.get('id'))} for {price} gold"))
            choice_index += 1

        for relic in screen_state.get("relics", []):
            price = _shop_price(relic)
            if price <= gold:
                choice_index += 1

        has_empty_potion_slot = self.has_empty_potion_slot(game)
        for potion in screen_state.get("potions", []):
            price = _shop_price(potion)
            if price > gold:
                continue
            key = _potion_key(potion)
            if has_empty_potion_slot and any(token in key for token in SHOP_HIGH_IMPACT_POTION_TOKENS):
                potion_score = SHOP_HIGH_IMPACT_POTION_SCORE
                if self.boss_prep_needs_potion(game):
                    potion_score += SHOP_BOSS_PREP_POTION_BONUS
                candidates.append(
                    (
                        potion_score - price * 0.04,
                        choice_index,
                        f"buy {potion.get('name', potion.get('id'))} for {price} gold",
                    )
                )
            choice_index += 1

        if not candidates:
            return Decision([{"action": "cancel"}], "No high-confidence shop purchase; leave.")
        score, index, reason = max(candidates, key=lambda item: item[0])
        return Decision([{"action": "choose", "choice_index": index}], f"Shop {reason}; score {score:.1f}.")

    def decide_boss_reward(self, game: dict[str, Any]) -> Decision:
        relics = game.get("screen_state", {}).get("relics", [])
        if not relics:
            return Decision([{"action": "skip"}], "No boss relics visible.")
        ranked = [
            (self.memory.relic_score(relic.get("name", relic.get("id", ""))), index, relic)
            for index, relic in enumerate(relics, start=1)
        ]
        score, index, relic = max(ranked, key=lambda item: item[0])
        return Decision([{"action": "choose", "choice_index": index}], f"Pick boss relic {relic.get('name')} score {score:.1f}.")


def _shop_price(item: dict[str, Any]) -> int:
    try:
        return int(item.get("price", 9999) or 9999)
    except (TypeError, ValueError):
        return 9999


def _act2_low_hp_slow_power_shop_penalty(card: dict[str, Any], game: dict[str, Any]) -> float:
    if int(game.get("act", 1) or 1) < 2:
        return 0.0
    current_hp = int(game.get("current_hp", 0) or 0)
    max_hp = int(game.get("max_hp", 0) or 0)
    if max_hp <= 0 or current_hp / max_hp > 0.55:
        return 0.0
    name = normalize_card_name(str(card.get("id") or card.get("name") or ""))
    card_type = str(card.get("type", "")).upper()
    if card_type == "POWER" and (name in SLOW_ENGINE_CARDS or name in SHOP_ACT2_LOW_HP_SLOW_POWER_NAMES):
        return SHOP_ACT2_LOW_HP_SLOW_POWER_PENALTY
    return 0.0


def _potion_key(potion: dict[str, Any]) -> str:
    return "".join(ch for ch in str(potion.get("id") or potion.get("name") or "").lower() if ch.isalnum())
