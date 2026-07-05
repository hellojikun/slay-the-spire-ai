"""Chest screen policy."""

from __future__ import annotations

from typing import Any

from .policy_decision import Decision


class ChestPolicy:
    def __init__(self) -> None:
        self._probe_floors: set[int] = set()

    def decide(self, game: dict[str, Any]) -> Decision:
        screen_state = game.get("screen_state") or {}
        rewards = screen_state.get("rewards") or []
        for index, reward in enumerate(rewards, start=1):
            if reward.get("reward_type") in {"GOLD", "STOLEN_GOLD", "RELIC"}:
                return Decision([{"action": "choose", "choice_index": index}], f"Collect chest {reward.get('reward_type')}.")
        if rewards:
            return Decision([{"action": "choose", "choice_index": 1}], "Take visible chest reward.")

        try:
            floor = int(game.get("floor", -1))
        except (TypeError, ValueError):
            floor = -1
        if (
            game.get("room_phase") == "COMPLETE"
            and screen_state.get("chest_open") is not True
            and floor not in self._probe_floors
        ):
            self._probe_floors.add(floor)
            return Decision(
                [{"action": "choose", "choice_index": 1}],
                "Chest complete without reward details; try open/collect before proceeding.",
            )

        if screen_state.get("chest_open") or game.get("room_phase") == "COMPLETE":
            return Decision([{"action": "proceed"}], "Chest is open; proceed.")
        return Decision([{"action": "choose", "choice_index": 1}], "Open chest.")
