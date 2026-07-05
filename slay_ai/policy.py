"""Explainable heuristic policy for Slay the Spire."""

from __future__ import annotations

import re
from typing import Any

from .combat_search import find_best_combat_sequence
from .domain.monsters import monster_attack_damage
from .memory import StrategyMemory, normalize_card_name
from .policy_decision import Decision
from .policy_route import decide_route


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
EARLY_UNSUPPORTED_ENGINE_PENALTY = 24.0
EARLY_DUPLICATE_EXHAUST_ENABLER_PENALTY = 16.0
ACT1_LOW_HP_SURVIVAL_CARD_BONUS = 14.0
LOW_HP_ENGINE_COMBAT_PENALTY = 20.0
SLOW_ENGINE_CARDS = {"Burning Pact", "Havoc"}
SELF_DAMAGE_RISK_CARDS = {"Bloodletting", "Combust", "Offering"}
ENERGY_SETUP_CARDS = {"Seeing Red"}
SEARCH_PROTECTED_SINGLE_CARDS = {
    "Battle Trance",
    "Burning Pact",
    "Dark Shackles",
    "Disarm",
    "Intimidate",
    "Offering",
    "Piercing Wail",
    "Shockwave",
}
AOE_ATTACK_CARDS = {"Cleave", "Immolate", "Reaper", "Thunderclap"}
REFLECT_DAMAGE_POWER_IDS = {"sharphide", "thorns"}
SHOP_BUY_CARD_THRESHOLD = 54.0
SHOP_PURGE_STRIKE_SCORE = 58.0
SHOP_HIGH_IMPACT_POTION_SCORE = 50.0
SHOP_HIGH_IMPACT_POTION_TOKENS = {
    "attack",
    "block",
    "dexterity",
    "distilledchaos",
    "essenceofsteel",
    "explosive",
    "fear",
    "fire",
    "heartofiron",
    "power",
    "speed",
    "strength",
    "weak",
}
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


