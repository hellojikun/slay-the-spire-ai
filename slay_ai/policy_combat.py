"""Combat decision orchestration for the heuristic policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .policy_decision import Decision
from .policy_potion import PotionPolicy


MonsterAttackFn = Callable[[dict[str, Any]], int]
HpRatioFn = Callable[[dict[str, Any]], float]
EmptyHandFn = Callable[[dict[str, Any]], bool]
ClearPendingFn = Callable[[], None]
PendingSearchFn = Callable[[dict[str, Any], int, int], Decision | None]
GuardianDrawFn = Callable[[dict[str, Any], list[dict[str, Any]], int, int, float], Decision | None]
SearchActionFn = Callable[[dict[str, Any]], Decision | None]
SingleActionFn = Callable[[dict[str, Any]], Decision]


class CombatPolicy:
    def __init__(
        self,
        *,
        potion_policy: PotionPolicy,
        monster_attack: MonsterAttackFn,
        hp_ratio: HpRatioFn,
        empty_hand_after_actions: EmptyHandFn,
        clear_pending_search_sequence: ClearPendingFn,
        pending_search_action: PendingSearchFn,
        guardian_pressure_draw_action: GuardianDrawFn,
        combat_local_search_action: SearchActionFn,
        best_single_combat_action: SingleActionFn,
    ) -> None:
        self.potion_policy = potion_policy
        self.monster_attack = monster_attack
        self.hp_ratio = hp_ratio
        self.empty_hand_after_actions = empty_hand_after_actions
        self.clear_pending_search_sequence = clear_pending_search_sequence
        self.pending_search_action = pending_search_action
        self.guardian_pressure_draw_action = guardian_pressure_draw_action
        self.combat_local_search_action = combat_local_search_action
        self.best_single_combat_action = best_single_combat_action

    def decide_combat(self, game: dict[str, Any]) -> Decision:
        combat = game.get("combat_state", {})
        player = combat.get("player", {})
        hand = combat.get("hand", [])
        monsters = combat.get("monsters", [])
        energy = _safe_int(player.get("current_energy"))
        current_block = _safe_int(player.get("block"))
        incoming = sum(self.monster_attack(monster) for monster in monsters)
        hp_ratio = self.hp_ratio(player)

        if not monsters:
            self.clear_pending_search_sequence()
            if game.get("room_phase") == "COMPLETE":
                return Decision([{"action": "proceed"}], "Combat complete; proceed.")
            return Decision([{"action": "wait", "ms": 250}], "Combat is ending; wait for reward transition.")

        if not hand and monsters:
            self.clear_pending_search_sequence()
            turn = int(combat.get("turn", 1) or 1)
            if 0 < energy < 3:
                return Decision([{"action": "end_turn"}], "Hand is empty after spending energy; end the turn.")
            if turn > 1 and current_block > 0 and current_block >= incoming:
                return Decision([{"action": "end_turn"}], "Hand is empty and block covers incoming; end the turn.")
            if energy <= 0 and (turn > 1 or self.empty_hand_after_actions(combat)):
                return Decision([{"action": "end_turn"}], "Hand is empty; end the turn.")
            return Decision([{"action": "wait", "ms": 250}], "Combat is still settling; wait for hand to be dealt.")

        potion_action = self.potion_policy.emergency_potion(game, monsters, incoming, current_block, hp_ratio)
        if potion_action:
            self.clear_pending_search_sequence()
            return potion_action
        potion_action = self.potion_policy.strategic_combat_potion(game, monsters)
        if potion_action:
            self.clear_pending_search_sequence()
            return potion_action

        pending_action = self.pending_search_action(game, incoming, current_block)
        if pending_action:
            return pending_action

        draw_setup = self.guardian_pressure_draw_action(game, monsters, incoming, current_block, hp_ratio)
        if draw_setup:
            self.clear_pending_search_sequence()
            return draw_setup

        search_action = self.combat_local_search_action(game)
        if search_action:
            return search_action

        return self.best_single_combat_action(game)


def _safe_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
