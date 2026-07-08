import unittest

from slay_ai.policy_shop import ShopRelicPolicy


class FakeMemory:
    def relic_score(self, name: str) -> float:
        return {"Runic Pyramid": 88, "Ectoplasm": 45}.get(name, 50)


def shop_policy() -> ShopRelicPolicy:
    return ShopRelicPolicy(
        FakeMemory(),  # type: ignore[arg-type]
        card_reward_score=lambda card, game: 0,
        should_purge_strike=lambda game: False,
        boss_prep_needs_potion=lambda game: False,
        has_empty_potion_slot=lambda game: True,
    )


class ShopRelicPolicyTests(unittest.TestCase):
    def test_shop_potion_choice_index_accounts_for_affordable_relics(self):
        game = {
            "gold": 160,
            "potions": [{"is_empty": True}],
            "screen_state": {
                "purge_available": False,
                "purge_cost": 100,
                "cards": [],
                "relics": [{"id": "Bag of Marbles", "name": "Bag of Marbles", "price": 150}],
                "potions": [{"id": "SwiftPotion", "name": "Swift Potion", "price": 50}],
            },
        }

        decision = shop_policy().decide_shop_screen(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("buy Swift Potion", decision.reason)

    def test_boss_reward_uses_memory_relic_score(self):
        game = {
            "screen_state": {
                "relics": [
                    {"id": "Ectoplasm", "name": "Ectoplasm"},
                    {"id": "Runic Pyramid", "name": "Runic Pyramid"},
                ]
            }
        }

        decision = shop_policy().decide_boss_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("Runic Pyramid", decision.reason)


if __name__ == "__main__":
    unittest.main()