class HeuristicPolicy:
    def __init__(self, memory: StrategyMemory, character: str = "IRONCLAD") -> None:
        self.memory = memory
        self.character = character

    def decide(self, state: dict[str, Any]) -> Decision:
        if not state.get("in_game"):
            return Decision([], "Game is at main menu; use --start or --continue.")

        game = state.get("game_state", {})
        screen = str(game.get("screen_type", "NONE"))
        if screen == "NONE" and game.get("room_phase") == "COMBAT":
            return self._combat(game)
        if screen == "COMBAT_REWARD":
            return self._combat_reward(game)
        if screen == "CARD_REWARD":
            return self._card_reward(game)
        if screen == "MAP":
            return self._map(game)
        if screen == "REST":
            return self._rest(game)
        if screen == "GRID":
            return self._grid(game)
        if screen == "HAND_SELECT":
            return self._hand_select(game)
        if screen == "BOSS_REWARD":
            return self._boss_reward(game)
        if screen == "EVENT":
            return self._event(game)
        if screen == "SHOP_ROOM":
            return Decision([{"action": "choose", "choice_index": 1}], "Enter shop.")
        if screen == "SHOP_SCREEN":
            return self._shop_screen(game)
        if screen == "CHEST":
            if game.get("screen_state", {}).get("chest_open") or game.get("room_phase") == "COMPLETE":
                return Decision([{"action": "proceed"}], "Chest is open; proceed.")
            return Decision([{"action": "choose", "choice_index": 1}], "Open chest.")
        if screen == "GAME_OVER":
            return Decision([], "Run is over; record outcome.", should_stop=True)
        if game.get("room_phase") == "COMPLETE":
            return Decision([{"action": "proceed"}], "Room complete; proceed.")
        return Decision([], f"No policy for screen {screen}; waiting.")

    def _combat(self, game: dict[str, Any]) -> Decision:
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        hand = combat.get("hand", [])
        monsters = combat.get("monsters", [])
        energy = int(player.get("current_energy", 0))
        current_block = int(player.get("block", 0))
        incoming = sum(_monster_attack(m) for m in monsters)
        hp_ratio = _hp_ratio(player)

        if not monsters:
            if game.get("room_phase") == "COMPLETE":
                return Decision([{"action": "proceed"}], "Combat complete; proceed.")
            return Decision([{"action": "wait", "ms": 250}], "Combat is ending; wait for reward transition.")

        if not hand and monsters:
            turn = int(combat.get("turn", 1) or 1)
            if 0 < energy < 3:
                return Decision([{"action": "end_turn"}], "Hand is empty after spending energy; end the turn.")
            if turn > 1 and current_block > 0 and current_block >= incoming:
                return Decision([{"action": "end_turn"}], "Hand is empty and block covers incoming; end the turn.")
            if energy <= 0 and (turn > 1 or _empty_hand_after_actions(combat)):
                return Decision([{"action": "end_turn"}], "Hand is empty; end the turn.")
            return Decision([{"action": "wait", "ms": 250}], "Combat is still settling; wait for hand to be dealt.")

        potion_action = self._emergency_potion(game, monsters, incoming, current_block, hp_ratio)
        if potion_action:
            return potion_action
        potion_action = self._strategic_combat_potion(game, monsters)
        if potion_action:
            return potion_action

        search_action = self._combat_local_search_action(game)
        if search_action:
            return search_action

        return self.best_single_combat_action(game)

    def _strategic_combat_potion(self, game: dict[str, Any], monsters: list[dict[str, Any]]) -> Decision | None:
        combat = game.get("combat_state", {})
        turn = int(combat.get("turn", 1) or 1)
        if turn > 2 or not _is_long_fight(monsters):
            return None
        for slot, potion in enumerate(game.get("potions", []), start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if "cultist" in key or "strength" in key or "steroid" in key:
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Long boss/elite fight; use {potion.get('name', potion.get('id'))}.",
                )
        for slot, potion in enumerate(game.get("potions", []), start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if "blessingoftheforge" in key or "forge" in key:
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Long boss/elite fight; use {potion.get('name', potion.get('id'))}.",
                )
        return None

    def _emergency_potion(
        self,
        game: dict[str, Any],
        monsters: list[dict[str, Any]],
        incoming: int,
        current_block: int,
        hp_ratio: float,
    ) -> Decision | None:
        potions = game.get("potions", [])
        current_hp = int(game.get("current_hp", 0) or game.get("combat_state", {}).get("player", {}).get("current_hp", 0))
        projected_loss = max(0, incoming - current_block)
        lethal = projected_loss >= current_hp
        high_damage = projected_loss >= max(18, int(current_hp * 0.30))
        low_hp = hp_ratio <= 0.35
        defensive_danger = projected_loss > 0 and (lethal or high_damage or low_hp)
        tempo_danger = lethal or high_damage or low_hp
        if not tempo_danger:
            return None

        target = _highest_attack_target(monsters) or 1
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if ("regen" in key or "fruitjuice" in key or "blood" in key) and (low_hp or defensive_danger):
                return Decision([{"action": "use_potion", "potion_slot": slot}], f"Low HP; use {potion.get('name', potion.get('id'))}.")
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if defensive_danger and ("weak" in key or "fear" in key):
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot, "target_index": target}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))}.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if defensive_danger and ("essenceofsteel" in key or "heartofiron" in key or "block" in key or "metallicize" in key):
                return Decision(
                    [{"action": "use_potion", "potion_slot": slot}],
                    f"Dangerous incoming damage; use {potion.get('name', potion.get('id'))}.",
                )
        for slot, potion in enumerate(potions, start=1):
            if potion.get("is_empty") or not potion.get("can_use", True):
                continue
            key = _potion_key(potion)
            if any(
                token in key
                for token in (
                    "fire",
                    "explosive",
                    "attack",
                    "distilledchaos",
                    "energy",
                    "snecko",
                    "swift",
                    "power",
                    "steroid",
                    "strength",
                    "dexterity",
                    "speed",
                    "blessingoftheforge",
                    "forge",
                    "liquidbronze",
                    "bronze",
                    "thorn",
                )
            ):
                action = {"action": "use_potion", "potion_slot": slot}
                if potion.get("requires_target") or any(token in key for token in ("fire", "attack")):
                    potion_target = _choose_target(monsters, 20)[0] if any(token in key for token in ("fire", "attack")) else target
                    action["target_index"] = potion_target or target
                return Decision([action], f"Emergency tempo; use {potion.get('name', potion.get('id'))}.")
        return None

    def best_single_combat_action(self, game: dict[str, Any]) -> Decision:
        """Return the old single-card combat choice; useful for tests and debugging."""
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        hand = combat.get("hand", [])
        monsters = combat.get("monsters", [])
        energy = int(player.get("current_energy", 0))
        current_block = int(player.get("block", 0))
        current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
        incoming = sum(_monster_attack(m) for m in monsters)
        hp_ratio = _hp_ratio(player)

        energy_setup = self._energy_setup_action(hand, monsters, energy, incoming, current_block)
        if energy_setup:
            return energy_setup

        best: tuple[float, dict[str, Any], str] | None = None
        for index, card in enumerate(hand, start=1):
            if not card.get("is_playable", True):
                continue
            cost = _card_energy_cost(card, energy)
            if cost > energy:
                continue
            score, target_index, reason = self._score_combat_card(
                card, monsters, incoming, current_block, current_hp, hp_ratio, energy
            )
            if _single_target_x_cost_penalty_applies(card, hand, index, monsters, energy):
                score -= min(18.0, 4.0 * max(energy, 1))
                reason = f"Play {card.get('name')} with score {score:.1f}."
            if score < self.memory.base["combat"]["minimum_card_score"]:
                continue
            action = {"action": "play_card", "card_index": index}
            if target_index is not None:
                action["target_index"] = target_index
            if best is None or score > best[0]:
                best = (score, action, reason)

        if best:
            return Decision([best[1]], best[2])
        fallback = self._pressure_attack_fallback(hand, monsters, energy, incoming, current_block, current_hp)
        if fallback:
            return fallback
        return Decision([{"action": "end_turn"}], f"No valuable playable card. Incoming={incoming}, block={current_block}.")

    def _combat_local_search_action(self, game: dict[str, Any]) -> Decision | None:
        result = find_best_combat_sequence(game)
        if result is None or len(result.sequence) < 2:
            return None
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        monsters = combat.get("monsters", [])
        current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
        if current_hp > 0 and result.projected_loss >= current_hp and not result.avoided_lethal:
            return None
        if _is_gremlin_nob_fight(monsters) and not result.avoided_lethal:
            return None
        first_card = _card_for_action(game, result.first_action)
        if first_card and _search_first_attack_has_reflect_risk(first_card, result.first_action, monsters, player):
            return None
        if (
            result.projected_loss >= result.initial_loss
            and result.kills <= 0
            and result.attacks_removed <= 0
            and result.first_card_key not in ENERGY_SETUP_CARDS
        ):
            return None

        single = self.best_single_combat_action(game)
        single_action = single.actions[0] if single.actions else {}
        single_card_key = _card_key_for_action(game, single_action)
        if single_card_key in SEARCH_PROTECTED_SINGLE_CARDS and not result.avoided_lethal:
            return None
        if (
            single_action.get("action") == "play_card"
            and single_card_key != result.first_card_key
            and not result.avoided_lethal
            and result.projected_loss > result.initial_loss - 6
            and result.kills <= 0
            and result.attacks_removed <= 0
            and result.first_card_key not in ENERGY_SETUP_CARDS
        ):
            return None

        return Decision([result.first_action], f"One-turn search: {result.reason}.")

    def _energy_setup_action(
        self,
        hand: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
        energy: int,
        incoming: int,
        current_block: int,
    ) -> Decision | None:
        if energy < 0 or _is_gremlin_nob_fight(monsters):
            return None
        for index, card in enumerate(hand, start=1):
            if _card_key(card) not in ENERGY_SETUP_CARDS:
                continue
            if not card.get("is_playable", True):
                continue
            cost = _card_energy_cost(card, energy)
            if cost > energy:
                continue
            if _energy_setup_has_payoff(card, hand, index, energy, incoming, current_block):
                return Decision(
                    [{"action": "play_card", "card_index": index}],
                    f"Play {card.get('name')} to unlock more energy.",
                )
        return None

    def _pressure_attack_fallback(
        self,
        hand: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
        energy: int,
        incoming: int,
        current_block: int,
        current_hp: int,
    ) -> Decision | None:
        if energy <= 0 or incoming <= current_block:
            return None
        best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        for index, card in enumerate(hand, start=1):
            if not card.get("is_playable", True):
                continue
            cost = _card_energy_cost(card, energy)
            if cost > energy:
                continue
            damage = int(card.get("damage", 0) or 0)
            if damage <= 0:
                continue
            target_index, target = _choose_target(monsters, damage)
            reflect_damage = _attack_reflect_damage(monsters, target_index, damage)
            reflect_buffer = current_block + int(card.get("block", 0) or 0)
            reflect_loss = max(0, reflect_damage - reflect_buffer)
            if reflect_damage and reflect_loss >= current_hp and not (target and _attack_kills(target, damage)):
                continue
            score = float(damage)
            if reflect_damage:
                score -= reflect_damage * 1.5 + reflect_loss * 10
            if target and _attack_kills(target, damage):
                score += self.memory.base["combat"]["kill_bonus"]
            if target and _monster_attack(target) > 0:
                score += self.memory.base["combat"]["attack_intent_bonus"]
            action = {"action": "play_card", "card_index": index}
            if target_index is not None:
                action["target_index"] = target_index
            if best is None or score > best[0]:
                best = (score, action, card)
        if best is None:
            return None
        if best[0] < self.memory.base["combat"]["minimum_card_score"]:
            return None
        return Decision([best[1]], f"No defense under pressure; play {best[2].get('name')} as fallback score {best[0]:.1f}.")

    def _score_combat_card(
        self,
        card: dict[str, Any],
        monsters: list[dict[str, Any]],
        incoming: int,
        current_block: int,
        current_hp: int,
        hp_ratio: float,
        energy: int,
    ) -> tuple[float, int | None, str]:
        name = _card_key(card)
        ctype = card.get("type")
        damage = int(card.get("damage", 0))
        block = int(card.get("block", 0))
        score = 0.0
        target_index: int | None = None
        pressure = max(0, incoming - current_block)
        dangerous_pressure = _dangerous_pressure(pressure, current_hp, hp_ratio)

        if ctype == "ATTACK" or damage:
            target_index, target = _choose_target(monsters, damage)
            score += damage
            if name in AOE_ATTACK_CARDS and damage:
                live_monsters = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
                target_index = None
                target = None
                score = max(score, damage * max(len(live_monsters), 1) * 0.95)
                score += sum(1 for monster in live_monsters if _attack_kills(monster, damage)) * 6
                if any(_monster_attack(monster) > 0 for monster in live_monsters):
                    score += self.memory.base["combat"]["attack_intent_bonus"]
                if name == "Thunderclap" and len(live_monsters) >= 2:
                    score += 6
            if target and _attack_kills(target, damage):
                score += self.memory.base["combat"]["kill_bonus"]
            if target and _monster_attack(target) > 0:
                score += self.memory.base["combat"]["attack_intent_bonus"]
            if name in {"Bash", "Uppercut", "Shockwave", "Clothesline"}:
                score += 8
            if name == "Whirlwind":
                score = max(score, damage * max(energy, 1) * max(len(monsters), 1) * 0.9)
            if dangerous_pressure and not (target and _attack_stops_current_intent(target, damage)):
                score -= min(14, max(8, pressure * 0.5))
            reflect_damage = _attack_reflect_damage(monsters, target_index, damage)
            if reflect_damage:
                reflect_buffer = current_block + max(block, 0)
                reflect_loss = max(0, reflect_damage - reflect_buffer)
                if reflect_loss >= current_hp:
                    score -= 120
                elif hp_ratio < 0.35:
                    score -= reflect_damage * 2 + reflect_loss * 10

        if block:
            if pressure > 0:
                block_weight = 1.4
                if pressure >= 15:
                    block_weight = 2.2
                if hp_ratio < 0.45:
                    block_weight = max(block_weight, 3.8)
                if hp_ratio < 0.5:
                    block_weight = max(block_weight, 3.2)
                if hp_ratio < 0.3:
                    block_weight = max(block_weight, 4.2)
                score += min(block, pressure + self.memory.base["combat"]["block_pressure_margin"]) * block_weight
            elif hp_ratio < self.memory.base["combat"]["low_hp_ratio"]:
                score += block * 0.35
            if _is_gremlin_nob_fight(monsters) and pressure < max(current_hp, 1):
                score -= 14

        if name in {"Disarm", "Shockwave", "Intimidate", "Piercing Wail", "Dark Shackles"}:
            pressure = max(0, incoming - current_block)
            if name == "Shockwave":
                score += 18 + len(monsters) * 4
                if pressure > 0:
                    score += min(pressure, 18)
            elif name == "Disarm":
                target_index = _highest_attack_target(monsters)
                score += 16
                if target_index is not None and _monster_attack(monsters[target_index - 1]) > 0:
                    score += min(_monster_attack(monsters[target_index - 1]), 20)
                if hp_ratio < 0.5:
                    score += 8
            else:
                score += 12
                if pressure > 0:
                    score += min(pressure, 16)
            if target_index is None and card.get("has_target"):
                target_index = _highest_attack_target(monsters)

        if ctype == "POWER":
            score += self.memory.card_score(name, self.character) * 0.35
            if incoming > current_block + 10 and hp_ratio < 0.45:
                score -= 10

        if card.get("exhausts"):
            score += 2
        if name in {"Offering", "Battle Trance", "Burning Pact", "Shrug It Off"}:
            score += 10
        if name in SLOW_ENGINE_CARDS and dangerous_pressure:
            score -= LOW_HP_ENGINE_COMBAT_PENALTY
        if name in SELF_DAMAGE_RISK_CARDS and (dangerous_pressure or hp_ratio < 0.45):
            score -= LOW_HP_ENGINE_COMBAT_PENALTY
        if name in {"Feed", "Hand of Greed"} and target_index is not None:
            score += 5

        return score, target_index, f"Play {card.get('name')} with score {score:.1f}."

    def _combat_reward(self, game: dict[str, Any]) -> Decision:
        rewards = game.get("screen_state", {}).get("rewards", [])
        for index, reward in enumerate(rewards, start=1):
            rtype = reward.get("reward_type")
            if rtype in {"GOLD", "STOLEN_GOLD", "RELIC"}:
                return Decision([{"action": "choose", "choice_index": index}], f"Collect {rtype}.")
            if rtype == "POTION" and _has_empty_potion_slot(game):
                return Decision([{"action": "choose", "choice_index": index}], "Take potion into empty slot.")
        for index, reward in enumerate(rewards, start=1):
            if reward.get("reward_type") == "CARD":
                return Decision([{"action": "choose", "choice_index": index}], "Open card reward.")
        if rewards and all(reward.get("reward_type") == "POTION" for reward in rewards):
            return Decision([{"action": "proceed"}], "Potion slots are full; skip remaining potion rewards.")
        if game.get("screen_state", {}).get("rewards"):
            return Decision([{"action": "choose", "choice_index": 1}], "Take remaining reward.")
        return Decision([{"action": "proceed"}], "Rewards collected; proceed.")

    def _card_reward(self, game: dict[str, Any]) -> Decision:
        cards = game.get("screen_state", {}).get("cards", [])
        if not cards:
            return Decision([{"action": "skip"}], "No card reward cards visible.")
        ranked = [
            (self._card_reward_score(card, game), index, card)
            for index, card in enumerate(cards, start=1)
        ]
        score, index, card = max(ranked, key=lambda item: item[0])
        if score < 28 and game.get("floor", 0) > 8:
            return Decision([{"action": "skip"}], f"Skip low-impact card reward; best was {card.get('name')} ({score:.1f}).")
        name = card.get("id") or card.get("name", "")
        display_name = card.get("name", name)
        return Decision(
            [{"action": "choose", "choice_index": index}],
            f"Pick {display_name}; reward score {score:.1f}.",
            learn_card_pick=name,
        )

    def _card_reward_score(self, card: dict[str, Any], game: dict[str, Any]) -> float:
        name = _card_key(card)
        score = self.memory.card_score(name, self.character)
        if self.character == "IRONCLAD" and name in EXHAUST_PAYOFF_CARDS and not _deck_has_exhaust_enabler(game):
            score -= EXHAUST_PAYOFF_UNSUPPORTED_PENALTY
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
        if self.character == "IRONCLAD" and _act1_deck_needs_block_stabilizer(game):
            if name in ACT1_BLOCK_STABILIZER_CARDS:
                score += 16
            if name in ATTACK_DUPLICATE_SOFT_CAPS and _deck_is_attack_heavy(game):
                copies = _deck_card_count(game, name)
                soft_cap = ATTACK_DUPLICATE_SOFT_CAPS[name]
                if copies >= soft_cap:
                    score -= 12 + max(0, copies - soft_cap) * 4
        return score

    def _shop_screen(self, game: dict[str, Any]) -> Decision:
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
        if screen_state.get("purge_available") and purge_cost <= gold and _shop_should_purge_strike(game):
            candidates.append((SHOP_PURGE_STRIKE_SCORE - purge_cost * 0.05, choice_index, f"purge Strike for {purge_cost} gold"))
        if screen_state.get("purge_available") and purge_cost <= gold:
            choice_index += 1

        for card in screen_state.get("cards", []):
            price = _shop_price(card)
            if price > gold:
                continue
            score = self._card_reward_score(card, game) - price * 0.08
            if score >= SHOP_BUY_CARD_THRESHOLD:
                candidates.append((score, choice_index, f"buy {card.get('name', card.get('id'))} for {price} gold"))
            choice_index += 1

        for relic in screen_state.get("relics", []):
            price = _shop_price(relic)
            if price <= gold:
                choice_index += 1

        has_empty_potion_slot = _has_empty_potion_slot(game)
        for potion in screen_state.get("potions", []):
            price = _shop_price(potion)
            if price > gold:
                continue
            key = _potion_key(potion)
            if has_empty_potion_slot and any(token in key for token in SHOP_HIGH_IMPACT_POTION_TOKENS):
                candidates.append(
                    (
                        SHOP_HIGH_IMPACT_POTION_SCORE - price * 0.04,
                        choice_index,
                        f"buy {potion.get('name', potion.get('id'))} for {price} gold",
                    )
                )
            choice_index += 1

        if not candidates:
            return Decision([{"action": "cancel"}], "No high-confidence shop purchase; leave.")
        score, index, reason = max(candidates, key=lambda item: item[0])
        return Decision([{"action": "choose", "choice_index": index}], f"Shop {reason}; score {score:.1f}.")

    def _map(self, game: dict[str, Any]) -> Decision:
        return decide_route(game, self.memory)

    def _rest(self, game: dict[str, Any]) -> Decision:
        options = game.get("screen_state", {}).get("rest_options", [])
        hp_ratio = _hp_ratio(game)
        labels = [str(opt).lower() for opt in options]
        rest_index = _rest_option_index(labels)
        if game.get("room_phase") == "COMPLETE":
            return Decision([{"action": "proceed"}], "Rest site complete.")
        if rest_index and hp_ratio < 0.42:
            return Decision([{"action": "choose", "choice_index": rest_index}], "Low HP; rest.")
        if rest_index and _should_rest_before_act1_danger(game, hp_ratio):
            return Decision([{"action": "choose", "choice_index": rest_index}], "Act 1 risk is high without potions; rest.")
        for index, label in enumerate(labels, start=1):
            if "smith" in label or "upgrade" in label:
                return Decision([{"action": "choose", "choice_index": index}], "HP is safe; smith.")
        return Decision([{"action": "choose", "choice_index": 1}], "Use first rest option.")

    def _grid(self, game: dict[str, Any]) -> Decision:
        screen_state = game.get("screen_state", {})
        cards = screen_state.get("cards", [])
        selected_cards = screen_state.get("selected_cards", [])
        if screen_state.get("confirm_up") or _grid_selection_complete(screen_state):
            return Decision([{"action": "confirm"}], "Confirm grid selection.")
        if not cards:
            return Decision([{"action": "confirm"}], "No grid cards; confirm.")
        if screen_state.get("for_upgrade"):
            ranked = [
                (self.memory.upgrade_score(_card_key(card), self.character), index, card)
                for index, card in enumerate(cards, start=1)
            ]
        elif screen_state.get("for_purge") or game.get("room_phase") == "EVENT":
            selected_uuids = {card.get("uuid") for card in selected_cards if isinstance(card, dict) and card.get("uuid")}
            selected_keys = {_card_key(card) for card in selected_cards if isinstance(card, dict)}
            ranked = [
                (-self.memory.card_score(_card_key(card), self.character), index, card)
                for index, card in enumerate(cards, start=1)
                if (not card.get("uuid") or card.get("uuid") not in selected_uuids)
                and _card_key(card) not in selected_keys
            ]
        else:
            ranked = [(self.memory.card_score(_card_key(card), self.character), index, card) for index, card in enumerate(cards, start=1)]
        if not ranked:
            return Decision([{"action": "confirm"}], "No unselected grid cards; confirm.")
        score, index, card = max(ranked, key=lambda item: item[0])
        return Decision([{"action": "choose", "choice_index": index}], f"Grid choose {card.get('name')} score {score:.1f}.")

    def _hand_select(self, game: dict[str, Any]) -> Decision:
        screen_state = game.get("screen_state", {})
        hand = screen_state.get("hand", [])
        max_cards = int(screen_state.get("max_cards", 1) or 1)
        ranked = sorted(
            ((self.memory.card_score(_card_key(card), self.character), index, card) for index, card in enumerate(hand, start=1)),
            key=lambda item: item[0],
        )
        drop = [index for _, index, _ in ranked[:max_cards]]
        return Decision([{"action": "select_cards", "drop": drop}], f"Drop weakest hand cards {drop}.")

    def _boss_reward(self, game: dict[str, Any]) -> Decision:
        relics = game.get("screen_state", {}).get("relics", [])
        if not relics:
            return Decision([{"action": "skip"}], "No boss relics visible.")
        ranked = [
            (self.memory.relic_score(relic.get("name", relic.get("id", ""))), index, relic)
            for index, relic in enumerate(relics, start=1)
        ]
        score, index, relic = max(ranked, key=lambda item: item[0])
        return Decision([{"action": "choose", "choice_index": index}], f"Pick boss relic {relic.get('name')} score {score:.1f}.")

    def _event(self, game: dict[str, Any]) -> Decision:
        options = game.get("screen_state", {}).get("options", [])
        enabled = [opt for opt in options if not opt.get("disabled")]
        if not enabled:
            return Decision([], "Event has no enabled option.")
        hp_ratio = _hp_ratio(game)
        current_hp = int(game.get("current_hp", 0) or 0)
        best = None
        for option in enabled:
            text = _option_text(option)
            score = 50
            hp_loss = _event_hp_loss(text)
            if _text_has_any(text, _SAFE_EVENT_WORDS):
                score += 8
            if _text_has_any(text, _HEAL_EVENT_WORDS):
                score += 20 if hp_ratio < 0.65 else 5
            if hp_loss > 0:
                if current_hp and hp_loss >= current_hp:
                    score -= 100
                elif hp_ratio < 0.25:
                    score -= 65
                elif hp_ratio < 0.45:
                    score -= 35
                else:
                    score -= min(20, hp_loss)
            if _text_has_any(text, _DANGEROUS_EVENT_WORDS):
                score -= 45 if hp_ratio < 0.45 else 12
            if _text_has_any(text, _REWARD_EVENT_WORDS):
                score += 15
            if best is None or score > best[0]:
                best = (score, option)
        assert best is not None
        choice_index = int(best[1].get("choice_index", 0)) + 1
        return Decision([{"action": "choose", "choice_index": choice_index}], f"Event option score {best[0]:.1f}: {best[1].get('label')}.")


