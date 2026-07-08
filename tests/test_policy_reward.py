import unittest

from slay_ai.policy_reward import CombatRewardPolicy


def reward_policy(*, has_empty_potion_slot=False) -> CombatRewardPolicy:
    return CombatRewardPolicy(has_empty_potion_slot=lambda game: has_empty_potion_slot)


class CombatRewardPolicyTests(unittest.TestCase):
    def test_collects_relic_before_card_reward(self):
        game = {
            "screen_state": {
                "rewards": [
                    {"reward_type": "CARD"},
                    {"reward_type": "RELIC", "id": "Lantern"},
                ]
            }
        }

        decision = reward_policy().decide_combat_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("Collect RELIC", decision.reason)

    def test_collects_relic_with_lowercase_reward_type(self):
        game = {
            "screen_state": {
                "rewards": [
                    {"reward_type": "card"},
                    {"reward_type": "relic", "id": "Lantern"},
                ]
            }
        }

        decision = reward_policy().decide_combat_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("Collect RELIC", decision.reason)

    def test_takes_potion_when_slot_is_empty(self):
        game = {
            "screen_state": {
                "rewards": [
                    {"reward_type": "POTION", "id": "Fire Potion"},
                    {"reward_type": "CARD"},
                ]
            }
        }

        decision = reward_policy(has_empty_potion_slot=True).decide_combat_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_opens_card_when_potion_slots_are_full(self):
        game = {
            "screen_state": {
                "rewards": [
                    {"reward_type": "POTION", "id": "Fire Potion"},
                    {"reward_type": "CARD"},
                ]
            }
        }

        decision = reward_policy(has_empty_potion_slot=False).decide_combat_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_skips_pure_potion_rewards_when_slots_are_full(self):
        game = {
            "screen_state": {
                "rewards": [
                    {"reward_type": "POTION", "id": "Dexterity Potion"},
                    {"reward_type": "POTION", "id": "Fire Potion"},
                ]
            }
        }

        decision = reward_policy(has_empty_potion_slot=False).decide_combat_reward(game)

        self.assertEqual(decision.actions, [{"action": "proceed"}])
        self.assertIn("Potion slots are full", decision.reason)

    def test_proceeds_when_rewards_are_empty(self):
        decision = reward_policy().decide_combat_reward({"screen_state": {"rewards": []}})

        self.assertEqual(decision.actions, [{"action": "proceed"}])

    def test_waits_once_for_empty_combat_reward_snapshot(self):
        policy = reward_policy()
        game = {
            "screen_type": "COMBAT_REWARD",
            "room_phase": "INCOMPLETE",
            "floor": 8,
            "screen_state": {"rewards": []},
        }

        first = policy.decide_combat_reward(game)
        second = policy.decide_combat_reward(game)

        self.assertEqual(first.actions, [{"action": "wait", "ms": 250}])
        self.assertIn("wait for reward snapshot", first.reason)
        self.assertEqual(second.actions, [{"action": "proceed"}])

    def test_empty_complete_combat_reward_proceeds_without_wait(self):
        game = {
            "screen_type": "COMBAT_REWARD",
            "room_phase": "COMPLETE",
            "floor": 8,
            "screen_state": {"rewards": []},
        }

        decision = reward_policy().decide_combat_reward(game)

        self.assertEqual(decision.actions, [{"action": "proceed"}])


if __name__ == "__main__":
    unittest.main()
