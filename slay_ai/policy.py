"""Explainable heuristic policy for Slay the Spire."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .combat_search import find_best_combat_sequence
from .domain.monsters import monster_attack_damage
from .memory import StrategyMemory, normalize_card_name
from .model import COMBAT_VALUE_MODEL_PATH, POTION_TEMPO_MODEL_PATH, ROUTE_RISK_MODEL_PATH, PotionTempoModel, RouteRiskModel
from .policy_card_reward import AOE_ATTACK_CARDS, SLOW_ENGINE_CARDS, CardRewardPolicy
from .policy_character import DEFAULT_CHARACTER_PROFILE, profile_for
from .policy_chest import ChestPolicy
from .policy_combat import CombatPolicy
from .policy_decision import Decision
from .policy_event import EventPolicy
from .policy_potion import PotionPolicy
from .policy_reward import CombatRewardPolicy
from .policy_rest import RestGridPolicy
from .policy_route import decide_route
from .policy_shop import ShopRelicPolicy
from .static_knowledge import StaticKnowledge
from .train_combat_value_model import CombatValueScorer


LOW_HP_ENGINE_COMBAT_PENALTY = 20.0
SELF_DAMAGE_RISK_CARDS = DEFAULT_CHARACTER_PROFILE.self_damage_risk_cards
IMMEDIATE_SELF_DAMAGE_ENGINE_CARDS = DEFAULT_CHARACTER_PROFILE.immediate_self_damage_engine_cards
SELF_DAMAGE_HP_COST_CARDS = DEFAULT_CHARACTER_PROFILE.self_damage_hp_cost_cards
ACT2_MULTI_ENEMY_SELF_DAMAGE_SETUP_PENALTY = 9.0
REPEAT_SELF_DAMAGE_ENGINE_PENALTY = 18.0
SHALLOW_SLIME_SPLIT_PENALTY = 36.0
SHALLOW_SLIME_SPLIT_RATIO = 0.42
ENERGY_SETUP_CARD_GAIN = DEFAULT_CHARACTER_PROFILE.energy_gain_cards
ENERGY_SETUP_CARDS = frozenset(ENERGY_SETUP_CARD_GAIN)
SEARCH_PROTECTED_SINGLE_CARDS = DEFAULT_CHARACTER_PROFILE.search_protected_single_cards
ATTACK_BLOCK_CARDS = DEFAULT_CHARACTER_PROFILE.attack_block_cards
REFLECT_DAMAGE_POWER_IDS = {"sharphide", "thorns"}
DUPLICATION_DEFENSE_BLOCK_THRESHOLD = 8
DUPLICATION_ATTACK_DAMAGE_THRESHOLD = 18
DUPLICATION_HIGH_VALUE_CARDS = DEFAULT_CHARACTER_PROFILE.duplication_high_value_cards
@dataclass
class _PendingSearchSequence:
    floor: int
    turn: int
    card_keys: tuple[str, ...]
    reason: str


class HeuristicPolicy:
    def __init__(
        self,
        memory: StrategyMemory,
        character: str = "IRONCLAD",
        model_authority: str = "shadow",
        combat_value_model_path: Path | None = COMBAT_VALUE_MODEL_PATH,
        route_risk_model_path: Path | None = ROUTE_RISK_MODEL_PATH,
        potion_tempo_model_path: Path | None = POTION_TEMPO_MODEL_PATH,
    ) -> None:
        self.memory = memory
        self.character = character
        self._model_authority = model_authority
        self._pending_search_sequence: _PendingSearchSequence | None = None
        self._combat_value_scorer = CombatValueScorer(combat_value_model_path) if combat_value_model_path else None
        self._route_risk_model = (
            RouteRiskModel.load(route_risk_model_path)
            if route_risk_model_path and model_authority in {"assist", "pilot"}
            else None
        )
        self._potion_tempo_model = (
            PotionTempoModel.load(potion_tempo_model_path)
            if potion_tempo_model_path and model_authority in {"assist", "pilot"}
            else None
        )
        self._static_knowledge = (
            StaticKnowledge.load()
            if self._potion_tempo_model is not None and self._potion_tempo_model.feature_weights
            else None
        )
        self._card_reward_policy = CardRewardPolicy(memory, character=character, model_authority=model_authority)
        self._chest_policy = ChestPolicy()
        self._combat_reward_policy = CombatRewardPolicy(has_empty_potion_slot=_has_empty_potion_slot)
        self._event_policy = EventPolicy(memory, character=character)
        self._rest_grid_policy = RestGridPolicy(memory, character=character)
        self._shop_relic_policy = ShopRelicPolicy(
            memory,
            card_reward_score=self._card_reward_policy.score_card_reward,
            should_purge_strike=_shop_should_purge_strike,
            boss_prep_needs_potion=self._card_reward_policy.act1_boss_prep_needs_potion,
            has_empty_potion_slot=_has_empty_potion_slot,
        )
        self._potion_policy = PotionPolicy(
            highest_attack_target=_highest_attack_target,
            choose_target=_choose_target,
            is_long_fight=_is_long_fight,
            is_dangerous_early_scaling_fight=_is_dangerous_early_scaling_fight,
            has_duplication_potion_target=_has_duplication_potion_target,
            liquid_memories_target=_liquid_memories_target,
            potion_tempo_model=self._potion_tempo_model,
            model_authority=model_authority,
            static_knowledge=self._static_knowledge,
        )
        self._combat_policy = CombatPolicy(
            potion_policy=self._potion_policy,
            monster_attack=_monster_attack,
            hp_ratio=_hp_ratio,
            empty_hand_after_actions=_empty_hand_after_actions,
            clear_pending_search_sequence=self._clear_pending_search_sequence,
            pending_search_action=self._pending_search_action,
            guardian_pressure_draw_action=self._guardian_pressure_draw_action,
            combat_local_search_action=self._combat_local_search_action,
            best_single_combat_action=self.best_single_combat_action,
        )

    def decide(self, state: dict[str, Any]) -> Decision:
        if not state.get("in_game"):
            self._clear_pending_search_sequence()
            return Decision([], "Game is at main menu; use --start or --continue.")

        game = state.get("game_state", {})
        screen = str(game.get("screen_type", "NONE"))
        if screen == "NONE" and game.get("room_phase") == "COMBAT":
            return self._combat(game)
        self._clear_pending_search_sequence()
        if screen == "COMBAT_REWARD":
            return self._combat_reward_policy.decide_combat_reward(game)
        if screen == "CARD_REWARD":
            return self._card_reward_policy.decide_card_reward(game)
        if screen == "MAP":
            return self._map(game)
        if screen == "REST":
            return self._rest_grid_policy.decide_rest(game)
        if screen == "GRID":
            return self._rest_grid_policy.decide_grid(game)
        if screen == "HAND_SELECT":
            return self._event_policy.decide_hand_select(game)
        if screen == "BOSS_REWARD":
            return self._shop_relic_policy.decide_boss_reward(game)
        if screen == "EVENT":
            return self._event_policy.decide_event(game)
        if screen == "SHOP_ROOM":
            return Decision([{"action": "choose", "choice_index": 1}], "Enter shop.")
        if screen == "SHOP_SCREEN":
            return self._shop_relic_policy.decide_shop_screen(game)
        if screen == "CHEST":
            return self._chest_policy.decide(game)
        if screen == "GAME_OVER":
            return Decision([], "Run is over; record outcome.", should_stop=True)
        if game.get("room_phase") == "COMPLETE":
            return Decision([{"action": "proceed"}], "Room complete; proceed.")
        return Decision([], f"No policy for screen {screen}; waiting.")

    def _combat(self, game: dict[str, Any]) -> Decision:
        return self._combat_policy.decide_combat(game)

    def _clear_pending_search_sequence(self) -> None:
        self._pending_search_sequence = None

    def _pending_search_action(self, game: dict[str, Any], incoming: int, current_block: int) -> Decision | None:
        pending = self._pending_search_sequence
        if pending is None:
            return None
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        if pending.floor != int(game.get("floor", 0) or 0) or pending.turn != int(combat.get("turn", 1) or 1):
            self._clear_pending_search_sequence()
            return None
        if not pending.card_keys:
            self._clear_pending_search_sequence()
            return None
        energy = int(player.get("current_energy", 0) or 0)
        next_key = pending.card_keys[0]
        if _fresh_search_supersedes_pending(game, pending, next_key, incoming, current_block):
            self._clear_pending_search_sequence()
            return None
        match = _find_playable_card_by_key(combat.get("hand", []), next_key, energy)
        if match is None:
            self._clear_pending_search_sequence()
            return None
        index, card = match
        damage = _card_damage_value(card)
        block = _card_block_value(card)
        if block > 0 and damage <= 0 and current_block >= incoming:
            self._clear_pending_search_sequence()
            return None
        monsters = combat.get("monsters", [])
        action = {"action": "play_card", "card_index": index}
        if damage > 0 and _card_key(card) not in AOE_ATTACK_CARDS:
            target_index, _ = _choose_target(monsters, damage)
            if target_index is not None:
                action["target_index"] = target_index
        if damage > 0 and _search_first_attack_has_reflect_risk(card, action, monsters, player):
            self._clear_pending_search_sequence()
            return None
        remaining = pending.card_keys[1:]
        self._pending_search_sequence = (
            _PendingSearchSequence(pending.floor, pending.turn, remaining, pending.reason) if remaining else None
        )
        return Decision([action], f"Continue one-turn search: play {card.get('name', next_key)} from {pending.reason}.")

    def _guardian_pressure_draw_action(
        self,
        game: dict[str, Any],
        monsters: list[dict[str, Any]],
        incoming: int,
        current_block: int,
        hp_ratio: float,
    ) -> Decision | None:
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
        pressure = max(0, incoming - current_block)
        if not _dangerous_pressure(pressure, current_hp, hp_ratio):
            return None
        if not _guardian_mode_shift_under_pressure(monsters):
            return None
        if _player_has_power(player, {"nodraw"}):
            return None
        energy = int(player.get("current_energy", 0) or 0)
        if energy <= 0:
            return None
        hand = combat.get("hand", [])
        for index, card in enumerate(hand, start=1):
            if _card_key(card) != "Battle Trance":
                continue
            if not card.get("is_playable", True):
                continue
            if _card_energy_cost(card, energy) > energy:
                continue
            return Decision(
                [{"action": "play_card", "card_index": index}],
                "Guardian high-pressure turn; draw before spending energy on block.",
            )
        return None

    def best_single_combat_action(self, game: dict[str, Any]) -> Decision:
        """Return the old single-card combat choice; useful for tests and debugging."""
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        hand = combat.get("hand", [])
        monsters = combat.get("monsters", [])
        energy = _safe_int(player.get("current_energy"))
        current_block = _safe_int(player.get("block"))
        current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
        incoming = sum(_monster_attack(m) for m in monsters)
        hp_ratio = _hp_ratio(player)

        energy_setup = self._energy_setup_action(hand, monsters, energy, incoming, current_block)
        if energy_setup:
            return energy_setup

        direct_lethal = self._single_enemy_direct_lethal_action(
            hand, monsters, energy, incoming, current_block, current_hp, hp_ratio
        )
        if direct_lethal:
            return direct_lethal

        best: tuple[float, dict[str, Any], str] | None = None
        for index, card in enumerate(hand, start=1):
            if not card.get("is_playable", True):
                continue
            cost = _card_energy_cost(card, energy)
            if cost > energy:
                continue
            if _zero_energy_x_attack(card, energy):
                continue
            score, target_index, reason = self._score_combat_card(
                card, hand, monsters, incoming, current_block, current_hp, hp_ratio, energy
            )
            self_damage_penalty = _self_damage_engine_penalty(
                card,
                game,
                hand,
                monsters,
                incoming,
                current_block,
                current_hp,
                hp_ratio,
            )
            if self_damage_penalty:
                score -= self_damage_penalty
                reason = f"Play {card.get('name')} with score {score:.1f}."
            if _single_target_x_cost_penalty_applies(card, hand, index, monsters, energy):
                score -= min(18.0, 4.0 * max(energy, 1))
                reason = f"Play {card.get('name')} with score {score:.1f}."
            if _x_cost_attack_spends_needed_block(
                card, hand, index, monsters, energy, incoming, current_block, current_hp, hp_ratio
            ):
                pressure = max(0, incoming - current_block)
                score -= min(24.0, max(12.0, float(pressure)))
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
        fallback = self._pressure_attack_fallback(hand, monsters, energy, incoming, current_block, current_hp, hp_ratio)
        if fallback:
            return fallback
        return Decision([{"action": "end_turn"}], f"No valuable playable card. Incoming={incoming}, block={current_block}.")

    def _combat_local_search_action(self, game: dict[str, Any]) -> Decision | None:
        result = find_best_combat_sequence(game)
        if result is None:
            return None
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        monsters = combat.get("monsters", [])
        current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
        current_block = int(player.get("block", 0) or 0)
        single_card_override = _single_card_search_override(result, game, monsters, current_hp)
        if len(result.sequence) < 2 and not single_card_override:
            return None
        if (
            current_hp > 0
            and result.projected_loss >= current_hp
            and not result.avoided_lethal
            and not _desperate_single_card_search_override(result, game, current_hp)
        ):
            return None
        if _is_gremlin_nob_fight(monsters) and not result.avoided_lethal:
            return None
        first_card = _card_for_action(game, result.first_action)
        if first_card and _search_first_attack_has_reflect_risk(first_card, result.first_action, monsters, player):
            if not (result.kills > 0 and current_hp > 0 and result.projected_loss < current_hp):
                return None
        result_first_card_key = normalize_card_name(result.first_card_key)
        if (
            result.projected_loss >= result.initial_loss
            and result.kills <= 0
            and result.attacks_removed <= 0
            and result_first_card_key not in ENERGY_SETUP_CARDS
        ):
            return None

        single = self.best_single_combat_action(game)
        single_action = single.actions[0] if single.actions else {}
        single_card_key = _card_key_for_action(game, single_action)
        if single_action.get("action") == "play_card" and "direct lethal" in str(single.reason).lower():
            return None
        loss_reduction = result.initial_loss - result.projected_loss
        modest_block_sequence = (
            loss_reduction >= 5
            and result.initial_loss >= 8
            and current_block > 0
            and _action_adds_block(game, result.first_action)
        )
        pressure_block_sequence = (
            loss_reduction > 0
            and result.initial_loss >= max(18, int(current_hp * 0.40))
            and _action_adds_block(game, result.first_action)
        )
        sentries_dazed_pressure_sequence = (
            _is_sentries_fight(monsters)
            and loss_reduction >= 5
            and result.initial_loss >= 10
            and _action_adds_block(game, result.first_action)
        )
        if single_card_key in SEARCH_PROTECTED_SINGLE_CARDS and not result.avoided_lethal:
            return None
        if (
            single_action.get("action") == "play_card"
            and single_card_key != result_first_card_key
            and not result.avoided_lethal
            and not modest_block_sequence
            and not pressure_block_sequence
            and not sentries_dazed_pressure_sequence
            and result.projected_loss > result.initial_loss - 6
            and result.kills <= 0
            and result.attacks_removed <= 0
            and result_first_card_key not in ENERGY_SETUP_CARDS
        ):
            return None

        self._remember_search_sequence(game, result.sequence_card_keys, result.reason, loss_reduction, result.initial_loss)
        metadata = _search_result_metadata(result)
        combat_value_shadow = self._combat_value_shadow_metadata(game, result)
        if combat_value_shadow is not None:
            metadata["combat_value_shadow"] = combat_value_shadow
        return Decision(
            [result.first_action],
            f"One-turn search: {result.reason}.",
            metadata={"search": metadata},
        )

    def _combat_value_shadow_metadata(self, game: dict[str, Any], result: Any) -> dict[str, Any] | None:
        scorer = self._combat_value_scorer
        if scorer is None or not scorer.available:
            return None
        row = _combat_value_shadow_row(game, result)
        try:
            predicted_value = scorer.score(row)
        except Exception as exc:  # pragma: no cover - protects live play from optional model failures.
            return {
                "status": "error",
                "path": str(scorer.model_path),
                "error": str(exc)[:160],
                "runtime_authority": False,
            }
        return {
            "status": "scored",
            "path": str(scorer.model_path),
            "predicted_value": predicted_value,
            "runtime_authority": False,
        }

    def _remember_search_sequence(
        self,
        game: dict[str, Any],
        sequence_card_keys: tuple[str, ...],
        reason: str,
        loss_reduction: int,
        initial_loss: int,
    ) -> None:
        self._clear_pending_search_sequence()
        remaining = tuple(normalize_card_name(key) for key in sequence_card_keys[1:])
        if loss_reduction <= 0 or not remaining:
            return
        hand = game.get("combat_state", {}).get("hand", [])
        monsters = game.get("combat_state", {}).get("monsters", [])
        high_pressure_gain = loss_reduction >= 8 and initial_loss >= 18
        if (
            not high_pressure_gain
            and not _sequence_has_block_followup(hand, remaining)
            and not (_guardian_mode_shift_under_pressure(monsters) and _sequence_has_attack_followup(hand, remaining))
        ):
            return
        combat = game.get("combat_state", {})
        self._pending_search_sequence = _PendingSearchSequence(
            floor=int(game.get("floor", 0) or 0),
            turn=int(combat.get("turn", 1) or 1),
            card_keys=remaining,
            reason=reason,
        )

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

    def _single_enemy_direct_lethal_action(
        self,
        hand: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
        energy: int,
        incoming: int,
        current_block: int,
        current_hp: int,
        hp_ratio: float,
    ) -> Decision | None:
        live_targets = [
            (index, monster)
            for index, monster in enumerate(monsters, start=1)
            if not (monster.get("is_dead") or monster.get("is_gone")) and _monster_hp_with_block(monster) > 0
        ]
        if len(live_targets) != 1:
            return None
        target_index, target = live_targets[0]

        best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        for index, card in enumerate(hand, start=1):
            if not card.get("is_playable", True):
                continue
            if int(card.get("cost", 0) or 0) < 0:
                continue
            cost = _card_energy_cost(card, energy)
            if cost > energy:
                continue
            damage = _card_damage_value(card)
            if damage <= 0 or not _attack_kills(target, damage):
                continue
            action = {"action": "play_card", "card_index": index}
            if _card_key(card) not in AOE_ATTACK_CARDS and card.get("has_target", True):
                action["target_index"] = target_index
            reflect_damage = _attack_reflect_damage(monsters, action.get("target_index"), damage)
            reflect_loss = max(0, reflect_damage - current_block - _card_block_value(card))
            if reflect_loss >= current_hp:
                continue
            if _self_damage_fallback_too_risky(card, target, monsters, current_hp, incoming, current_block, hp_ratio):
                continue
            score = float(damage) - cost * 0.1
            score -= SELF_DAMAGE_HP_COST_CARDS.get(_card_key(card), 0) * 0.5
            if best is None or score > best[0]:
                best = (score, action, card)

        if best is None:
            return None
        return Decision([best[1]], f"Play {best[2].get('name')} for direct lethal.")

    def _pressure_attack_fallback(
        self,
        hand: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
        energy: int,
        incoming: int,
        current_block: int,
        current_hp: int,
        hp_ratio: float,
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
            damage = _card_damage_value(card)
            if damage <= 0:
                continue
            target_index, target = _choose_target(monsters, damage)
            reflect_damage = _attack_reflect_damage(monsters, target_index, damage)
            reflect_buffer = current_block + _card_block_value(card)
            reflect_loss = max(0, reflect_damage - reflect_buffer)
            if reflect_damage and reflect_loss >= current_hp and not (target and _attack_kills(target, damage)):
                continue
            if _self_damage_fallback_too_risky(card, target, monsters, current_hp, incoming, current_block, hp_ratio):
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
        hand: list[dict[str, Any]],
        monsters: list[dict[str, Any]],
        incoming: int,
        current_block: int,
        current_hp: int,
        hp_ratio: float,
        energy: int,
    ) -> tuple[float, int | None, str]:
        name = _card_key(card)
        ctype = card.get("type")
        damage = _card_damage_value(card)
        block = _card_block_value(card)
        score = 0.0
        target_index: int | None = None
        target: dict[str, Any] | None = None
        pressure = max(0, incoming - current_block)
        dangerous_pressure = _dangerous_pressure(pressure, current_hp, hp_ratio)

        if damage:
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
                elif pressure > 0 and not (target and _attack_stops_current_intent(target, damage)):
                    if block <= reflect_damage:
                        score -= reflect_damage * 4 + min(pressure, reflect_damage) * 3
                    else:
                        score -= reflect_damage * 1.5
                elif hp_ratio < 0.35:
                    score -= reflect_damage * 2 + reflect_loss * 10
            if target and _bad_shallow_slime_split(card, target, damage, incoming, current_block, energy):
                score -= SHALLOW_SLIME_SPLIT_PENALTY

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

        second_wind_cleanup = _second_wind_burn_cleanup_bonus(card, hand, monsters, current_hp, hp_ratio, pressure)
        if second_wind_cleanup:
            score += second_wind_cleanup

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
            score -= _high_pressure_power_penalty(name, pressure, current_hp, hp_ratio)

        if card.get("exhausts"):
            score += 2
        if name in {"Offering", "Battle Trance", "Burning Pact", "Shrug It Off"}:
            score += 10
        if name in SLOW_ENGINE_CARDS and dangerous_pressure:
            score -= LOW_HP_ENGINE_COMBAT_PENALTY
        if name in SELF_DAMAGE_RISK_CARDS and (dangerous_pressure or hp_ratio < 0.45):
            score -= LOW_HP_ENGINE_COMBAT_PENALTY
        self_damage_cost = SELF_DAMAGE_HP_COST_CARDS.get(name, 0)
        if self_damage_cost:
            kills_target = bool(target and _attack_kills(target, damage))
            ends_fight = bool(kills_target and _all_other_monsters_gone(monsters, target))
            if current_hp <= self_damage_cost:
                score -= 200
            elif current_hp - self_damage_cost <= 6 and not ends_fight:
                score -= 24
            elif hp_ratio < 0.20 and kills_target and not ends_fight:
                score -= 14
            elif hp_ratio < 0.25 and not kills_target:
                score -= 28
            elif hp_ratio < 0.45 and not kills_target:
                score -= 16
            if pressure > 0 and not ends_fight:
                score -= _high_pressure_self_damage_penalty(self_damage_cost, pressure, current_hp, hp_ratio)
        if name in {"Feed", "Hand of Greed"} and target_index is not None:
            score += 5

        return score, target_index, f"Play {card.get('name')} with score {score:.1f}."

    def _map(self, game: dict[str, Any]) -> Decision:
        return decide_route(
            game,
            self.memory,
            route_risk_model=self._route_risk_model,
            model_authority=self._model_authority,
        )

def _monster_attack(monster: dict[str, Any]) -> int:
    return monster_attack_damage(monster)


def _safe_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_gremlin_nob_fight(monsters: list[dict[str, Any]]) -> bool:
    return any("gremlinnob" in str(monster.get("id") or monster.get("name") or "").replace(" ", "").lower() for monster in monsters)


def _is_sentries_fight(monsters: list[dict[str, Any]]) -> bool:
    live_sentries = 0
    for monster in monsters:
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        label = str(monster.get("id") or monster.get("name") or "").replace(" ", "").lower()
        if "sentry" in label:
            live_sentries += 1
    return live_sentries >= 2


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


def _is_dangerous_early_scaling_fight(game: dict[str, Any], monsters: list[dict[str, Any]]) -> bool:
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
    max_hp = int(player.get("max_hp", game.get("max_hp", current_hp)) or current_hp or 1)
    current_block = int(player.get("block", 0) or 0)
    incoming = max(0, sum(_monster_attack(monster) for monster in monsters) - current_block)
    if incoming < 14 or current_hp / max(max_hp, 1) > 0.65:
        return False

    live_count = 0
    total_hp = 0
    for monster in monsters:
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        live_count += 1
        max_hp_monster = int(monster.get("max_hp", 0) or 0)
        current_hp_monster = int(monster.get("current_hp", 0) or 0)
        total_hp += max(max_hp_monster, current_hp_monster)
    return live_count >= 2 and total_hp >= 65


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


def _shop_should_purge_strike(game: dict[str, Any]) -> bool:
    if int(game.get("act", 1) or 1) > 2:
        return False
    profile = profile_for(game.get("class"))
    return sum(1 for card in game.get("deck", []) if profile.is_starter_strike(card)) >= 4


def _card_energy_cost(card: dict[str, Any], current_energy: int) -> int:
    cost = int(card.get("cost", 0))
    if cost < 0:
        return max(current_energy, 0)
    return max(cost, 0)


def _zero_energy_x_attack(card: dict[str, Any], current_energy: int) -> bool:
    return int(card.get("cost", 0) or 0) < 0 and current_energy <= 0 and _card_damage_value(card) > 0


def _energy_setup_has_payoff(
    card: dict[str, Any],
    hand: list[dict[str, Any]],
    card_index: int,
    energy: int,
    incoming: int,
    current_block: int,
) -> bool:
    name = _card_key(card)
    gain = ENERGY_SETUP_CARD_GAIN.get(name, 0)
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
        if raw_cost < 0 and _card_damage_value(other) > 0:
            return True
        other_cost = _card_energy_cost(other, energy_after_gain)
        if energy_after_cost < other_cost <= energy_after_gain:
            return True
        total_useful_cost += other_cost
    return total_useful_cost > energy


def _card_has_immediate_value(card: dict[str, Any], incoming: int, current_block: int) -> bool:
    name = _card_key(card)
    if _card_damage_value(card) > 0:
        return True
    if _card_block_value(card) > 0 and incoming > current_block:
        return True
    if name in {"Battle Trance", "Burning Pact", "Disarm", "Offering", "Shockwave", "Shrug It Off"}:
        return True
    return card.get("type") == "POWER"


def _find_playable_card_by_key(hand: list[dict[str, Any]], card_key: str, energy: int) -> tuple[int, dict[str, Any]] | None:
    for index, card in enumerate(hand, start=1):
        if _card_key(card) != card_key:
            continue
        if not card.get("is_playable", True):
            continue
        if _card_energy_cost(card, energy) > energy:
            continue
        return index, card
    return None


def _sequence_has_block_followup(hand: list[dict[str, Any]], card_keys: tuple[str, ...]) -> bool:
    remaining = list(card_keys)
    for card in hand:
        key = _card_key(card)
        if key not in remaining:
            continue
        if _card_block_value(card) > 0:
            return True
        remaining.remove(key)
    return False


def _sequence_has_attack_followup(hand: list[dict[str, Any]], card_keys: tuple[str, ...]) -> bool:
    remaining = list(card_keys)
    for card in hand:
        key = _card_key(card)
        if key not in remaining:
            continue
        if _card_damage_value(card) > 0:
            return True
        remaining.remove(key)
    return False


def _single_target_x_cost_penalty_applies(
    card: dict[str, Any],
    hand: list[dict[str, Any]],
    card_index: int,
    monsters: list[dict[str, Any]],
    energy: int,
) -> bool:
    damage = _card_damage_value(card)
    if energy <= 1 or int(card.get("cost", 0) or 0) >= 0 or damage <= 0:
        return False
    live_monsters = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
    if len(live_monsters) != 1:
        return False
    if _attack_kills(live_monsters[0], damage):
        return False
    for index, other in enumerate(hand, start=1):
        if index == card_index or not other.get("is_playable", True):
            continue
        if int(other.get("cost", 0) or 0) < 0:
            continue
        if _card_energy_cost(other, energy) <= energy and _card_damage_value(other) > 0:
            return True
    return False


def _x_cost_attack_spends_needed_block(
    card: dict[str, Any],
    hand: list[dict[str, Any]],
    card_index: int,
    monsters: list[dict[str, Any]],
    energy: int,
    incoming: int,
    current_block: int,
    current_hp: int,
    hp_ratio: float,
) -> bool:
    card_damage = _card_damage_value(card)
    if energy <= 1 or int(card.get("cost", 0) or 0) >= 0 or card_damage <= 0:
        return False
    pressure = max(0, incoming - current_block)
    if not _dangerous_pressure(pressure, current_hp, hp_ratio):
        return False
    live_monsters = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
    attackers = [monster for monster in live_monsters if _monster_attack(monster) > 0]
    if not attackers:
        return False
    damage = card_damage * energy
    if damage <= 0:
        return False
    name = _card_key(card)
    if name in AOE_ATTACK_CARDS or not card.get("has_target", True):
        if all(_attack_stops_current_intent(monster, damage) for monster in attackers):
            return False
    else:
        _, target = _choose_target(live_monsters, damage)
        if target is not None and len(attackers) == 1 and _attack_stops_current_intent(target, damage):
            return False
    for index, other in enumerate(hand, start=1):
        if index == card_index or not other.get("is_playable", True):
            continue
        if _card_block_value(other) <= 0:
            continue
        other_cost = _card_energy_cost(other, energy)
        if 0 < other_cost <= energy:
            return True
    return False


def _self_damage_fallback_too_risky(
    card: dict[str, Any],
    target: dict[str, Any] | None,
    monsters: list[dict[str, Any]],
    current_hp: int,
    incoming: int,
    current_block: int,
    hp_ratio: float,
) -> bool:
    self_damage = SELF_DAMAGE_HP_COST_CARDS.get(_card_key(card), 0)
    if self_damage <= 0:
        return False
    if current_hp <= self_damage:
        return True

    damage = _card_damage_value(card)
    target_killed = bool(target and _attack_kills(target, damage))
    remaining_incoming = incoming
    if target_killed and target is not None:
        remaining_incoming = max(0, incoming - _monster_attack(target))
    projected_loss = self_damage + max(0, remaining_incoming - current_block)
    if projected_loss >= current_hp:
        return True
    if target_killed and all(monster is target or monster.get("is_dead") or monster.get("is_gone") for monster in monsters):
        return False
    remaining_hp = current_hp - projected_loss
    if remaining_hp <= 4:
        return True
    if hp_ratio <= 0.20 and not target_killed:
        return True
    return False


def _high_pressure_power_penalty(name: str, pressure: int, current_hp: int, hp_ratio: float) -> float:
    if pressure <= 0 or current_hp <= 0:
        return 0.0
    lethal_pressure = pressure >= current_hp
    high_pressure = lethal_pressure or pressure >= max(14, int(current_hp * 0.30))
    if not high_pressure:
        return 0.0
    penalty = min(34.0, 8.0 + pressure * 0.75)
    projected_hp = current_hp - pressure
    if lethal_pressure:
        penalty += 36.0
    if projected_hp <= max(10, int(current_hp * 0.25)):
        penalty += 18.0
    if hp_ratio < 0.45:
        penalty += 10.0
    if name in {"Demon Form", "Barricade", "Dark Embrace", "Inflame"}:
        penalty += 8.0
    if name == "Metallicize":
        penalty *= 0.65
    return penalty


def _high_pressure_self_damage_penalty(self_damage_cost: int, pressure: int, current_hp: int, hp_ratio: float) -> float:
    projected_loss = self_damage_cost + pressure
    if projected_loss <= 0 or current_hp <= 0:
        return 0.0
    penalty = 0.0
    if projected_loss >= max(12, int(current_hp * 0.30)):
        penalty += min(42.0, 10.0 + projected_loss * 0.55)
    remaining_hp = current_hp - projected_loss
    if remaining_hp <= max(8, int(current_hp * 0.20)):
        penalty += 22.0
    if hp_ratio < 0.35:
        penalty += 8.0
    return penalty


def _self_damage_engine_penalty(
    card: dict[str, Any],
    game: dict[str, Any],
    hand: list[dict[str, Any]],
    monsters: list[dict[str, Any]],
    incoming: int,
    current_block: int,
    current_hp: int,
    hp_ratio: float,
) -> float:
    name = _card_key(card)
    if name not in IMMEDIATE_SELF_DAMAGE_ENGINE_CARDS:
        return 0.0
    live_monsters = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
    if not live_monsters:
        return 0.0

    penalty = 0.0
    combat = game.get("combat_state", {})
    if hp_ratio < 0.70 and _combat_has_current_turn_activity(combat):
        penalty += REPEAT_SELF_DAMAGE_ENGINE_PENALTY

    act = int(game.get("act", 0) or 0)
    floor = int(game.get("floor", 0) or 0)
    pressure = max(0, incoming - current_block)
    has_safe_play = any(_is_safe_non_self_damage_play(other) for other in hand)
    if (
        hp_ratio < 0.70
        and pressure <= 0
        and len(live_monsters) >= 3
        and (act >= 2 or floor >= 17)
        and has_safe_play
    ):
        penalty += ACT2_MULTI_ENEMY_SELF_DAMAGE_SETUP_PENALTY
        if current_hp > 0 and current_hp <= 55:
            penalty += 4.0
    return penalty


def _combat_has_current_turn_activity(combat: dict[str, Any]) -> bool:
    for key in ("cards_played_this_turn", "cards_discarded_this_turn", "cards_exhausted_this_turn"):
        value = combat.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return True
        if isinstance(value, (list, tuple, set, dict)) and bool(value):
            return True
    return False


def _is_safe_non_self_damage_play(card: dict[str, Any]) -> bool:
    if not card.get("is_playable", True):
        return False
    if _card_key(card) in SELF_DAMAGE_RISK_CARDS:
        return False
    return _card_damage_value(card) > 0 or _card_block_value(card) > 0


def _card_damage_value(card: dict[str, Any]) -> int:
    if str(card.get("type") or "").upper() != "ATTACK":
        return 0
    return max(0, int(card.get("damage", 0) or 0))


def _card_block_value(card: dict[str, Any]) -> int:
    block = int(card.get("block", 0) or 0)
    if block <= 0:
        return 0
    if str(card.get("type") or "").upper() == "ATTACK" and _card_key(card) not in ATTACK_BLOCK_CARDS:
        return 0
    return block


def _second_wind_burn_cleanup_bonus(
    card: dict[str, Any],
    hand: list[dict[str, Any]],
    monsters: list[dict[str, Any]],
    current_hp: int,
    hp_ratio: float,
    pressure: int,
) -> float:
    if _card_key(card) != "Second Wind":
        return 0.0
    burn_damage = sum(_burn_end_turn_damage(other) for other in hand if other is not card)
    if burn_damage <= 0:
        return 0.0
    boss_burn_pressure = _is_hexaghost_fight(monsters) and (
        hp_ratio <= 0.55 or pressure > 0 or current_hp <= burn_damage + 30
    )
    lethalish_burn_pressure = current_hp > 0 and current_hp <= burn_damage + max(12, pressure)
    if not (boss_burn_pressure or hp_ratio <= 0.35 or lethalish_burn_pressure):
        return 0.0

    status_count = sum(
        1
        for other in hand
        if other is not card and str(other.get("type") or "").upper() == "STATUS"
    )
    collateral_count = sum(1 for other in hand if other is not card and _second_wind_collateral_card(other))
    bonus = burn_damage * 4.0 + status_count * 2.0
    if boss_burn_pressure:
        bonus += 8.0
    if hp_ratio <= 0.25:
        bonus += 6.0
    if lethalish_burn_pressure:
        bonus += 6.0
    bonus -= collateral_count * 5.0
    return max(0.0, bonus)


def _second_wind_collateral_card(card: dict[str, Any]) -> bool:
    ctype = str(card.get("type") or "").upper()
    if ctype in {"ATTACK", "STATUS"}:
        return False
    return _card_key(card) != "Second Wind"


def _burn_end_turn_damage(card: dict[str, Any]) -> int:
    raw_key = str(card.get("id") or card.get("name") or "")
    key = raw_key.lower().replace("+", "")
    name = str(card.get("name") or "")
    if key != "burn" and name.strip().lower().replace("+", "") != "burn":
        return 0
    if int(card.get("upgrades", 0) or 0) > 0 or "+" in name or "+" in raw_key:
        return 4
    return 2


def _is_hexaghost_fight(monsters: list[dict[str, Any]]) -> bool:
    for monster in monsters:
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        label = f"{monster.get('id', '')} {monster.get('name', '')}".replace(" ", "").lower()
        if "hexaghost" in label:
            return True
    return False


def _is_act1_boss_fight(monsters: list[dict[str, Any]]) -> bool:
    for monster in monsters:
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        label = f"{monster.get('id', '')} {monster.get('name', '')}".replace(" ", "").lower()
        if any(boss in label for boss in ("hexaghost", "slimeboss", "theguardian")):
            return True
    return False


def _boss_single_card_search_override(
    result: Any,
    game: dict[str, Any],
    monsters: list[dict[str, Any]],
    current_hp: int,
) -> bool:
    if len(getattr(result, "sequence", ()) or ()) != 1:
        return False
    if not (_is_act1_boss_fight(monsters) or (_safe_int(game.get("act")) == 1 and _safe_int(game.get("floor")) >= 16)):
        return False
    loss_reduction = int(result.initial_loss or 0) - int(result.projected_loss or 0)
    if bool(result.avoided_lethal):
        return True
    if int(result.attacks_removed or 0) >= 10:
        return True
    return loss_reduction >= max(10, int(max(current_hp, 1) * 0.25))


def _single_card_search_override(
    result: Any,
    game: dict[str, Any],
    monsters: list[dict[str, Any]],
    current_hp: int,
) -> bool:
    if _boss_single_card_search_override(result, game, monsters, current_hp):
        return True
    if len(getattr(result, "sequence", ()) or ()) != 1:
        return False
    if bool(result.avoided_lethal):
        return True
    if current_hp <= 0:
        return False
    loss_reduction = int(result.initial_loss or 0) - int(result.projected_loss or 0)
    high_pressure = int(result.initial_loss or 0) >= max(12, int(max(current_hp, 1) * 0.35))
    if not high_pressure or loss_reduction <= 0:
        return False
    if int(result.attacks_removed or 0) >= 8 and loss_reduction >= 6:
        return True
    if _action_adds_block(game, result.first_action) and loss_reduction >= max(6, int(current_hp * 0.20)):
        return True
    return loss_reduction >= max(10, int(current_hp * 0.25)) and int(result.projected_loss or 0) <= max(
        6, int(current_hp * 0.50)
    )


def _desperate_single_card_search_override(result: Any, game: dict[str, Any], current_hp: int) -> bool:
    if len(getattr(result, "sequence", ()) or ()) != 1:
        return False
    if current_hp <= 0 or int(result.initial_loss or 0) < current_hp:
        return False
    loss_reduction = int(result.initial_loss or 0) - int(result.projected_loss or 0)
    if loss_reduction < max(6, int(max(current_hp, 1) * 0.25)):
        return False
    return int(result.attacks_removed or 0) > 0 or _action_adds_block(game, result.first_action)


def _fresh_search_supersedes_pending(
    game: dict[str, Any],
    pending: _PendingSearchSequence,
    next_key: str,
    incoming: int,
    current_block: int,
) -> bool:
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    current_hp = int(player.get("current_hp", game.get("current_hp", 0)) or 0)
    result = find_best_combat_sequence(game)
    if result is None:
        return False
    result_first_key = normalize_card_name(result.first_card_key)
    if result_first_key == normalize_card_name(next_key):
        return False
    if current_hp > 0 and result.projected_loss >= current_hp and not result.avoided_lethal:
        return False

    monsters = combat.get("monsters", [])
    loss_reduction = int(result.initial_loss or 0) - int(result.projected_loss or 0)
    if _boss_single_card_search_override(result, game, monsters, current_hp):
        return True
    if result.avoided_lethal:
        return True
    if result.attacks_removed > 0 and loss_reduction >= max(6, int(max(current_hp, 1) * 0.20)):
        return True
    if _action_adds_block(game, result.first_action) and loss_reduction >= max(6, int(max(current_hp, 1) * 0.20)):
        return True
    pressure = max(0, incoming - current_block)
    if pressure >= max(12, int(max(current_hp, 1) * 0.30)) and result.projected_loss <= max(5, int(current_hp * 0.25)):
        return True
    return False


def _dangerous_pressure(pressure: int, current_hp: int, hp_ratio: float) -> bool:
    if pressure <= 0:
        return False
    if current_hp <= 0:
        return hp_ratio < 0.5
    return hp_ratio < 0.5 or pressure >= max(10, int(current_hp * 0.25))


def _attack_kills(monster: dict[str, Any], damage: int) -> bool:
    hp_with_block = _monster_hp_with_block(monster)
    return hp_with_block > 0 and damage >= hp_with_block


def _all_other_monsters_gone(monsters: list[dict[str, Any]], target: dict[str, Any]) -> bool:
    for monster in monsters:
        if monster is target:
            continue
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        if _monster_hp_with_block(monster) > 0:
            return False
    return True


def _monster_hp_with_block(monster: dict[str, Any]) -> int:
    hp = monster.get("current_hp")
    if hp is None:
        hp = monster.get("hp", 0)
    return max(0, int(hp or 0)) + max(0, int(monster.get("block", 0) or 0))


def _monster_power_amount(monster: dict[str, Any], power_ids: set[str]) -> int:
    for power in monster.get("powers", []) or []:
        key = str(power.get("id") or power.get("name") or "").replace(" ", "").lower()
        if key in power_ids:
            return max(0, int(power.get("amount", 0) or 0))
    return 0


def _player_has_power(player: dict[str, Any], power_ids: set[str]) -> bool:
    for power in player.get("powers", []) or []:
        key = str(power.get("id") or power.get("name") or "").replace(" ", "").lower()
        if key in power_ids:
            return True
    return False


def _guardian_mode_shift_under_pressure(monsters: list[dict[str, Any]]) -> bool:
    for monster in monsters:
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        label = f"{monster.get('id', '')} {monster.get('name', '')}".replace(" ", "").lower()
        if "guardian" not in label:
            continue
        if _monster_attack(monster) <= 0:
            continue
        if _monster_power_amount(monster, {"modeshift"}) > 0:
            return True
    return False


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
    if _attack_triggers_guardian_mode_shift(monster, damage):
        return True
    if not _is_splitting_slime(monster):
        return False
    hp = _safe_int(monster.get("current_hp"))
    max_hp = _safe_int(monster.get("max_hp"))
    block = _safe_int(monster.get("block"))
    hp_loss = max(0, damage - block)
    return hp > max_hp / 2 and hp - hp_loss <= max_hp / 2


def _attack_triggers_guardian_mode_shift(monster: dict[str, Any], damage: int) -> bool:
    if damage <= 0:
        return False
    mode_shift = _monster_power_amount(monster, {"modeshift"})
    if mode_shift <= 0:
        return False
    block = int(monster.get("block", 0) or 0)
    return max(0, damage - block) >= mode_shift


def _bad_shallow_slime_split(
    card: dict[str, Any],
    monster: dict[str, Any],
    damage: int,
    incoming: int,
    current_block: int,
    energy: int,
) -> bool:
    if damage <= 0 or not _is_splitting_slime(monster):
        return False
    if incoming > current_block:
        return False
    if _attack_kills(monster, damage):
        return False
    hp = int(monster.get("current_hp", 0) or 0)
    max_hp = int(monster.get("max_hp", 0) or 0)
    block = int(monster.get("block", 0) or 0)
    if max_hp <= 0 or hp <= max_hp / 2:
        return False
    hp_after = hp - max(0, damage - block)
    if hp_after > max_hp / 2 or hp_after <= 0:
        return False
    if hp_after / max_hp < SHALLOW_SLIME_SPLIT_RATIO:
        return False
    remaining_energy = max(0, energy - _card_energy_cost(card, energy))
    return remaining_energy <= 0


def _is_splitting_slime(monster: dict[str, Any]) -> bool:
    label = f"{monster.get('id', '')} {monster.get('name', '')}".lower()
    max_hp = int(monster.get("max_hp", 0) or 0)
    return "slime" in label and max_hp >= 30


def _is_slime(monster: dict[str, Any]) -> bool:
    label = f"{monster.get('id', '')} {monster.get('name', '')}".lower()
    return "slime" in label


def _choose_target(monsters: list[dict[str, Any]], damage: int) -> tuple[int | None, dict[str, Any] | None]:
    if not monsters:
        return None, None
    live_monsters = [monster for monster in monsters if not (monster.get("is_dead") or monster.get("is_gone"))]
    post_split_slimes = sum(1 for monster in live_monsters if _is_slime(monster)) >= 2
    ranked = []
    for index, monster in enumerate(monsters, start=1):
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        hp_with_block = _monster_hp_with_block(monster)
        attack = _monster_attack(monster)
        killable = _attack_kills(monster, damage)
        stops_attack = _attack_stops_current_intent(monster, damage)
        if post_split_slimes:
            ranked.append((stops_attack, killable, attack > 0, -hp_with_block, attack, index, monster))
        else:
            ranked.append((stops_attack, attack > 0, attack, killable, -hp_with_block, index, monster))
    if not ranked:
        return None, None
    *_, index, monster = max(ranked)
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


def _same_play_card_action(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("action") != "play_card" or right.get("action") != "play_card":
        return False
    if _safe_int(left.get("card_index")) != _safe_int(right.get("card_index")):
        return False
    left_target = _safe_int(left.get("target_index"))
    right_target = _safe_int(right.get("target_index"))
    if left_target > 0 and right_target > 0:
        return left_target == right_target
    return True


def _action_adds_block(game: dict[str, Any], action: dict[str, Any]) -> bool:
    card = _card_for_action(game, action)
    return bool(card and _card_block_value(card) > 0)


def _search_first_attack_has_reflect_risk(
    card: dict[str, Any],
    action: dict[str, Any],
    monsters: list[dict[str, Any]],
    player: dict[str, Any],
) -> bool:
    damage = _card_damage_value(card)
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


def _empty_hand_after_actions(combat: dict[str, Any]) -> bool:
    if int(combat.get("cards_discarded_this_turn", 0) or 0) > 0:
        return True
    return bool(combat.get("discard_pile") or combat.get("exhaust_pile"))


def _copy_monster(monster: dict[str, Any]) -> dict[str, Any]:
    copied = dict(monster)
    if "move" in monster and isinstance(monster["move"], dict):
        copied["move"] = dict(monster["move"])
    return copied


def _apply_attack(monsters: list[dict[str, Any]], target_index: int, damage: int) -> bool:
    if target_index < 1 or target_index > len(monsters):
        return False
    monster = monsters[target_index - 1]
    block = _safe_int(monster.get("block"))
    remaining_damage = max(0, damage - block)
    monster["block"] = max(0, block - damage)
    monster["current_hp"] = max(0, _safe_int(monster.get("current_hp")) - remaining_damage)
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


def _has_duplication_potion_target(
    game: dict[str, Any],
    monsters: list[dict[str, Any]],
    *,
    incoming_sensitive: bool,
) -> bool:
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    hand = combat.get("hand", [])
    try:
        energy = int(player.get("current_energy", 0) or 0)
    except (TypeError, ValueError):
        energy = 0
    try:
        current_block = int(player.get("block", 0) or 0)
    except (TypeError, ValueError):
        current_block = 0
    pressure = max(0, sum(_monster_attack(monster) for monster in monsters) - current_block)
    long_fight = _is_long_fight(monsters)

    for card in hand:
        if not isinstance(card, dict) or not card.get("is_playable", True):
            continue
        if _card_energy_cost(card, energy) > energy:
            continue
        name = _card_key(card)
        block = _card_block_value(card)
        damage = _card_damage_value(card)
        if pressure > 0 and block >= DUPLICATION_DEFENSE_BLOCK_THRESHOLD:
            return True
        if incoming_sensitive and pressure >= 12 and name in DUPLICATION_HIGH_VALUE_CARDS:
            return True
        if long_fight and (damage >= DUPLICATION_ATTACK_DAMAGE_THRESHOLD or name in DUPLICATION_HIGH_VALUE_CARDS):
            return True
    return False


def _liquid_memories_target(
    game: dict[str, Any],
    monsters: list[dict[str, Any]],
    incoming: int,
    current_block: int,
    current_hp: int,
) -> dict[str, Any] | None:
    pressure = max(0, incoming - current_block)
    if pressure <= 0:
        return None
    combat = game.get("combat_state", {})
    discard_pile = combat.get("discard_pile") or combat.get("discardPile") or []
    if not isinstance(discard_pile, list):
        return None

    best: tuple[float, dict[str, Any]] | None = None
    for card in discard_pile:
        if not isinstance(card, dict):
            continue
        score = _liquid_memories_card_score(card, monsters, pressure, current_hp)
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, card)
    return best[1] if best and best[0] >= 16 else None


def _liquid_memories_card_score(
    card: dict[str, Any],
    monsters: list[dict[str, Any]],
    pressure: int,
    current_hp: int,
) -> float:
    name = _card_key(card)
    block = _card_block_value(card)
    damage = _card_damage_value(card)
    score = 0.0

    if block > 0:
        needed_to_survive = max(0, pressure - max(current_hp - 1, 0))
        score += min(block, pressure) * 1.3
        if needed_to_survive > 0 and block >= needed_to_survive:
            score += 18
        elif block >= 8:
            score += 10

    if name in {"Disarm", "Shockwave", "Intimidate", "Piercing Wail", "Dark Shackles"}:
        score += 18 + min(pressure, 18)

    if damage > 0:
        _, target = _choose_target(monsters, damage)
        if target and _attack_stops_current_intent(target, damage):
            score += 22
        elif target and _attack_kills(target, damage):
            score += 14
        elif pressure >= current_hp and damage >= 18:
            score += 10

    if name in DUPLICATION_HIGH_VALUE_CARDS:
        score += 6
    return score


def _search_result_metadata(result: Any) -> dict[str, Any]:
    return {
        "type": "one_turn_search",
        "sequence_card_keys": list(result.sequence_card_keys),
        "first_card_key": result.first_card_key,
        "score": result.score,
        "initial_loss": result.initial_loss,
        "projected_loss": result.projected_loss,
        "kills": result.kills,
        "attacks_removed": result.attacks_removed,
        "avoided_lethal": result.avoided_lethal,
        "retaliation_damage": getattr(result, "retaliation_damage", 0),
    }


def _combat_value_shadow_row(game: dict[str, Any], result: Any) -> dict[str, Any]:
    combat = game.get("combat_state", {})
    player = combat.get("player", {})
    hand = combat.get("hand", [])
    monsters = [
        monster
        for monster in combat.get("monsters", [])
        if isinstance(monster, dict) and not (monster.get("is_dead") or monster.get("is_gone"))
    ]
    current_hp = _safe_int(player.get("current_hp", game.get("current_hp")))
    max_hp = _safe_int(player.get("max_hp", game.get("max_hp"))) or max(current_hp, 1)
    initial_loss = _safe_int(getattr(result, "initial_loss", 0))
    projected_loss = _safe_int(getattr(result, "projected_loss", 0))
    return {
        "character": game.get("class"),
        "ascension": game.get("ascension_level"),
        "floor": game.get("floor"),
        "act": game.get("act"),
        "turn": combat.get("turn"),
        "hp_ratio": current_hp / max(max_hp, 1),
        "current_hp": current_hp,
        "max_hp": max_hp,
        "current_block": player.get("block"),
        "current_energy": player.get("current_energy"),
        "incoming": sum(_monster_attack(monster) for monster in monsters),
        "hand_size": len(hand),
        "hand_ids": [_card_key(card) for card in hand if isinstance(card, dict)],
        "hand_names": [card.get("name") for card in hand if isinstance(card, dict)],
        "playable_count": sum(1 for card in hand if not isinstance(card, dict) or card.get("is_playable", True)),
        "enemy_count": len(monsters),
        "enemy_ids": [monster.get("id") or monster.get("name") for monster in monsters],
        "enemy_intents": [monster.get("intent") or monster.get("move") for monster in monsters],
        "label_first_card_key": getattr(result, "first_card_key", None),
        "label_sequence_card_keys": list(getattr(result, "sequence_card_keys", ()) or []),
        "sequence_length": len(getattr(result, "sequence_card_keys", ()) or []),
        "search_type": "one_turn_search",
        "initial_loss": initial_loss,
        "projected_loss": projected_loss,
        "loss_delta": initial_loss - projected_loss,
        "kills": getattr(result, "kills", 0),
        "attacks_removed": getattr(result, "attacks_removed", 0),
        "retaliation_damage": getattr(result, "retaliation_damage", 0),
        "avoided_lethal": bool(getattr(result, "avoided_lethal", False)),
    }