def _monster_attack(monster: dict[str, Any]) -> int:
    return monster_attack_damage(monster)


def _is_gremlin_nob_fight(monsters: list[dict[str, Any]]) -> bool:
    return any("gremlinnob" in str(monster.get("id") or monster.get("name") or "").replace(" ", "").lower() for monster in monsters)


def _is_long_fight(monsters: list[dict[str, Any]]) -> bool:
    live_count = 0
    total_hp = 0
    for monster in monsters:
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        max_hp = int(monster.get("max_hp", 0) or 0)
        current_hp = int(monster.get("current_hp", 0) or 0)
        live_count += 1
        total_hp += max(max_hp, current_hp)
        label = str(monster.get("id") or monster.get("name") or "").replace(" ", "").lower()
        if max(max_hp, current_hp) >= 80:
            return True
        if any(token in label for token in ("boss", "guardian", "gremlinnob", "lagavulin")):
            return True
    if live_count >= 3 and total_hp >= 70:
        return True
    if total_hp >= 110:
        return True
    return False


def _card_key(card: dict[str, Any]) -> str:
    return normalize_card_name(str(card.get("id") or card.get("name") or ""))


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


def _shop_price(item: dict[str, Any]) -> int:
    try:
        return int(item.get("price", 9999) or 9999)
    except (TypeError, ValueError):
        return 9999


