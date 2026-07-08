import unittest

from slay_ai.policy_combat import CombatPolicy
from slay_ai.policy_decision import Decision


class FakePotionPolicy:
    def __init__(self, decision=None) -> None:
        self.decision = decision

    def emergency_potion(self, game, monsters, incoming, current_block, hp_ratio):
        return self.decision

    def strategic_combat_potion(self, game, monsters):
        return None


class CombatPolicyTests(unittest.TestCase):
    def test_emergency_potion_preempts_search_and_clears_pending(self):
        calls = []
        policy = CombatPolicy(
            potion_policy=FakePotionPolicy(Decision([{"action": "use_potion", "potion_slot": 1}], "Emergency.")),
            monster_attack=lambda monster: int(monster.get("incoming", 0)),
            hp_ratio=lambda player: 0.25,
            empty_hand_after_actions=lambda combat: False,
            clear_pending_search_sequence=lambda: calls.append("clear"),
            pending_search_action=lambda game, incoming, current_block: calls.append("pending") or None,
            guardian_pressure_draw_action=lambda game, monsters, incoming, current_block, hp_ratio: None,
            combat_local_search_action=lambda game: None,
            best_single_combat_action=lambda game: Decision([{"action": "end_turn"}], "Fallback."),
        )
        game = {
            "combat_state": {
                "player": {"current_energy": 3, "block": 0},
                "hand": [{"name": "Strike", "is_playable": True}],
                "monsters": [{"name": "Cultist", "incoming": 18}],
            }
        }

        decision = policy.decide_combat(game)

        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertEqual(calls, ["clear"])

    def test_empty_hand_with_block_ends_turn(self):
        policy = CombatPolicy(
            potion_policy=FakePotionPolicy(),
            monster_attack=lambda monster: int(monster.get("incoming", 0)),
            hp_ratio=lambda player: 0.8,
            empty_hand_after_actions=lambda combat: False,
            clear_pending_search_sequence=lambda: None,
            pending_search_action=lambda game, incoming, current_block: None,
            guardian_pressure_draw_action=lambda game, monsters, incoming, current_block, hp_ratio: None,
            combat_local_search_action=lambda game: None,
            best_single_combat_action=lambda game: Decision([{"action": "wait"}], "Fallback."),
        )
        game = {
            "combat_state": {
                "turn": 2,
                "player": {"current_energy": 3, "block": 12},
                "hand": [],
                "monsters": [{"name": "Cultist", "incoming": 8}],
            }
        }

        decision = policy.decide_combat(game)

        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_empty_hand_on_active_later_turn_waits_when_full_energy_without_block(self):
        policy = CombatPolicy(
            potion_policy=FakePotionPolicy(),
            monster_attack=lambda monster: int(monster.get("incoming", 0)),
            hp_ratio=lambda player: 0.8,
            empty_hand_after_actions=lambda combat: False,
            clear_pending_search_sequence=lambda: None,
            pending_search_action=lambda game, incoming, current_block: None,
            guardian_pressure_draw_action=lambda game, monsters, incoming, current_block, hp_ratio: None,
            combat_local_search_action=lambda game: None,
            best_single_combat_action=lambda game: Decision([{"action": "wait"}], "Fallback."),
        )
        game = {
            "combat_state": {
                "turn": 2,
                "player": {"current_energy": 3, "block": 0},
                "hand": [],
                "monsters": [{"name": "Cultist", "incoming": 18}],
            }
        }

        decision = policy.decide_combat(game)

        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_empty_hand_on_active_later_turn_ends_when_energy_is_spent(self):
        policy = CombatPolicy(
            potion_policy=FakePotionPolicy(),
            monster_attack=lambda monster: int(monster.get("incoming", 0)),
            hp_ratio=lambda player: 0.8,
            empty_hand_after_actions=lambda combat: False,
            clear_pending_search_sequence=lambda: None,
            pending_search_action=lambda game, incoming, current_block: None,
            guardian_pressure_draw_action=lambda game, monsters, incoming, current_block, hp_ratio: None,
            combat_local_search_action=lambda game: None,
            best_single_combat_action=lambda game: Decision([{"action": "wait"}], "Fallback."),
        )
        game = {
            "combat_state": {
                "turn": 2,
                "player": {"current_energy": 0, "block": 0},
                "hand": [],
                "monsters": [{"name": "Cultist", "incoming": 18}],
            }
        }

        decision = policy.decide_combat(game)

        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_none_block_is_treated_as_zero(self):
        observed = {}
        policy = CombatPolicy(
            potion_policy=FakePotionPolicy(),
            monster_attack=lambda monster: int(monster.get("incoming", 0)),
            hp_ratio=lambda player: 0.8,
            empty_hand_after_actions=lambda combat: False,
            clear_pending_search_sequence=lambda: None,
            pending_search_action=lambda game, incoming, current_block: observed.setdefault("pending_block", current_block) or None,
            guardian_pressure_draw_action=lambda game, monsters, incoming, current_block, hp_ratio: None,
            combat_local_search_action=lambda game: None,
            best_single_combat_action=lambda game: Decision([{"action": "end_turn"}], "Fallback."),
        )
        game = {
            "combat_state": {
                "player": {"current_energy": 1, "block": None},
                "hand": [{"name": "Strike", "is_playable": True}],
                "monsters": [{"name": "Cultist", "incoming": 8}],
            }
        }

        decision = policy.decide_combat(game)

        self.assertEqual(observed["pending_block"], 0)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])


if __name__ == "__main__":
    unittest.main()
