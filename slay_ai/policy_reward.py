"""Reward screen policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .policy_decision import Decision


GamePredicate = Callable[[dict[str, Any]], bool]


class CombatRewardPolicy:
    def __init__(self, *, has_empty_potion_slot: GamePredicate) -> None:
        self.has_empty_potion_slot = has_empty_potion_slot
        self._empty_reward_wait_floors: set[int] = set()

    def decide_combat_reward(self, game: dict[str, Any]) -> Decision:
        rewards = game.get("screen_state", {}).get("rewards", [])
        for index, reward in enumerate(rewards, start=1):
            rtype = _reward_type(reward)
            if rtype in {"GOLD", "STOLEN_GOLD", "RELIC"}:
                return Decision([{"action": "choose", "choice_index": index}], f"Collect {rtype}.")
            if rtype == "POTION" and self.has_empty_potion_slot(game):
                return Decision([{"action": "choose", "choice_index": index}], "Take potion into empty slot.")
        for index, reward in enumerate(rewards, start=1):
            if _reward_type(reward) == "CARD":
                return Decision([{"action": "choose", "choice_index": index}], "Open card reward.")
        if rewards and all(_reward_type(reward) == "POTION" for reward in rewards):
            return Decision([{"action": "proceed"}], "Potion slots are full; skip remaining potion rewards.")
        if game.get("screen_state", {}).get("rewards"):
            return Decision([{"action": "choose", "choice_index": 1}], "Take remaining reward.")
        if _should_wait_for_reward_snapshot(game, self._empty_reward_wait_floors):
            return Decision([{"action": "wait", "ms": 250}], "Reward screen is empty; wait for reward snapshot.")
        return Decision([{"action": "proceed"}], "Rewards collected; proceed.")


def _reward_type(reward: dict[str, Any]) -> str:
    return str(reward.get("reward_type") or reward.get("type") or "").replace(" ", "_").replace("-", "_").upper()


def _should_wait_for_reward_snapshot(game: dict[str, Any], waited_floors: set[int]) -> bool:
    if game.get("screen_type") != "COMBAT_REWARD":
        return False
    if game.get("room_phase") == "COMPLETE":
        return False
    try:
        floor = int(game.get("floor", -1))
    except (TypeError, ValueError):
        floor = -1
    if floor in waited_floors:
        return False
    waited_floors.add(floor)
    return True