def _shop_should_purge_strike(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) > 2:
        return False
    return _deck_card_count(game, "Strike") + _deck_card_count(game, "Strike_R") >= 4


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


def _card_energy_cost(card: dict[str, Any], current_energy: int) -> int:
    cost = int(card.get("cost", 0))
    if cost < 0:
        return max(current_energy, 0)
    return max(cost, 0)


def _energy_setup_has_payoff(
    card: dict[str, Any],
    hand: list[dict[str, Any]],
    card_index: int,
    energy: int,
    incoming: int,
    current_block: int,
) -> bool:
    name = _card_key(card)
    gain = 2 if name == "Seeing Red" else 0
    cost = _card_energy_cost(card, energy)
    energy_after_cost = max(0, energy - cost)
    energy_after_gain = energy_after_cost + gain
    if energy_after_gain <= energy_after_cost:
        return False
    total_useful_cost = 0
    for other_index, other in enumerate(hand, start=1):
        if other_index == card_index or _card_key(other) in ENERGY_SETUP_CARDS:
            continue
        if not _card_has_immediate_value(other, incoming, current_block):
            continue
        raw_cost = int(other.get("cost", 0) or 0)
        if raw_cost < 0 and int(other.get("damage", 0) or 0) > 0:
            return True
        other_cost = _card_energy_cost(other, energy_after_gain)
        if energy_after_cost < other_cost <= energy_after_gain:
            return True
        total_useful_cost += other_cost
    return total_useful_cost > energy


def _card_has_immediate_value(card: dict[str, Any], incoming: int, current_block: int) -> bool:
    name = _card_key(card)
    if int(card.get("damage", 0) or 0) > 0:
        return True
    if int(card.get("block", 0) or 0) > 0 and incoming > current_block:
        return True
    if name in {"Battle Trance", "Burning Pact", "Disarm", "Offering", "Shockwave", "Shrug It Off"}:
        return True
    return card.get("type") == "POWER"


def _single_target_x_cost_penalty_applies(
    card: dict[str, Any],
    hand: list[dict[str, Any]],
    card_index: int,
    monsters: list[dict[str, Any]],
    energy: int,
) -> bool:
    if energy <= 1 or int(card.get("cost", 0) or 0) >= 0 or int(card.get("damage", 0) or 0) <= 0:
        return False
    live_monsters = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
    if len(live_monsters) != 1:
        return False
    if _attack_kills(live_monsters[0], int(card.get("damage", 0) or 0)):
        return False
    for index, other in enumerate(hand, start=1):
        if index == card_index or not other.get("is_playable", True):
            continue
        if int(other.get("cost", 0) or 0) < 0:
            continue
        if _card_energy_cost(other, energy) <= energy and int(other.get("damage", 0) or 0) > 0:
            return True
    return False


def _dangerous_pressure(pressure: int, current_hp: int, hp_ratio: float) -> bool:
    if pressure <= 0:
        return False
    if current_hp <= 0:
        return hp_ratio < 0.5
    return hp_ratio < 0.5 or pressure >= max(10, int(current_hp * 0.25))


def _attack_kills(monster: dict[str, Any], damage: int) -> bool:
    hp_with_block = int(monster.get("current_hp", 0)) + int(monster.get("block", 0))
    return damage >= hp_with_block


def _monster_power_amount(monster: dict[str, Any], power_ids: set[str]) -> int:
    for power in monster.get("powers", []) or []:
        key = str(power.get("id") or power.get("name") or "").replace(" ", "").lower()
        if key in power_ids:
            return max(0, int(power.get("amount", 0) or 0))
    return 0


def _attack_reflect_damage(monsters: list[dict[str, Any]], target_index: int | None, damage: int) -> int:
    if damage <= 0:
        return 0
    if target_index is None:
        targets = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
    elif 1 <= target_index <= len(monsters):
        targets = [monsters[target_index - 1]]
    else:
        return 0
    total = 0
    for monster in targets:
        if _attack_kills(monster, damage):
            continue
        total += _monster_power_amount(monster, REFLECT_DAMAGE_POWER_IDS)
    return total


def _attack_stops_current_intent(monster: dict[str, Any], damage: int) -> bool:
    if _monster_attack(monster) <= 0:
        return False
    if _attack_kills(monster, damage):
        return True
    if not _is_splitting_slime(monster):
        return False
    hp = int(monster.get("current_hp", 0))
    max_hp = int(monster.get("max_hp", 0))
    block = int(monster.get("block", 0))
    hp_loss = max(0, damage - block)
    return hp > max_hp / 2 and hp - hp_loss <= max_hp / 2


def _is_splitting_slime(monster: dict[str, Any]) -> bool:
    label = f"{monster.get('id', '')} {monster.get('name', '')}".lower()
    max_hp = int(monster.get("max_hp", 0) or 0)
    return "slime" in label and max_hp >= 30


def _choose_target(monsters: list[dict[str, Any]], damage: int) -> tuple[int | None, dict[str, Any] | None]:
    if not monsters:
        return None, None
    ranked = []
    for index, monster in enumerate(monsters, start=1):
        hp_with_block = int(monster.get("current_hp", 0)) + int(monster.get("block", 0))
        attack = _monster_attack(monster)
        killable = _attack_kills(monster, damage)
        stops_attack = _attack_stops_current_intent(monster, damage)
        ranked.append((stops_attack, attack > 0, attack, killable, -hp_with_block, index, monster))
    _, _, _, _, _, index, monster = max(ranked)
    return index, monster


def _card_key_for_action(game: dict[str, Any], action: dict[str, Any]) -> str:
    card = _card_for_action(game, action)
    return _card_key(card) if card else ""


def _card_for_action(game: dict[str, Any], action: dict[str, Any]) -> dict[str, Any] | None:
    if str(action.get("action", "")).lower() != "play_card":
        return None
    try:
        index = int(action.get("card_index", 0) or 0)
    except (TypeError, ValueError):
        return None
    if index <= 0:
        return None
    hand = game.get("combat_state", {}).get("hand", [])
    if index > len(hand):
        return None
    card = hand[index - 1]
    return card if isinstance(card, dict) else None


def _search_first_attack_has_reflect_risk(
    card: dict[str, Any],
    action: dict[str, Any],
    monsters: list[dict[str, Any]],
    player: dict[str, Any],
) -> bool:
    damage = int(card.get("damage", 0) or 0)
    if damage <= 0:
        return False
    target_index = action.get("target_index")
    if target_index is not None:
        try:
            target_index = int(target_index)
        except (TypeError, ValueError):
            target_index = None
    reflect_damage = _attack_reflect_damage(monsters, target_index, damage)
    if reflect_damage <= 0:
        return False
    current_block = int(player.get("block", 0) or 0)
    current_hp = int(player.get("current_hp", 0) or 0)
    reflect_loss = max(0, reflect_damage - current_block)
    if reflect_loss >= current_hp:
        return True
    return _hp_ratio(player) < 0.35 and reflect_loss > 0


def _highest_attack_target(monsters: list[dict[str, Any]]) -> int | None:
    if not monsters:
        return None
    ranked = [(_monster_attack(monster), index) for index, monster in enumerate(monsters, start=1)]
    return max(ranked)[1]


def _potion_key(potion: dict[str, Any]) -> str:
    return "".join(ch for ch in str(potion.get("id") or potion.get("name") or "").lower() if ch.isalnum())


def _empty_hand_after_actions(combat: dict[str, Any]) -> bool:
    if int(combat.get("cards_discarded_this_turn", 0) or 0) > 0:
        return True
    return bool(combat.get("discard_pile") or combat.get("exhaust_pile"))


_SAFE_EVENT_WORDS = (
    "leave",
    "ignore",
    "skip",
    "depart",
    "continue",
    "离开",
    "離開",
    "无视",
    "無視",
    "跳过",
    "跳過",
    "继续",
    "繼續",
)
_HEAL_EVENT_WORDS = ("heal", "healing", "治疗", "治療", "回复", "恢復", "恢复")
_DANGEROUS_EVENT_WORDS = (
    "fight",
    "combat",
    "battle",
    "attack",
    "stomp",
    "smash",
    "lose",
    "damage",
    "curse",
    "sacrifice",
    "战斗",
    "戰鬥",
    "攻击",
    "攻擊",
    "踩扁",
    "失去",
    "损失",
    "損失",
    "伤害",
    "傷害",
    "诅咒",
    "詛咒",
    "献祭",
    "獻祭",
)
_REWARD_EVENT_WORDS = (
    "remove",
    "upgrade",
    "transform",
    "relic",
    "gold",
    "card",
    "移除",
    "升级",
    "升級",
    "变化",
    "變化",
    "遗物",
    "遺物",
    "金币",
    "金幣",
    "卡牌",
)


def _option_text(option: dict[str, Any]) -> str:
    parts = [
        str(option.get("text", "")),
        str(option.get("label", "")),
        str(option.get("description", "")),
    ]
    return " ".join(parts).lower()


def _event_hp_loss(text: str) -> int:
    patterns = (
        r"(?:lose|loss|pay|take)\s*(\d+)\s*(?:hp|health|life)",
        r"(?:失去|损失|損失|支付)\s*(\d+)\s*(?:点)?\s*(?:生命|生命值|血|体力|體力)",
        r"ʧȥ\s*(\d+)\s*������",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def _text_has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word.lower() in text for word in words)


def _copy_monster(monster: dict[str, Any]) -> dict[str, Any]:
    copied = dict(monster)
    if "move" in monster and isinstance(monster["move"], dict):
        copied["move"] = dict(monster["move"])
    return copied


def _apply_attack(monsters: list[dict[str, Any]], target_index: int, damage: int) -> bool:
    if target_index < 1 or target_index > len(monsters):
        return False
    monster = monsters[target_index - 1]
    block = int(monster.get("block", 0))
    remaining_damage = max(0, damage - block)
    monster["block"] = max(0, block - damage)
    monster["current_hp"] = max(0, int(monster.get("current_hp", 0)) - remaining_damage)
    if int(monster["current_hp"]) <= 0:
        del monsters[target_index - 1]
        return True
    return False


def _all_monsters_defeated(monsters: list[dict[str, Any]]) -> bool:
    return not monsters


def _hp_ratio(obj: dict[str, Any]) -> float:
    current = float(obj.get("current_hp", 1) or 1)
    max_hp = float(obj.get("max_hp", current) or current)
    return current / max(max_hp, 1.0)


def _has_empty_potion_slot(game: dict[str, Any]) -> bool:
    return any(potion.get("is_empty") for potion in game.get("potions", []))


def _has_usable_potion(game: dict[str, Any]) -> bool:
    return any(not potion.get("is_empty") for potion in game.get("potions", []))


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


def _grid_selection_complete(screen_state: dict[str, Any]) -> bool:
    try:
        needed = int(screen_state.get("num_cards", 0) or 0)
    except (TypeError, ValueError):
        needed = 0
    if needed <= 0:
        return False
    selected = screen_state.get("selected_cards", [])
    return isinstance(selected, list) and len(selected) >= needed


def _rest_option_index(labels: list[str]) -> int | None:
    for index, label in enumerate(labels, start=1):
        if "rest" in label or "sleep" in label:
            return index
    return None


def _should_rest_before_act1_danger(game: dict[str, Any], hp_ratio: float) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    if int(game.get("floor", 0) or 0) < 5:
        return False
    if hp_ratio >= 0.78:
        return False
    if hp_ratio < 0.70:
        return True
    return not _has_elite_tempo_potion(game)
