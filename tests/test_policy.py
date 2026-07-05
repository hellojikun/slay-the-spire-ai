import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.campaign import build_targets, load_progress
from slay_ai.combat_search import find_best_combat_sequence
from slay_ai.learn import read_log
from slay_ai.memory import StrategyMemory
from slay_ai.policy import HeuristicPolicy
from slay_ai.train_card_model import load_examples
from slay_ai.unlocks import read_unlocks


def policy():
    return HeuristicPolicy(StrategyMemory.load())


def isolated_policy(tmp: str):
    return HeuristicPolicy(
        StrategyMemory.load(
            learned_path=Path(tmp) / "learned.json",
            value_model_path=Path(tmp) / "card_model.json",
        )
    )


def act1_deck_without_premium_block() -> list[dict[str, str]]:
    return [
        {"id": "Strike_R"},
        {"id": "Strike_R"},
        {"id": "Strike_R"},
        {"id": "Strike_R"},
        {"id": "Defend_R"},
        {"id": "Defend_R"},
        {"id": "Defend_R"},
        {"id": "Bash"},
        {"id": "Twin Strike"},
        {"id": "Thunderclap"},
        {"id": "Heavy Blade"},
        {"id": "Battle Trance"},
    ]


class PolicyTests(unittest.TestCase):
    def test_card_reward_picks_highest_memory_score(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "floor": 2,
                "screen_state": {
                    "cards": [
                        {"name": "Clash"},
                        {"name": "Shrug It Off"},
                        {"name": "Flex"},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Shrug It Off")

    def test_card_reward_scores_localized_cards_by_id(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "floor": 1,
                "screen_state": {
                    "cards": [
                        {"name": "localized warcry", "id": "Warcry"},
                        {"name": "localized seeing red", "id": "Seeing Red"},
                        {"name": "localized perfected strike", "id": "Perfected Strike"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 3}])
        self.assertEqual(decision.learn_card_pick, "Perfected Strike")

    def test_card_reward_penalizes_exhaust_payoff_without_enablers(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "floor": 1,
                "class": "IRONCLAD",
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized feel no pain", "id": "Feel No Pain"},
                        {"name": "localized anger", "id": "Anger"},
                        {"name": "localized flex", "id": "Flex"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Anger")

    def test_card_reward_keeps_exhaust_payoff_with_enabler(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "floor": 1,
                "class": "IRONCLAD",
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "True Grit"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized feel no pain", "id": "Feel No Pain"},
                        {"name": "localized anger", "id": "Anger"},
                        {"name": "localized flex", "id": "Flex"},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.learn_card_pick, "Feel No Pain")

    def test_card_reward_prioritizes_block_stabilizer_when_act1_deck_lacks_defense(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 10,
                "class": "IRONCLAD",
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Clothesline"},
                    {"id": "Clothesline"},
                    {"id": "Anger"},
                    {"id": "Hemokinesis"},
                    {"id": "Whirlwind"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized power through", "id": "Power Through", "type": "SKILL"},
                        {"name": "localized rage", "id": "Rage", "type": "SKILL"},
                        {"name": "localized clothesline", "id": "Clothesline", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.learn_card_pick, "Power Through")

    def test_card_reward_does_not_penalize_first_early_attack_when_damage_is_needed(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 4,
                "class": "IRONCLAD",
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized armaments", "id": "Armaments", "type": "SKILL"},
                        {"name": "localized anger", "id": "Anger", "type": "ATTACK"},
                        {"name": "localized flex", "id": "Flex", "type": "SKILL"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Anger")

    def test_card_reward_penalizes_early_burning_pact_without_payoff(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 1,
                "class": "IRONCLAD",
                "current_hp": 75,
                "max_hp": 88,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized burning pact", "id": "Burning Pact", "type": "SKILL"},
                        {"name": "localized havoc", "id": "Havoc", "type": "SKILL"},
                        {"name": "localized thunderclap", "id": "Thunderclap", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 3}])
        self.assertEqual(decision.learn_card_pick, "Thunderclap")

    def test_card_reward_keeps_early_burning_pact_with_exhaust_payoff(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 3,
                "class": "IRONCLAD",
                "current_hp": 75,
                "max_hp": 88,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Feel No Pain"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized burning pact", "id": "Burning Pact", "type": "SKILL"},
                        {"name": "localized thunderclap", "id": "Thunderclap", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.learn_card_pick, "Burning Pact")

    def test_card_reward_low_hp_prefers_early_survival_card_over_pure_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 3,
                "class": "IRONCLAD",
                "current_hp": 37,
                "max_hp": 88,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Burning Pact"},
                    {"id": "Inflame"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized body slam", "id": "Body Slam", "type": "ATTACK"},
                        {"name": "localized iron wave", "id": "Iron Wave", "type": "ATTACK"},
                        {"name": "localized pummel", "id": "Pummel", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Iron Wave")

    def test_card_reward_penalizes_second_exhaust_enabler_without_payoff(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 2,
                "class": "IRONCLAD",
                "current_hp": 82,
                "max_hp": 88,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "True Grit"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized flame barrier", "id": "Flame Barrier", "type": "SKILL"},
                        {"name": "localized second wind", "id": "Second Wind", "type": "SKILL"},
                        {"name": "localized pommel strike", "id": "Pommel Strike", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.learn_card_pick, "Flame Barrier")

    def test_card_reward_penalizes_third_exhaust_enabler_without_payoff(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 3,
                "class": "IRONCLAD",
                "current_hp": 88,
                "max_hp": 88,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "True Grit"},
                    {"id": "Second Wind"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized bloodletting", "id": "Bloodletting", "type": "SKILL"},
                        {"name": "localized perfected strike", "id": "Perfected Strike", "type": "ATTACK"},
                        {"name": "localized fiend fire", "id": "Fiend Fire", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Perfected Strike")

    def test_card_reward_probe77_prefers_boss_frontload_over_unsupported_dark_embrace(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 7,
                "class": "IRONCLAD",
                "current_hp": 80,
                "max_hp": 80,
                "potions": [],
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Cleave"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized dark embrace", "id": "Dark Embrace", "type": "POWER"},
                        {"name": "localized dropkick", "id": "Dropkick", "type": "ATTACK"},
                        {"name": "localized flex", "id": "Flex", "type": "SKILL"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Dropkick")

    def test_card_reward_probe78_prefers_first_act1_aoe_over_extra_block(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 6,
                "class": "IRONCLAD",
                "current_hp": 55,
                "max_hp": 80,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Anger"},
                    {"id": "Hemokinesis"},
                    {"id": "True Grit"},
                    {"id": "Bloodletting"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized flame barrier", "id": "Flame Barrier", "type": "SKILL"},
                        {"name": "localized cleave", "id": "Cleave", "type": "ATTACK"},
                        {"name": "localized pommel strike", "id": "Pommel Strike", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Cleave")

    def test_card_reward_keeps_premium_block_when_act1_aoe_is_already_covered(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "CARD_REWARD",
                "act": 1,
                "floor": 6,
                "class": "IRONCLAD",
                "current_hp": 55,
                "max_hp": 80,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Cleave"},
                ],
                "screen_state": {
                    "cards": [
                        {"name": "localized flame barrier", "id": "Flame Barrier", "type": "SKILL"},
                        {"name": "localized thunderclap", "id": "Thunderclap", "type": "ATTACK"},
                        {"name": "localized pommel strike", "id": "Pommel Strike", "type": "ATTACK"},
                    ]
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.learn_card_pick, "Flame Barrier")

    def test_shop_room_enters_shop(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_ROOM",
                "floor": 5,
                "current_hp": 67,
                "max_hp": 88,
                "gold": 156,
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_shop_screen_buys_premium_card_by_available_choice_index(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "act": 1,
                "floor": 5,
                "current_hp": 67,
                "max_hp": 88,
                "gold": 100,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "purge_available": True,
                    "purge_cost": 75,
                    "cards": [
                        {"name": "localized flex", "id": "Flex", "type": "SKILL", "price": 40},
                        {"name": "localized flame barrier", "id": "Flame Barrier", "type": "SKILL", "price": 75},
                    ],
                    "relics": [],
                    "potions": [],
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 3}])

    def test_shop_screen_purges_strike_when_no_better_purchase(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "act": 1,
                "floor": 5,
                "current_hp": 67,
                "max_hp": 88,
                "gold": 100,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "purge_available": True,
                    "purge_cost": 75,
                    "cards": [
                        {"name": "localized flex", "id": "Flex", "type": "SKILL", "price": 40},
                    ],
                    "relics": [],
                    "potions": [],
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_shop_screen_buys_elite_potion_after_probe58_purge(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "act": 1,
                "floor": 3,
                "current_hp": 76,
                "max_hp": 80,
                "gold": 154,
                "potions": [
                    {"is_empty": True},
                    {"is_empty": True},
                    {"is_empty": True},
                ],
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Shrug It Off"},
                ],
                "screen_state": {
                    "purge_available": False,
                    "purge_cost": 100,
                    "cards": [],
                    "relics": [],
                    "potions": [
                        {"name": "Swift Potion", "id": "Swift Potion", "price": 50},
                        {"name": "Regen Potion", "id": "Regen Potion", "price": 77},
                        {"name": "Duplication Potion", "id": "DuplicationPotion", "price": 78},
                    ],
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertIn("buy Swift Potion", decision.reason)

    def test_shop_screen_boss_prep_potion_can_beat_purge(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "act": 1,
                "floor": 7,
                "current_hp": 80,
                "max_hp": 80,
                "gold": 111,
                "potions": [
                    {"is_empty": True},
                    {"is_empty": True},
                    {"is_empty": True},
                ],
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Cleave"},
                ],
                "screen_state": {
                    "purge_available": True,
                    "purge_cost": 75,
                    "cards": [],
                    "relics": [],
                    "potions": [
                        {"name": "Fire Potion", "id": "Fire Potion", "price": 50},
                    ],
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("buy Fire Potion", decision.reason)

    def test_shop_screen_cancels_without_high_confidence_purchase(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "act": 1,
                "floor": 5,
                "current_hp": 67,
                "max_hp": 88,
                "gold": 45,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                ],
                "screen_state": {
                    "purge_available": True,
                    "purge_cost": 75,
                    "cards": [
                        {"name": "localized flex", "id": "Flex", "type": "SKILL", "price": 40},
                    ],
                    "relics": [],
                    "potions": [],
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "cancel"}])

    def test_shop_screen_waits_for_inventory_snapshot_before_leaving(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "act": 1,
                "floor": 13,
                "current_hp": 59,
                "max_hp": 80,
                "gold": 239,
                "screen_state": {
                    "cards": [],
                    "relics": [],
                    "potions": [],
                    "purge_available": None,
                    "purge_cost": None,
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_reward_skips_potion_when_slots_are_full(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "COMBAT_REWARD",
                "potions": [
                    {"id": "Strength Potion", "can_use": True},
                    {"id": "Fire Potion", "can_use": True},
                    {"id": "Blood Potion", "can_use": True},
                ],
                "screen_state": {
                    "rewards": [
                        {"reward_type": "POTION", "id": "Dexterity Potion"},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "proceed"}])

    def test_combat_blocks_when_under_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 35, "max_hp": 80, "current_energy": 1},
                    "hand": [
                        {"name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Cultist", "current_hp": 40, "max_hp": 40, "move": {"damage": 12}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_blocks_at_low_hp_even_with_reduced_block(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 23, "max_hp": 88, "current_energy": 1},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 4, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Lagavulin", "current_hp": 43, "max_hp": 109, "move": {"damage": 20}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_prioritizes_block_when_low_hp_and_attack_does_not_reduce_danger(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 36, "max_hp": 88, "current_energy": 2, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Bash", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 8, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Red Slaver", "current_hp": 44, "max_hp": 48, "move": {"damage": 20}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_allows_kill_when_it_removes_dangerous_incoming(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 18, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Louse", "current_hp": 6, "max_hp": 12, "move": {"damage": 18}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2, "target_index": 1}])

    def test_combat_avoids_basic_defend_against_gremlin_nob_when_not_lethal(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 65, "max_hp": 80, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "id": "GremlinNob", "current_hp": 56, "max_hp": 82, "move": {"damage": 14}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_blocks_against_gremlin_nob_when_incoming_is_lethal(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 12, "max_hp": 80, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "id": "GremlinNob", "current_hp": 26, "max_hp": 82, "move": {"damage": 20}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_chooses_one_safe_action_per_state_read(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 70, "max_hp": 80, "current_energy": 3},
                    "hand": [
                        {"name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Cultist", "current_hp": 40, "max_hp": 40, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0]["action"], "play_card")

    def test_combat_avoids_offering_in_probe60_byrds_setup(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "act": 2,
                "floor": 20,
                "current_hp": 49,
                "max_hp": 80,
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 49, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Shrug It Off", "id": "Shrug It Off", "type": "SKILL", "cost": 1, "block": 11, "is_playable": True},
                        {"name": "Offering", "id": "Offering", "type": "SKILL", "cost": 0, "exhausts": True, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Offering", "id": "Offering", "type": "SKILL", "cost": 0, "exhausts": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Byrd", "id": "Byrd", "current_hp": 28, "max_hp": 28, "intent": "BUFF", "move": None, "powers": [{"id": "Flight", "amount": 3}]},
                        {"name": "Byrd", "id": "Byrd", "current_hp": 31, "max_hp": 31, "intent": "BUFF", "move": None, "powers": [{"id": "Flight", "amount": 3}]},
                        {"name": "Byrd", "id": "Byrd", "current_hp": 28, "max_hp": 28, "intent": "BUFF", "move": None, "powers": [{"id": "Flight", "amount": 3}]},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])

    def test_combat_avoids_second_offering_after_turn_activity(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "act": 2,
                "floor": 20,
                "current_hp": 49,
                "max_hp": 80,
                "combat_state": {
                    "turn": 1,
                    "cards_played_this_turn": 1,
                    "player": {"current_hp": 49, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Offering", "id": "Offering", "type": "SKILL", "cost": 0, "exhausts": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Byrd", "id": "Byrd", "current_hp": 28, "max_hp": 28, "intent": "BUFF", "move": None},
                        {"name": "Byrd", "id": "Byrd", "current_hp": 31, "max_hp": 31, "intent": "BUFF", "move": None},
                        {"name": "Byrd", "id": "Byrd", "current_hp": 28, "max_hp": 28, "intent": "BUFF", "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertNotEqual(decision.actions, [{"action": "play_card", "card_index": 2}])

    def test_combat_can_play_offering_when_safe(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "act": 2,
                "floor": 20,
                "current_hp": 80,
                "max_hp": 80,
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 80, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Shrug It Off", "id": "Shrug It Off", "type": "SKILL", "cost": 1, "block": 11, "is_playable": True},
                        {"name": "Offering", "id": "Offering", "type": "SKILL", "cost": 0, "exhausts": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Byrd", "id": "Byrd", "current_hp": 28, "max_hp": 28, "intent": "BUFF", "move": None},
                        {"name": "Byrd", "id": "Byrd", "current_hp": 31, "max_hp": 31, "intent": "BUFF", "move": None},
                        {"name": "Byrd", "id": "Byrd", "current_hp": 28, "max_hp": 28, "intent": "BUFF", "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 3}])

    def test_combat_prefers_strike_over_single_target_x_cost_when_energy_would_be_wasted(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 39,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 39, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Whirlwind", "id": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 5, "has_target": False, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "Gremlin Nob",
                            "id": "GremlinNob",
                            "current_hp": 78,
                            "max_hp": 84,
                            "move": {"damage": 8},
                            "powers": [{"id": "Anger", "amount": 2}],
                        },
                    ],
                },
            },
        }
        with TemporaryDirectory() as tmp:
            decision = isolated_policy(tmp).decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_reserves_block_energy_before_probe59_slime_split_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 35,
                "max_hp": 88,
                "combat_state": {
                    "turn": 8,
                    "player": {"current_hp": 35, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Bash", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Whirlwind", "id": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 6, "has_target": False, "is_playable": True},
                        {"name": "True Grit", "id": "True Grit", "type": "SKILL", "cost": 1, "block": 5, "exhausts": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Spike Slime", "id": "SpikeSlime_L", "current_hp": 62, "max_hp": 62, "move": {"damage": 18}},
                        {"name": "Acid Slime", "id": "AcidSlime_L", "current_hp": 42, "max_hp": 62, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 3}])

    def test_combat_avoids_probe64_shallow_slime_boss_split_without_followup(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 76,
                "max_hp": 88,
                "combat_state": {
                    "turn": 4,
                    "player": {"current_hp": 76, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Pommel Strike", "id": "Pommel Strike", "type": "ATTACK", "cost": 1, "damage": 13, "is_playable": True, "has_target": True},
                        {"name": "Slimed", "id": "Slimed", "type": "STATUS", "cost": 1, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Slime Boss", "id": "SlimeBoss", "current_hp": 80, "max_hp": 140, "move": None},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_allows_slime_split_to_stop_current_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 47,
                "max_hp": 88,
                "combat_state": {
                    "player": {"current_hp": 47, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 8, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {"name": "Acid Slime", "id": "AcidSlime_L", "current_hp": 38, "max_hp": 70, "move": {"damage": 12}},
                        {"name": "Louse", "current_hp": 12, "max_hp": 12, "move": {"damage": 6}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_does_not_play_zero_energy_x_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 82,
                "max_hp": 88,
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 82, "max_hp": 88, "current_energy": 0, "block": 22},
                    "hand": [
                        {"name": "Whirlwind", "id": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 5, "is_playable": True, "has_target": False},
                        {"name": "Slimed", "id": "Slimed", "type": "STATUS", "cost": 1, "is_playable": False},
                    ],
                    "monsters": [
                        {"name": "Slime Boss", "id": "SlimeBoss", "current_hp": 98, "max_hp": 140, "move": {"damage": 28}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_plays_seeing_red_before_probe48_hexaghost_x_cost_setup(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 71,
                "max_hp": 88,
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 71, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Heavy Blade", "id": "Heavy Blade", "type": "ATTACK", "cost": 2, "damage": 14, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Whirlwind", "id": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 8, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Seeing Red", "id": "Seeing Red", "type": "SKILL", "cost": 1, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 250, "max_hp": 250, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 5}])

    def test_combat_plays_seeing_red_when_it_enables_expensive_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 50,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 50, "max_hp": 80, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Seeing Red", "id": "Seeing Red", "type": "SKILL", "cost": 1, "is_playable": True},
                        {"name": "Heavy Blade", "id": "Heavy Blade", "type": "ATTACK", "cost": 2, "damage": 14, "is_playable": False},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 180, "max_hp": 250, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_does_not_play_seeing_red_into_gremlin_nob(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 60,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 60, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Seeing Red", "id": "Seeing Red", "type": "SKILL", "cost": 1, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "id": "GremlinNob", "current_hp": 60, "max_hp": 85, "move": {"damage": 18}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2, "target_index": 1}])

    def test_combat_local_search_blocks_with_two_cards_to_avoid_lethal(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 2, "current_hp": 10, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [{"name": "Jaw Worm", "current_hp": 40, "intent_damage": 16}],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_local_search_counts_move_damage_hits(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 2, "current_hp": 10, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [{"name": "Hexaghost", "current_hp": 40, "move": {"damage": 8, "hits": 2}}],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_local_search_triggers_guardian_mode_shift_to_stop_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 40,
                "max_hp": 88,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_energy": 1, "current_hp": 40, "max_hp": 88, "block": 0},
                    "hand": [
                        {"id": "Reckless Charge", "name": "Reckless Charge", "type": "ATTACK", "cost": 0, "damage": 7, "is_playable": True, "has_target": True},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 222,
                            "max_hp": 240,
                            "block": 1,
                            "move": {"damage": 36},
                            "powers": [{"id": "Mode Shift", "amount": 12}],
                        }
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_guardian_high_pressure_draws_before_spending_block_energy(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 11,
                "max_hp": 80,
                "combat_state": {
                    "turn": 8,
                    "player": {"current_energy": 3, "current_hp": 11, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Flame Barrier", "name": "Flame Barrier", "type": "SKILL", "cost": 2, "block": 12, "is_playable": True},
                        {"id": "Anger", "name": "Anger", "type": "ATTACK", "cost": 0, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "Anger", "name": "Anger", "type": "ATTACK", "cost": 0, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Battle Trance", "name": "Battle Trance", "type": "SKILL", "cost": 0, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 138,
                            "max_hp": 240,
                            "block": 9,
                            "move": {"damage": 36},
                            "powers": [{"id": "Mode Shift", "amount": 10}],
                        }
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 5}])
        self.assertIn("draw before spending energy", decision.reason)

    def test_combat_guardian_draw_setup_respects_no_draw_power(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 11,
                "max_hp": 80,
                "combat_state": {
                    "turn": 8,
                    "player": {
                        "current_energy": 3,
                        "current_hp": 11,
                        "max_hp": 80,
                        "block": 0,
                        "powers": [{"id": "No Draw", "amount": -1}],
                    },
                    "hand": [
                        {"id": "Flame Barrier", "name": "Flame Barrier", "type": "SKILL", "cost": 2, "block": 12, "is_playable": True},
                        {"id": "Battle Trance", "name": "Battle Trance", "type": "SKILL", "cost": 0, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 138,
                            "max_hp": 240,
                            "block": 9,
                            "move": {"damage": 36},
                            "powers": [{"id": "Mode Shift", "amount": 10}],
                        }
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_guardian_search_commits_attack_followup_for_mode_shift(self):
        first_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 42,
                "max_hp": 80,
                "combat_state": {
                    "turn": 11,
                    "player": {"current_energy": 3, "current_hp": 42, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Whirlwind", "name": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 8, "is_playable": True, "has_target": False},
                        {"id": "Wound", "name": "Wound", "type": "STATUS", "cost": -2, "is_playable": False},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Bash", "name": "Bash", "type": "ATTACK", "cost": 2, "damage": 10, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 114,
                            "max_hp": 240,
                            "block": 9,
                            "move": {"damage": 36},
                            "powers": [{"id": "Mode Shift", "amount": 41}],
                        }
                    ],
                },
            },
        }
        second_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 42,
                "max_hp": 80,
                "combat_state": {
                    "turn": 11,
                    "player": {"current_energy": 2, "current_hp": 42, "max_hp": 80, "block": 5},
                    "hand": [
                        {"id": "Whirlwind", "name": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 8, "is_playable": True, "has_target": False},
                        {"id": "Wound", "name": "Wound", "type": "STATUS", "cost": -2, "is_playable": False},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "Bash", "name": "Bash", "type": "ATTACK", "cost": 2, "damage": 10, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 114,
                            "max_hp": 240,
                            "block": 9,
                            "move": {"damage": 36},
                            "powers": [{"id": "Mode Shift", "amount": 41}],
                        }
                    ],
                },
            },
        }
        test_policy = policy()

        first_decision = test_policy.decide(first_state)
        second_decision = test_policy.decide(second_state)

        self.assertEqual(first_decision.actions, [{"action": "play_card", "card_index": 4}])
        self.assertIn("One-turn search", first_decision.reason)
        self.assertEqual(second_decision.actions, [{"action": "play_card", "card_index": 1}])
        self.assertIn("Continue one-turn search", second_decision.reason)

    def test_combat_local_search_ignores_spurious_non_attack_damage_after_probe69(self):
        game = {
            "current_hp": 44,
            "max_hp": 80,
            "combat_state": {
                "turn": 7,
                "player": {"current_hp": 44, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"name": "Battle Trance", "id": "Battle Trance", "type": "SKILL", "cost": 0, "damage": 1, "is_playable": True},
                    {"name": "Headbutt", "id": "Headbutt", "type": "ATTACK", "cost": 1, "damage": 11, "is_playable": True, "has_target": True},
                    {"name": "Twin Strike", "id": "Twin Strike", "type": "ATTACK", "cost": 1, "damage": 7, "is_playable": True, "has_target": True},
                    {"name": "Slimed", "id": "Slimed", "type": "STATUS", "cost": 1, "damage": 1, "is_playable": True},
                    {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "damage": 1, "block": 4, "is_playable": True},
                ],
                "monsters": [
                    {"name": "Spike Slime", "id": "SpikeSlime_L", "current_hp": 50, "max_hp": 60, "move": {"damage": 18}},
                    {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 20, "max_hp": 30, "move": {"damage": 12}},
                    {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 12, "max_hp": 30, "move": None},
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 5})
        self.assertNotIn({"action": "play_card", "card_index": 1}, result.sequence)
        self.assertNotIn({"action": "play_card", "card_index": 4}, result.sequence)

    def test_combat_accepts_probe69_defend_under_extreme_slime_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 44,
                "max_hp": 80,
                "act": 1,
                "floor": 16,
                "combat_state": {
                    "turn": 7,
                    "player": {"current_hp": 44, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Battle Trance", "id": "Battle Trance", "type": "SKILL", "cost": 0, "damage": 1, "is_playable": True},
                        {"name": "Headbutt", "id": "Headbutt", "type": "ATTACK", "cost": 1, "damage": 11, "is_playable": True, "has_target": True},
                        {"name": "Twin Strike", "id": "Twin Strike", "type": "ATTACK", "cost": 1, "damage": 7, "is_playable": True, "has_target": True},
                        {"name": "Slimed", "id": "Slimed", "type": "STATUS", "cost": 1, "damage": 1, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "damage": 1, "block": 4, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Spike Slime", "id": "SpikeSlime_L", "current_hp": 50, "max_hp": 60, "move": {"damage": 18}},
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 20, "max_hp": 30, "move": {"damage": 12}},
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 12, "max_hp": 30, "move": None},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 5}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_search_models_probe71_clothesline_weak_under_lethal_hexaghost(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "act": 1,
                "current_hp": 17,
                "max_hp": 80,
                "combat_state": {
                    "turn": 9,
                    "player": {"current_hp": 17, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Clothesline", "id": "Clothesline", "type": "ATTACK", "cost": 2, "damage": 12, "is_playable": True, "has_target": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "True Grit", "id": "True Grit", "type": "SKILL", "cost": 1, "block": 7, "is_playable": True},
                        {"name": "Wild Strike", "id": "Wild Strike", "type": "ATTACK", "cost": 1, "damage": 12, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {
                            "name": "Hexaghost",
                            "id": "Hexaghost",
                            "current_hp": 66,
                            "max_hp": 250,
                            "move": {"hits": 6, "damage": 5},
                            "powers": [{"id": "Strength", "amount": 2}],
                        }
                    ],
                },
            },
        }

        result = find_best_combat_sequence(state["game_state"])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertLess(result.projected_loss, 17)
        self.assertEqual(result.first_card_key, "Clothesline")

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_search_counts_probe81_burn_end_turn_damage(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "act": 1,
                "current_hp": 6,
                "max_hp": 80,
                "combat_state": {
                    "turn": 15,
                    "player": {"current_hp": 6, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Bash+", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 10, "is_playable": True, "has_target": True},
                        {"name": "Burn+", "id": "Burn", "type": "STATUS", "cost": -2, "is_playable": False},
                        {"name": "Burn+", "id": "Burn", "type": "STATUS", "cost": -2, "is_playable": False},
                        {"name": "Uppercut", "id": "Uppercut", "type": "ATTACK", "cost": 2, "damage": 13, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {
                            "name": "Hexaghost",
                            "id": "Hexaghost",
                            "current_hp": 77,
                            "max_hp": 250,
                            "move": {"damage": 8},
                            "powers": [{"id": "Strength", "amount": 2}],
                        }
                    ],
                },
            },
        }

        result = find_best_combat_sequence(state["game_state"])

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 16)
        self.assertGreaterEqual(result.projected_loss, 9)
        self.assertFalse(result.avoided_lethal)

        decision = policy().decide(state)

        self.assertNotIn("One-turn search", decision.reason)

    def test_combat_search_counts_probe82_gremlin_nob_rage_from_skills(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 6,
                "act": 1,
                "current_hp": 20,
                "max_hp": 80,
                "combat_state": {
                    "turn": 4,
                    "player": {
                        "current_hp": 20,
                        "max_hp": 80,
                        "current_energy": 3,
                        "block": 0,
                        "powers": [{"amount": 1, "id": "Vulnerable"}],
                    },
                    "hand": [
                        {"name": "Headbutt", "id": "Headbutt", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True, "has_target": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Cleave", "id": "Cleave", "type": "ATTACK", "cost": 1, "damage": 8, "is_playable": True, "has_target": None},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {
                            "name": "Gremlin Nob",
                            "id": "GremlinNob",
                            "current_hp": 34,
                            "max_hp": 85,
                            "move": {"damage": 24},
                            "powers": [{"amount": 1, "id": "Vulnerable"}, {"amount": 2, "id": "Anger"}],
                        }
                    ],
                },
            },
        }

        result = find_best_combat_sequence(state["game_state"])

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Defend_R", "Defend_R", "Headbutt"))
        self.assertEqual(result.initial_loss, 24)
        self.assertEqual(result.projected_loss, 20)
        self.assertFalse(result.avoided_lethal)

        decision = policy().decide(state)

        self.assertNotIn("One-turn search", decision.reason)

    def test_combat_commits_defensive_search_sequence_across_state_reads(self):
        bot = policy()
        monsters = [
            {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 250, "max_hp": 250, "move": {"hits": 6, "damage": 6}},
        ]
        first_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 65,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 65, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Power Through", "id": "Power Through", "type": "SKILL", "cost": 1, "block": 15, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": monsters,
                },
            },
        }
        second_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 65,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 65, "max_hp": 80, "current_energy": 2, "block": 15},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": monsters,
                },
            },
        }
        third_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 65,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 65, "max_hp": 80, "current_energy": 1, "block": 20},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": monsters,
                },
            },
        }

        first = bot.decide(first_state)
        second = bot.decide(second_state)
        third = bot.decide(third_state)

        self.assertEqual(first.actions, [{"action": "play_card", "card_index": 1}])
        self.assertIn("One-turn search", first.reason)
        self.assertEqual(second.actions, [{"action": "play_card", "card_index": 1}])
        self.assertIn("Continue one-turn search", second.reason)
        self.assertEqual(third.actions, [{"action": "play_card", "card_index": 1}])
        self.assertIn("Continue one-turn search", third.reason)

    def test_combat_search_sequence_commitment_clears_on_new_turn(self):
        bot = policy()
        monsters = [
            {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 250, "max_hp": 250, "move": {"hits": 6, "damage": 6}},
        ]
        first_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 65,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 65, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Power Through", "id": "Power Through", "type": "SKILL", "cost": 1, "block": 15, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": monsters,
                },
            },
        }
        next_turn_state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "current_hp": 65,
                "max_hp": 80,
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 65, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [{"name": "Hexaghost", "id": "Hexaghost", "current_hp": 250, "max_hp": 250, "move": None}],
                },
            },
        }

        bot.decide(first_state)
        decision = bot.decide(next_turn_state)

        self.assertNotIn("Continue one-turn search", decision.reason)

    def test_combat_search_blocks_early_sentries_dazed_pressure_from_probe73(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "act": 1,
                "floor": 6,
                "current_hp": 63,
                "max_hp": 88,
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 63, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Second Wind", "id": "Second Wind", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"name": "Battle Trance", "id": "Battle Trance", "type": "SKILL", "cost": 0, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {"name": "Sentry", "id": "Sentry", "current_hp": 38, "max_hp": 38, "move": None},
                        {"name": "Sentry", "id": "Sentry", "current_hp": 41, "max_hp": 41, "move": {"damage": 10}},
                        {"name": "Sentry", "id": "Sentry", "current_hp": 38, "max_hp": 38, "move": None},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_local_search_continues_block_after_probe59_impervious(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 58,
                "max_hp": 88,
                "combat_state": {
                    "turn": 3,
                    "player": {"current_energy": 1, "current_hp": 58, "max_hp": 88, "block": 30},
                    "hand": [
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "Anger", "name": "Anger", "type": "ATTACK", "cost": 0, "damage": 6, "is_playable": True, "has_target": True},
                        {"id": "True Grit", "name": "True Grit", "type": "SKILL", "cost": 1, "block": 7, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Slime Boss", "id": "SlimeBoss", "current_hp": 118, "max_hp": 140, "move": {"damage": 38}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_local_search_plays_energy_setup_before_x_cost_sequence(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 1, "current_hp": 40, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Seeing Red", "name": "Seeing Red", "type": "SKILL", "cost": 1, "is_playable": True},
                        {
                            "id": "Whirlwind",
                            "name": "Whirlwind",
                            "type": "ATTACK",
                            "cost": -1,
                            "damage": 5,
                            "is_playable": True,
                            "has_target": False,
                        },
                    ],
                    "monsters": [
                        {"name": "Slaver", "current_hp": 18, "intent_damage": 8},
                        {"name": "Slaver", "current_hp": 18, "intent_damage": 8},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])
        self.assertIn("One-turn search", decision.reason)

    def test_combat_local_search_does_not_override_disarm_without_lethal_gain(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 2, "current_hp": 70, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {
                            "id": "Disarm",
                            "name": "Disarm",
                            "type": "SKILL",
                            "cost": 1,
                            "has_target": True,
                            "is_playable": True,
                        },
                    ],
                    "monsters": [{"name": "Lagavulin", "current_hp": 100, "intent_damage": 18}],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 3, "target_index": 1}])
        self.assertNotIn("One-turn search", decision.reason)

    def test_combat_local_search_does_not_sequence_skills_into_gremlin_nob(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 2, "current_hp": 65, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [{"name": "Gremlin Nob", "id": "Gremlin Nob", "current_hp": 80, "intent_damage": 14}],
                },
            },
        }
        decision = policy().decide(state)
        self.assertNotIn("One-turn search", decision.reason)
        self.assertNotEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_local_search_does_not_plan_through_second_wind_hand_change(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 3, "current_hp": 40, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Second Wind", "name": "Second Wind", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [{"name": "Jaw Worm", "current_hp": 40, "intent_damage": 12}],
                },
            },
        }
        decision = policy().decide(state)
        self.assertNotIn("One-turn search", decision.reason)

    def test_combat_local_search_does_not_open_with_reflect_lethal_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_energy": 2, "current_hp": 2, "max_hp": 80, "block": 0},
                    "hand": [
                        {"id": "Strike_R", "name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"id": "Defend_R", "name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "Guardian",
                            "current_hp": 40,
                            "intent_damage": 10,
                            "powers": [{"id": "Thorns", "amount": 3}],
                        }
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertNotIn("One-turn search", decision.reason)
        self.assertNotEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_local_search_counts_hemokinesis_self_damage(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 2,
            "max_hp": 80,
            "combat_state": {
                "turn": 2,
                "player": {"current_hp": 2, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {"name": "Hemokinesis", "id": "Hemokinesis", "type": "ATTACK", "cost": 1, "damage": 14, "is_playable": True},
                    {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                ],
                "monsters": [
                    {"name": "Red Slaver", "id": "SlaverRed", "current_hp": 19, "max_hp": 48, "move": None},
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertNotEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 1})

    def test_combat_local_search_ignores_spurious_attack_block_after_speed_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 44,
                "max_hp": 88,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 44, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 15, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "block": 9, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 15, "is_playable": True},
                        {"name": "Thunderclap", "id": "Thunderclap", "type": "ATTACK", "cost": 1, "damage": 4, "block": 9, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "block": 9, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Lagavulin", "id": "Lagavulin", "current_hp": 105, "max_hp": 109, "move": {"damage": 18}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions[0]["action"], "play_card")
        self.assertIn(decision.actions[0]["card_index"], {1, 3})

    def test_combat_waits_for_hand_to_be_dealt(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 80, "max_hp": 80, "current_energy": 3},
                    "hand": [],
                    "monsters": [
                        {"name": "Jaw Worm", "current_hp": 1, "max_hp": 40, "move": {"damage": 12}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_ends_turn_when_hand_is_empty_after_actions(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 31, "max_hp": 80, "current_energy": 0},
                    "hand": [],
                    "monsters": [
                        {"name": "Acid Slime", "current_hp": 3, "max_hp": 30, "move": {"damage": 12}},
                        {"name": "Looter", "current_hp": 27, "max_hp": 46, "move": {"damage": 11}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_ends_turn_when_empty_hand_block_already_covers_incoming(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 56, "max_hp": 88, "current_energy": 2, "block": 30},
                    "hand": [],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 250, "max_hp": 250, "move": {"hits": 6, "damage": 3}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_ends_turn_when_empty_hand_after_spending_energy(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 82, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [],
                    "monsters": [
                        {"name": "Fungi Beast", "id": "FungiBeast", "current_hp": 19, "max_hp": 27, "move": {"damage": 15}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_ends_turn_when_first_turn_empty_hand_after_spending_energy(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 94, "max_hp": 94, "current_energy": 1, "block": 0},
                    "hand": [],
                    "monsters": [
                        {"name": "Shelled Parasite", "id": "Shelled Parasite", "current_hp": 56, "max_hp": 70, "move": {"hits": 2, "damage": 7}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_waits_on_first_turn_empty_hand_even_without_energy(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 88, "max_hp": 88, "current_energy": 0},
                    "hand": [],
                    "monsters": [
                        {"name": "Louse", "current_hp": 12, "max_hp": 12, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_waits_when_new_turn_hand_is_not_dealt_yet(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 76, "max_hp": 88, "current_energy": 3},
                    "hand": [],
                    "monsters": [
                        {"name": "Louse", "current_hp": 12, "max_hp": 12, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_waits_when_monsters_are_gone_during_transition(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 20, "max_hp": 80, "current_energy": 0},
                    "hand": [],
                    "monsters": [],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_waits_with_cards_when_monsters_are_gone(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 20, "max_hp": 80, "current_energy": 2},
                    "hand": [
                        {"name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_does_not_use_emergency_potion_without_monsters(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 8,
                "max_hp": 80,
                "potions": [
                    {"id": "Blood Potion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 8, "max_hp": 80, "current_energy": 0},
                    "hand": [],
                    "monsters": [],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "wait", "ms": 250}])

    def test_combat_uses_regen_potion_at_low_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 10,
                "max_hp": 80,
                "potions": [
                    {"id": "Regen Potion", "name": "鍐嶇敓鑽按", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 10, "max_hp": 80, "current_energy": 3},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Jaw Worm", "current_hp": 40, "max_hp": 40, "move": {"damage": 11}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_strength_potion_when_dangerously_low(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 11,
                "max_hp": 80,
                "potions": [
                    {"id": "Strength Potion", "name": "鍔涢噺鑽按", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 11, "max_hp": 80, "current_energy": 3},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Lagavulin", "current_hp": 77, "max_hp": 109, "move": {"damage": 20}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_essence_of_steel_when_dangerously_low(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 13,
                "max_hp": 88,
                "potions": [
                    {"id": "EssenceOfSteel", "name": "閽箣绮惧崕", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 13, "max_hp": 88, "current_energy": 2},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Lagavulin", "current_hp": 23, "max_hp": 109, "move": {"damage": 20}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_blood_potion_before_non_healing_potions_at_low_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 8,
                "max_hp": 88,
                "potions": [
                    {"id": "EntropicBrew", "can_use": True},
                    {"id": "ColorlessPotion", "can_use": True},
                    {"id": "BloodPotion", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 8, "max_hp": 88, "current_energy": 2},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Sentry", "current_hp": 40, "max_hp": 40, "move": {"damage": 10}},
                        {"name": "Sentry", "current_hp": 27, "max_hp": 40, "move": {"damage": 10}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 3}])

    def test_combat_does_not_spend_blood_potion_when_hp_is_safe(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 60,
                "max_hp": 88,
                "potions": [
                    {"id": "BloodPotion", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 60, "max_hp": 88, "current_energy": 1},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Cultist", "current_hp": 40, "max_hp": 40, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_uses_heart_of_iron_when_incoming_is_lethal(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 23,
                "max_hp": 88,
                "potions": [
                    {"id": "HeartOfIron", "can_use": True},
                    {"id": "LiquidBronze", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 23, "max_hp": 88, "current_energy": 2},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "id": "GremlinNob", "current_hp": 26, "max_hp": 85, "move": {"damage": 39}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_block_potion_for_large_hexaghost_hit(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 70,
                "max_hp": 95,
                "potions": [
                    {"id": "Block Potion", "can_use": True},
                    {"id": "Energy Potion", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 70, "max_hp": 95, "current_energy": 0, "block": 8},
                    "hand": [
                        {"name": "Seeing Red", "id": "Seeing Red", "type": "SKILL", "cost": 0, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 211, "max_hp": 250, "move": {"hits": 6, "damage": 7}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_does_not_use_block_potion_at_low_hp_without_incoming(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 26,
                "max_hp": 95,
                "potions": [
                    {"id": "Block Potion", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 26, "max_hp": 95, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Bash", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 8, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 181, "max_hp": 250, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_uses_liquid_bronze_when_dangerously_low(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 23,
                "max_hp": 88,
                "potions": [
                    {"id": "LiquidBronze", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 23, "max_hp": 88, "current_energy": 2},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "id": "GremlinNob", "current_hp": 26, "max_hp": 85, "move": {"damage": 39}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_snecko_oil_as_emergency_tempo(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 11,
                "max_hp": 88,
                "potions": [
                    {"id": "SneckoOil", "can_use": True},
                ],
                "combat_state": {
                    "player": {"current_hp": 11, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Shrug It Off", "id": "Shrug It Off", "type": "SKILL", "cost": 1, "block": 6, "is_playable": True},
                        {"name": "Carnage", "id": "Carnage", "type": "ATTACK", "cost": 2, "damage": 20, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Spike Slime", "id": "SpikeSlime_L", "current_hp": 58, "max_hp": 62, "move": {"damage": 18}},
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 19, "max_hp": 23, "move": {"damage": 12}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_cultist_potion_early_in_long_boss_fight(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 67,
                "max_hp": 95,
                "potions": [
                    {"id": "CultistPotion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 67, "max_hp": 95, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Perfected Strike", "id": "Perfected Strike", "type": "ATTACK", "cost": 2, "damage": 18, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "The Guardian", "id": "TheGuardian", "current_hp": 240, "max_hp": 240, "move": {"damage": 0}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_scaling_potion_early_in_dangerous_hallway_before_emergency_threshold(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 60,
                "max_hp": 95,
                "potions": [
                    {"id": "StrengthPotion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 60, "max_hp": 95, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Jaw Worm", "id": "JawWorm", "current_hp": 35, "max_hp": 35, "move": {"damage": 8}},
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 30, "max_hp": 30, "move": {"damage": 6}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertIn("Dangerous early fight", decision.reason)

    def test_combat_uses_cultist_potion_as_probe74_low_hp_hallway_tempo(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 19,
                "max_hp": 95,
                "potions": [
                    {"id": "CultistPotion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 6,
                    "player": {"current_hp": 19, "max_hp": 95, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Jaw Worm", "id": "JawWorm", "current_hp": 32, "max_hp": 40, "move": {"damage": 15}},
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 17, "max_hp": 23, "move": None},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_cultist_potion_early_in_probe74_scaling_hallway(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 55,
                "max_hp": 95,
                "potions": [
                    {"id": "CultistPotion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 55, "max_hp": 95, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Jaw Worm", "id": "JawWorm", "current_hp": 40, "max_hp": 40, "move": {"damage": 15}},
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 28, "max_hp": 28, "move": None},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_saves_cultist_potion_in_safe_short_hallway(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 70,
                "max_hp": 95,
                "potions": [
                    {"id": "CultistPotion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 70, "max_hp": 95, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Louse", "id": "Louse", "current_hp": 12, "max_hp": 12, "move": {"damage": 6}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_uses_steroid_potion_early_in_sentries_elite(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 60,
                "max_hp": 80,
                "potions": [
                    {"id": "BlessingOfTheForge", "can_use": True},
                    {"id": "ElixirPotion", "can_use": True},
                    {"id": "SteroidPotion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 60, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Battle Trance", "id": "Battle Trance", "type": "SKILL", "cost": 0, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Sentry", "id": "Sentry", "current_hp": 41, "max_hp": 41, "move": None},
                        {"name": "Sentry", "id": "Sentry", "current_hp": 41, "max_hp": 41, "move": {"damage": 10}},
                        {"name": "Sentry", "id": "Sentry", "current_hp": 41, "max_hp": 41, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 3}])

    def test_combat_uses_swift_potion_as_emergency_tempo(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 4,
                "max_hp": 95,
                "potions": [
                    {"id": "Swift Potion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 12,
                    "player": {"current_hp": 4, "max_hp": 95, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Iron Wave", "id": "Iron Wave", "type": "ATTACK", "cost": 1, "damage": 5, "block": 7, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 39,
                            "max_hp": 240,
                            "move": {"damage": 10},
                            "powers": [{"id": "Sharp Hide", "amount": 3}],
                        },
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_energy_potion_as_emergency_tempo(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 28,
                "max_hp": 87,
                "potions": [
                    {"id": "Energy Potion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 9,
                    "player": {"current_hp": 28, "max_hp": 87, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Pommel Strike", "id": "Pommel Strike", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 127, "max_hp": 250, "move": {"hits": 6, "damage": 5}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_duplication_potion_before_big_hexaghost_block_card(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 67,
                "max_hp": 88,
                "potions": [
                    {"id": "DuplicationPotion", "name": "Duplication Potion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 67, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Bash", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 8, "is_playable": True, "has_target": True},
                        {"name": "Pommel Strike", "id": "Pommel Strike", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True, "has_target": True},
                        {"name": "Shrug It Off", "id": "Shrug It Off", "type": "SKILL", "cost": 1, "block": 13, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 230, "max_hp": 250, "move": {"hits": 6, "damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_does_not_spend_duplication_potion_on_low_impact_boss_hand(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 67,
                "max_hp": 88,
                "potions": [
                    {"id": "DuplicationPotion", "name": "Duplication Potion", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 67, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 250, "max_hp": 250, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions[0]["action"], "play_card")

    def test_combat_uses_gamblers_brew_as_emergency_tempo(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 9,
                "max_hp": 80,
                "potions": [
                    {"id": "GamblersBrew", "name": "Gambler's Brew", "can_use": True},
                ],
                "combat_state": {
                    "turn": 9,
                    "player": {"current_hp": 9, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Hexaghost", "id": "Hexaghost", "current_hp": 102, "max_hp": 250, "move": {"hits": 6, "damage": 5}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])

    def test_combat_uses_skill_potion_under_probe68_snecko_lethal_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "act": 2,
                "floor": 21,
                "current_hp": 10,
                "max_hp": 80,
                "potions": [
                    {"id": "SkillPotion", "name": "Skill Potion", "can_use": True},
                    {"id": "LiquidMemories", "name": "Liquid Memories", "can_use": True},
                ],
                "combat_state": {
                    "turn": 3,
                    "player": {"current_hp": 10, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Carnage", "id": "Carnage", "type": "ATTACK", "cost": 2, "damage": 20, "is_playable": True, "has_target": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                        {"name": "Demon Form", "id": "Demon Form", "type": "POWER", "cost": 2, "is_playable": True},
                        {"name": "Reaper", "id": "Reaper", "type": "ATTACK", "cost": 3, "damage": 4, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {"name": "Snecko", "id": "Snecko", "current_hp": 78, "max_hp": 115, "move": {"damage": 27}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertIn("look for defense", decision.reason)

    def test_combat_uses_liquid_memories_for_discarded_block_under_lethal_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 18,
                "max_hp": 80,
                "potions": [
                    {"id": "LiquidMemories", "name": "Liquid Memories", "can_use": True},
                ],
                "combat_state": {
                    "turn": 5,
                    "player": {"current_hp": 18, "max_hp": 80, "current_energy": 1, "block": 8},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "discard_pile": [
                        {"name": "Flame Barrier", "id": "Flame Barrier", "type": "SKILL", "cost": 2, "block": 12},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "id": "GremlinNob", "current_hp": 34, "max_hp": 83, "move": {"damage": 30}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertIn("recover Flame Barrier", decision.reason)

    def test_combat_does_not_spend_liquid_memories_without_impactful_discard(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 16,
                "max_hp": 80,
                "potions": [
                    {"id": "LiquidMemories", "name": "Liquid Memories", "can_use": True},
                ],
                "combat_state": {
                    "turn": 4,
                    "player": {"current_hp": 16, "max_hp": 80, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "discard_pile": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6},
                    ],
                    "monsters": [
                        {"name": "Jaw Worm", "id": "JawWorm", "current_hp": 40, "max_hp": 44, "move": {"damage": 18}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions[0]["action"], "play_card")

    def test_combat_does_not_spend_liquid_memories_when_safe(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 70,
                "max_hp": 80,
                "potions": [
                    {"id": "LiquidMemories", "name": "Liquid Memories", "can_use": True},
                ],
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 70, "max_hp": 80, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "discard_pile": [
                        {"name": "Flame Barrier", "id": "Flame Barrier", "type": "SKILL", "cost": 2, "block": 12},
                    ],
                    "monsters": [
                        {"name": "Cultist", "id": "Cultist", "current_hp": 40, "max_hp": 48, "move": {"damage": 6}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions[0]["action"], "play_card")

    def test_combat_does_not_spend_gamblers_brew_when_safe(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 70,
                "max_hp": 80,
                "potions": [
                    {"id": "GamblersBrew", "name": "Gambler's Brew", "can_use": True},
                ],
                "combat_state": {
                    "turn": 1,
                    "player": {"current_hp": 70, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {"name": "Cultist", "id": "Cultist", "current_hp": 40, "max_hp": 40, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions[0]["action"], "play_card")

    def test_combat_avoids_nonlethal_attack_when_sharp_hide_can_kill(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 2,
                "max_hp": 95,
                "potions": [],
                "combat_state": {
                    "turn": 12,
                    "player": {"current_hp": 2, "max_hp": 95, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 7, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 22,
                            "max_hp": 240,
                            "move": {"damage": 10},
                            "powers": [{"id": "Sharp Hide", "amount": 3}],
                        },
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])

    def test_combat_pressure_fallback_skips_negative_reflect_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 28,
                "max_hp": 88,
                "potions": [],
                "combat_state": {
                    "turn": 4,
                    "player": {"current_hp": 28, "max_hp": 88, "current_energy": 2, "block": 0},
                    "hand": [
                        {"name": "Anger", "id": "Anger", "type": "ATTACK", "cost": 0, "damage": 6, "has_target": True, "is_playable": True},
                        {"name": "Wild Strike", "id": "Wild Strike", "type": "ATTACK", "cost": 1, "damage": 12, "has_target": True, "is_playable": True},
                    ],
                    "monsters": [
                        {
                            "name": "The Guardian",
                            "id": "TheGuardian",
                            "current_hp": 120,
                            "max_hp": 240,
                            "move": {"damage": 16},
                            "powers": [{"id": "Sharp Hide", "amount": 3}],
                        },
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_targets_attack_even_without_has_target_flag(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 70, "max_hp": 80, "current_energy": 3},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Cultist", "current_hp": 40, "max_hp": 40, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_targets_killable_attacker_before_non_attacking_enemy(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 70, "max_hp": 80, "current_energy": 1},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Acid Slime", "current_hp": 5, "max_hp": 28, "move": None},
                        {"name": "Mad Gremlin", "current_hp": 6, "max_hp": 20, "move": {"damage": 7}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 2}])

    def test_combat_targets_large_slime_when_attack_triggers_split(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 47, "max_hp": 88, "current_energy": 1},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 8, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Acid Slime", "id": "AcidSlime_L", "current_hp": 38, "max_hp": 70, "move": {"damage": 12}},
                        {"name": "Louse", "current_hp": 12, "max_hp": 12, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 1}])

    def test_combat_post_split_slimes_focuses_low_hp_attacker(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 39, "max_hp": 80, "block": 6, "current_energy": 1},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 4, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 20, "max_hp": 20, "move": {"damage": 10}},
                        {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 12, "max_hp": 20, "move": {"damage": 10}},
                        {"name": "Acid Slime", "id": "AcidSlime_L", "current_hp": 46, "max_hp": 54, "move": {"damage": 12}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1, "target_index": 2}])

    def test_combat_prioritizes_disarm_against_large_attack(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 53, "max_hp": 80, "current_energy": 2},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Disarm", "id": "Disarm", "type": "SKILL", "cost": 1, "has_target": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Gremlin Nob", "current_hp": 42, "max_hp": 85, "move": {"damage": 18}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2, "target_index": 1}])

    def test_combat_prioritizes_shockwave_under_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 22, "max_hp": 80, "current_energy": 3},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Shockwave", "id": "Shockwave", "type": "SKILL", "cost": 2, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Fungi Beast", "current_hp": 28, "max_hp": 28, "move": {"damage": 6}},
                        {"name": "Fungi Beast", "current_hp": 28, "max_hp": 28, "move": {"damage": 6}},
                        {"name": "Fungi Beast", "current_hp": 28, "max_hp": 28, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])

    def test_combat_values_thunderclap_as_aoe_against_multiple_attackers(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "player": {"current_hp": 78, "max_hp": 88, "current_energy": 2, "block": 5},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"name": "Bash", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 8, "is_playable": True, "has_target": True},
                        {"name": "Thunderclap", "id": "Thunderclap", "type": "ATTACK", "cost": 1, "damage": 4, "is_playable": True, "has_target": False},
                    ],
                    "monsters": [
                        {"name": "Louse", "current_hp": 12, "max_hp": 12, "move": {"damage": 6}},
                        {"name": "Louse", "current_hp": 13, "max_hp": 13, "move": {"damage": 7}},
                        {"name": "Louse", "current_hp": 14, "max_hp": 14, "move": {"damage": 6}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 3}])

    def test_combat_avoids_burning_pact_under_low_hp_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 24,
                "max_hp": 88,
                "combat_state": {
                    "player": {"current_hp": 24, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Burning Pact", "id": "Burning Pact", "type": "SKILL", "cost": 1, "exhausts": True, "is_playable": True},
                        {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Acid Slime", "current_hp": 35, "max_hp": 35, "move": {"damage": 16}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2}])

    def test_combat_avoids_dark_embrace_before_block_under_probe65_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 43,
                "max_hp": 80,
                "combat_state": {
                    "turn": 5,
                    "player": {"current_hp": 43, "max_hp": 80, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Battle Trance", "id": "Battle Trance", "type": "SKILL", "cost": 0, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"name": "True Grit", "id": "True Grit", "type": "SKILL", "cost": 1, "block": 7, "is_playable": True},
                        {"name": "Dark Embrace", "id": "Dark Embrace", "type": "POWER", "cost": 2, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 14, "max_hp": 28, "move": {"damage": 12}},
                        {"name": "Blue Slaver", "id": "SlaverBlue", "current_hp": 31, "max_hp": 47, "move": {"damage": 8}},
                    ],
                },
            },
        }

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 3}])

    def test_combat_can_play_burning_pact_when_safe(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 70,
                "max_hp": 88,
                "combat_state": {
                    "player": {"current_hp": 70, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Burning Pact", "id": "Burning Pact", "type": "SKILL", "cost": 1, "exhausts": True, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Cultist", "current_hp": 35, "max_hp": 35, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 1}])

    def test_combat_avoids_combust_under_low_hp_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 20,
                "max_hp": 88,
                "combat_state": {
                    "player": {"current_hp": 20, "max_hp": 88, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Combust", "id": "Combust", "type": "POWER", "cost": 1, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Louse", "current_hp": 24, "max_hp": 24, "move": {"damage": 12}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "end_turn"}])

    def test_combat_avoids_nonlethal_hemokinesis_at_two_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 2,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 2, "max_hp": 80, "current_energy": 1, "block": 13},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Headbutt", "id": "Headbutt", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True},
                        {"name": "Hemokinesis", "id": "Hemokinesis", "type": "ATTACK", "cost": 1, "damage": 15, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Red Slaver", "id": "SlaverRed", "current_hp": 37, "max_hp": 48, "move": {"damage": 9}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 3, "target_index": 1}])

    def test_combat_can_play_hemokinesis_for_safe_lethal(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 10,
                "max_hp": 80,
                "combat_state": {
                    "turn": 2,
                    "player": {"current_hp": 10, "max_hp": 80, "current_energy": 1, "block": 0},
                    "hand": [
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                        {"name": "Hemokinesis", "id": "Hemokinesis", "type": "ATTACK", "cost": 1, "damage": 14, "is_playable": True},
                    ],
                    "monsters": [
                        {"name": "Lagavulin", "id": "Lagavulin", "current_hp": 14, "max_hp": 109, "move": None},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "play_card", "card_index": 2, "target_index": 1}])

    def test_combat_uses_attack_fallback_when_no_defense_under_pressure(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "current_hp": 34,
                "max_hp": 88,
                "combat_state": {
                    "player": {"current_hp": 34, "max_hp": 88, "current_energy": 3, "block": 0},
                    "hand": [
                        {"name": "Slimed", "id": "Slimed", "type": "STATUS", "cost": 1, "is_playable": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                        {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    ],
                    "monsters": [
                        {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 12, "max_hp": 32, "move": {"damage": 10}},
                        {"name": "Blue Slaver", "id": "SlaverBlue", "current_hp": 15, "max_hp": 48, "move": {"damage": 13}},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions[0]["action"], "play_card")
        self.assertIn(decision.actions[0]["card_index"], {2, 3})

    def test_map_avoids_elite_at_low_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "floor": 6,
                "current_hp": 20,
                "max_hp": 80,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 1, "y": 6},
                        {"symbol": "R", "x": 2, "y": 6},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_avoids_elite_at_injured_mid_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "floor": 6,
                "current_hp": 43,
                "max_hp": 80,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 1, "y": 6},
                        {"symbol": "R", "x": 2, "y": 6},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_chooses_question_over_second_elite_at_mid_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "floor": 10,
                "current_hp": 50,
                "max_hp": 88,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 0, "y": 10},
                        {"symbol": "?", "x": 1, "y": 10},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_avoids_late_act1_elite_with_only_speed_potion_and_question(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 10,
                "current_hp": 72,
                "max_hp": 88,
                "potions": [{"id": "SpeedPotion"}],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 0, "y": 10},
                        {"symbol": "?", "x": 1, "y": 10},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_can_take_late_act1_elite_with_high_impact_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 10,
                "current_hp": 72,
                "max_hp": 88,
                "potions": [{"id": "Fire Potion"}],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 0, "y": 10},
                        {"symbol": "?", "x": 1, "y": 10},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_map_prefers_rest_over_act1_elite_without_tempo_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 5,
                "current_hp": 74,
                "max_hp": 80,
                "potions": [
                    {"id": "ColorlessPotion"},
                    {"id": "GamblersBrew"},
                    {"id": "Ancient Potion"},
                ],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "R", "x": 0, "y": 5},
                        {"symbol": "E", "x": 1, "y": 5},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_map_prefers_rest_over_act1_elite_at_exact_probe44_hp_without_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 5,
                "current_hp": 76,
                "max_hp": 80,
                "potions": [],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "R", "x": 2, "y": 5},
                        {"symbol": "E", "x": 3, "y": 5},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_map_can_take_act1_elite_with_rest_choice_and_tempo_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 5,
                "current_hp": 74,
                "max_hp": 80,
                "potions": [
                    {"id": "Fire Potion"},
                ],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "R", "x": 0, "y": 5},
                        {"symbol": "E", "x": 1, "y": 5},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_probe54_low_hp_prefers_rest_over_elite_even_with_fire_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 5,
                "current_hp": 47,
                "max_hp": 72,
                "potions": [{"id": "Fire Potion"}],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 1, "y": 5},
                        {"symbol": "R", "x": 3, "y": 5},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_low_hp_prefers_question_over_monster_when_no_rest(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 7,
                "current_hp": 38,
                "max_hp": 88,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 2, "y": 7},
                        {"symbol": "?", "x": 3, "y": 7},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_act2_extreme_low_hp_prefers_question_over_probe61_monster(self):
        game = {
            "screen_type": "MAP",
            "act": 2,
            "floor": 20,
            "current_hp": 21,
            "max_hp": 80,
            "gold": 280,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "?", "x": 1, "y": 3},
                    {"symbol": "M", "x": 2, "y": 3},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "?", "x": 1, "y": 3, "children": [{"x": 1, "y": 4}]},
                        {"symbol": "M", "x": 2, "y": 3, "children": [{"x": 2, "y": 4}]},
                    ],
                    [
                        {"symbol": "M", "x": 1, "y": 4, "children": [{"x": 1, "y": 5}]},
                        {"symbol": "M", "x": 2, "y": 4, "children": [{"x": 2, "y": 5}]},
                    ],
                    [
                        {"symbol": "E", "x": 1, "y": 5, "children": [{"x": 1, "y": 6}]},
                        {"symbol": "E", "x": 2, "y": 5, "children": [{"x": 2, "y": 6}]},
                    ],
                    [
                        {"symbol": "R", "x": 1, "y": 6},
                        {"symbol": "R", "x": 2, "y": 6},
                    ],
                ],
            },
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        route_eval = game["route_evaluation"]
        self.assertEqual(route_eval["map_status"], "success")
        self.assertEqual(route_eval["options"][0]["lookahead_adjustment"], -145.0)
        self.assertEqual(route_eval["options"][1]["lookahead_adjustment"], -145.0)
        self.assertLess(route_eval["options"][1]["base_score"], route_eval["options"][0]["base_score"])

    def test_map_act2_probe76_critical_hp_monster_route_is_marked_risky(self):
        game = {
            "screen_type": "MAP",
            "act": 2,
            "floor": 20,
            "current_hp": 22,
            "max_hp": 88,
            "gold": 91,
            "potions": [],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "Shrug It Off"},
                {"id": "Cleave"},
            ],
            "screen_state": {"next_nodes": [{"symbol": "M", "x": 0, "y": 3}]},
            "map_observation": {
                "status": "success",
                "map": [
                    [{"symbol": "M", "x": 0, "y": 3, "children": [{"x": 0, "y": 4}]}],
                    [{"symbol": "M", "x": 0, "y": 4, "children": [{"x": 0, "y": 5}]}],
                    [{"symbol": "R", "x": 0, "y": 5}],
                ],
            },
        }

        decision = policy().decide({"in_game": True, "game_state": game})

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        lookahead = game["route_evaluation"]["options"][0]["lookahead"]
        self.assertLessEqual(lookahead["act2_route_penalty"], -70.0)
        self.assertIn("act2_critical_hp_forced_combat", lookahead["act2_route_flags"])
        self.assertIn("act2_no_recovery_buffer", lookahead["act2_route_flags"])
        self.assertIn("act2_no_emergency_potion", lookahead["act2_route_flags"])
        self.assertIn("act2_weak_missing", lookahead["act2_route_gaps"])

    def test_map_act2_low_hp_prefers_close_recovery_over_forced_hallway(self):
        game = {
            "screen_type": "MAP",
            "act": 2,
            "floor": 20,
            "current_hp": 24,
            "max_hp": 88,
            "gold": 91,
            "potions": [],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "Shrug It Off"},
            ],
            "screen_state": {
                "next_nodes": [
                    {"symbol": "M", "x": 0, "y": 3},
                    {"symbol": "?", "x": 1, "y": 3},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "M", "x": 0, "y": 3, "children": [{"x": 0, "y": 4}]},
                        {"symbol": "?", "x": 1, "y": 3, "children": [{"x": 1, "y": 4}]},
                    ],
                    [
                        {"symbol": "M", "x": 0, "y": 4, "children": [{"x": 0, "y": 5}]},
                        {"symbol": "R", "x": 1, "y": 4},
                    ],
                    [
                        {"symbol": "R", "x": 0, "y": 5},
                    ],
                ],
            },
        }

        decision = policy().decide({"in_game": True, "game_state": game})

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        self.assertIn("act2_route_penalty", route_eval["options"][0]["lookahead"])
        self.assertNotIn("act2_route_penalty", route_eval["options"][1]["lookahead"])

    def test_map_probe63_prefers_shop_buffer_before_forced_elite(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 3,
            "current_hp": 84,
            "max_hp": 88,
            "gold": 149,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "?", "x": 1, "y": 3},
                    {"symbol": "$", "x": 2, "y": 3},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "?", "x": 1, "y": 3, "children": [{"x": 1, "y": 4}]},
                        {"symbol": "$", "x": 2, "y": 3, "children": [{"x": 2, "y": 4}]},
                    ],
                    [
                        {"symbol": "?", "x": 1, "y": 4, "children": [{"x": 1, "y": 5}]},
                        {"symbol": "M", "x": 2, "y": 4, "children": [{"x": 2, "y": 5}]},
                    ],
                    [
                        {"symbol": "E", "x": 1, "y": 5},
                        {"symbol": "R", "x": 2, "y": 5, "children": [{"x": 2, "y": 6}]},
                    ],
                    [
                        {"symbol": "E", "x": 2, "y": 6},
                    ],
                ],
            },
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        self.assertLess(route_eval["options"][0]["lookahead_adjustment"], 0)
        self.assertGreater(route_eval["options"][1]["lookahead_adjustment"], 0)

    def test_map_probe66_prefers_question_over_no_buffer_forced_elite(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 2,
            "current_hp": 77,
            "max_hp": 80,
            "gold": 72,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "M", "x": 1, "y": 2},
                    {"symbol": "?", "x": 2, "y": 2},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "M", "x": 1, "y": 2, "children": [{"x": 1, "y": 3}]},
                        {"symbol": "?", "x": 2, "y": 2, "children": [{"x": 2, "y": 3}]},
                    ],
                    [
                        {"symbol": "?", "x": 1, "y": 3, "children": [{"x": 1, "y": 4}]},
                        {"symbol": "M", "x": 2, "y": 3, "children": [{"x": 2, "y": 4}, {"x": 3, "y": 4}]},
                    ],
                    [
                        {"symbol": "M", "x": 1, "y": 4, "children": [{"x": 1, "y": 5}]},
                        {"symbol": "?", "x": 2, "y": 4, "children": [{"x": 2, "y": 5}]},
                        {"symbol": "M", "x": 3, "y": 4, "children": [{"x": 3, "y": 5}]},
                    ],
                    [
                        {"symbol": "E", "x": 1, "y": 5},
                        {"symbol": "E", "x": 2, "y": 5, "children": [{"x": 2, "y": 6}]},
                        {"symbol": "?", "x": 3, "y": 5, "children": [{"x": 3, "y": 6}]},
                    ],
                    [
                        {"symbol": "R", "x": 2, "y": 6},
                        {"symbol": "R", "x": 3, "y": 6},
                    ],
                ],
            },
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        self.assertLess(route_eval["options"][0]["lookahead_adjustment"], 0)
        self.assertEqual(route_eval["options"][1]["lookahead"]["nearest_rest"], 4)

    def test_map_parallel_flow_avoids_deep_no_buffer_forced_elite(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 0,
            "current_hp": 88,
            "max_hp": 88,
            "gold": 99,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "M", "x": 0, "y": 0},
                    {"symbol": "M", "x": 2, "y": 0},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "M", "x": 0, "y": 0, "children": [{"x": 0, "y": 1}]},
                        {"symbol": "M", "x": 2, "y": 0, "children": [{"x": 2, "y": 1}]},
                    ],
                    [
                        {"symbol": "?", "x": 0, "y": 1, "children": [{"x": 0, "y": 2}]},
                        {"symbol": "M", "x": 2, "y": 1, "children": [{"x": 2, "y": 2}]},
                    ],
                    [
                        {"symbol": "M", "x": 0, "y": 2, "children": [{"x": 0, "y": 3}]},
                        {"symbol": "?", "x": 2, "y": 2, "children": [{"x": 2, "y": 3}]},
                    ],
                    [
                        {"symbol": "M", "x": 0, "y": 3, "children": [{"x": 0, "y": 4}]},
                        {"symbol": "R", "x": 2, "y": 3, "children": [{"x": 2, "y": 4}]},
                    ],
                    [
                        {"symbol": "M", "x": 0, "y": 4, "children": [{"x": 0, "y": 5}]},
                        {"symbol": "M", "x": 2, "y": 4, "children": [{"x": 2, "y": 5}]},
                    ],
                    [
                        {"symbol": "E", "x": 0, "y": 5},
                        {"symbol": "E", "x": 2, "y": 5},
                    ],
                ],
            },
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        self.assertTrue(route_eval["options"][0]["lookahead"]["forced_elite_within_5"])
        self.assertLess(route_eval["options"][0]["lookahead_adjustment"], 0)
        self.assertEqual(route_eval["options"][1]["lookahead"]["nearest_rest"], 3)
        self.assertGreater(route_eval["options"][1]["lookahead_adjustment"], route_eval["options"][0]["lookahead_adjustment"])

    def test_map_probe73_prefers_shop_over_healthy_no_buffer_forced_elite(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 3,
            "current_hp": 80,
            "max_hp": 88,
            "gold": 146,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "M", "x": 0, "y": 3},
                    {"symbol": "$", "x": 1, "y": 3},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "M", "x": 0, "y": 3, "children": [{"x": 0, "y": 4}]},
                        {"symbol": "$", "x": 1, "y": 3, "children": [{"x": 1, "y": 4}]},
                    ],
                    [
                        {"symbol": "M", "x": 0, "y": 4, "children": [{"x": 0, "y": 5}]},
                        {"symbol": "M", "x": 1, "y": 4, "children": [{"x": 1, "y": 5}]},
                    ],
                    [
                        {"symbol": "E", "x": 0, "y": 5},
                        {"symbol": "?", "x": 1, "y": 5, "children": [{"x": 1, "y": 6}]},
                    ],
                    [
                        {"symbol": "R", "x": 1, "y": 6},
                    ],
                ],
            },
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        self.assertLess(route_eval["options"][0]["lookahead_adjustment"], -50)
        self.assertEqual(route_eval["options"][0]["lookahead"]["nearest_shop"], None)
        self.assertEqual(route_eval["options"][1]["lookahead"]["nearest_shop"], 0)

    def test_map_probe83_avoids_injured_elite_chain_before_recovery(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 7,
            "current_hp": 69,
            "max_hp": 95,
            "gold": 268,
            "potions": [{"id": "Block Potion"}, {"id": "Fire Potion"}, {"id": "FearPotion"}],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "Bludgeon"},
                {"id": "Pommel Strike"},
            ],
            "screen_state": {
                "next_nodes": [
                    {"symbol": "E", "x": 0, "y": 7},
                    {"symbol": "M", "x": 1, "y": 7},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "E", "x": 0, "y": 7, "children": [{"x": 0, "y": 8}]},
                        {"symbol": "M", "x": 1, "y": 7, "children": [{"x": 1, "y": 8}]},
                    ],
                    [
                        {"symbol": "T", "x": 0, "y": 8, "children": [{"x": 0, "y": 9}]},
                        {"symbol": "R", "x": 1, "y": 8},
                    ],
                    [
                        {"symbol": "E", "x": 0, "y": 9, "children": [{"x": 0, "y": 10}]},
                    ],
                    [
                        {"symbol": "M", "x": 0, "y": 10, "children": [{"x": 0, "y": 11}]},
                    ],
                    [
                        {"symbol": "$", "x": 0, "y": 11},
                    ],
                ],
            },
        }

        decision = policy().decide({"in_game": True, "game_state": game})

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        elite_lookahead = route_eval["options"][0]["lookahead"]
        self.assertTrue(elite_lookahead["forced_elite_within_3"])
        self.assertEqual(elite_lookahead["nearest_shop"], 4)
        self.assertEqual(elite_lookahead["act1_elite_chain_penalty"], -38.0)

    def test_map_probe84_rest_node_does_not_hide_forced_late_elite(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 11,
            "current_hp": 40,
            "max_hp": 80,
            "gold": 40,
            "potions": [],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "Heavy Blade"},
                {"id": "Wild Strike"},
                {"id": "Carnage"},
                {"id": "True Grit"},
                {"id": "Metallicize"},
            ],
            "screen_state": {
                "next_nodes": [
                    {"symbol": "R", "x": 5, "y": 11},
                    {"symbol": "?", "x": 6, "y": 11},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "R", "x": 5, "y": 11, "children": [{"x": 5, "y": 12}]},
                        {"symbol": "?", "x": 6, "y": 11, "children": [{"x": 6, "y": 12}]},
                    ],
                    [
                        {"symbol": "E", "x": 5, "y": 12, "children": [{"x": 5, "y": 13}]},
                        {"symbol": "M", "x": 6, "y": 12, "children": [{"x": 6, "y": 13}]},
                    ],
                    [
                        {"symbol": "M", "x": 5, "y": 13, "children": [{"x": 5, "y": 14}]},
                        {"symbol": "?", "x": 6, "y": 13, "children": [{"x": 6, "y": 14}]},
                    ],
                    [
                        {"symbol": "R", "x": 5, "y": 14},
                        {"symbol": "R", "x": 6, "y": 14},
                    ],
                ],
            },
        }

        decision = policy().decide({"in_game": True, "game_state": game})

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        rest_lookahead = route_eval["options"][0]["lookahead"]
        self.assertTrue(rest_lookahead["forced_elite_within_3"])
        self.assertEqual(rest_lookahead["nearest_rest"], 0)
        self.assertEqual(rest_lookahead["act1_rest_forced_elite_penalty"], -64.0)
        self.assertLess(route_eval["options"][0]["score"], route_eval["options"][1]["score"])

    def test_map_readiness_avoids_immediate_elite_without_core_tools(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 6,
            "current_hp": 78,
            "max_hp": 88,
            "potions": [],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
            ],
            "screen_state": {
                "next_nodes": [
                    {"symbol": "E", "x": 0, "y": 6},
                    {"symbol": "?", "x": 1, "y": 6},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "E", "x": 0, "y": 6, "children": [{"x": 0, "y": 7}]},
                        {"symbol": "?", "x": 1, "y": 6, "children": [{"x": 1, "y": 7}]},
                    ],
                    [
                        {"symbol": "R", "x": 0, "y": 7},
                        {"symbol": "R", "x": 1, "y": 7},
                    ],
                ],
            },
        }

        decision = policy().decide({"in_game": True, "game_state": game})

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        elite_lookahead = game["route_evaluation"]["options"][0]["lookahead"]
        self.assertLess(elite_lookahead["readiness_penalty"], 0)
        self.assertIn("elite_not_ready", elite_lookahead["readiness_flags"])
        self.assertIn("premium_block_missing", elite_lookahead["readiness_gaps"])

    def test_map_readiness_allows_elite_with_aoe_weak_block_and_tempo(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 6,
            "current_hp": 78,
            "max_hp": 88,
            "potions": [{"id": "Fire Potion"}],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "Shrug It Off"},
                {"id": "Clothesline"},
                {"id": "Cleave"},
            ],
            "screen_state": {
                "next_nodes": [
                    {"symbol": "E", "x": 0, "y": 6},
                    {"symbol": "?", "x": 1, "y": 6},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "E", "x": 0, "y": 6, "children": [{"x": 0, "y": 7}]},
                        {"symbol": "?", "x": 1, "y": 6, "children": [{"x": 1, "y": 7}]},
                    ],
                    [
                        {"symbol": "R", "x": 0, "y": 7},
                        {"symbol": "R", "x": 1, "y": 7},
                    ],
                ],
            },
        }

        decision = policy().decide({"in_game": True, "game_state": game})

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        elite_lookahead = game["route_evaluation"]["options"][0]["lookahead"]
        self.assertNotIn("readiness_penalty", elite_lookahead)

    def test_map_lookahead_avoids_low_hp_path_committed_to_elite(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 5,
            "current_hp": 30,
            "max_hp": 88,
            "gold": 90,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "M", "x": 0, "y": 5},
                    {"symbol": "?", "x": 2, "y": 5},
                ]
            },
            "map_observation": {
                "status": "success",
                "map": [
                    [
                        {"symbol": "M", "x": 0, "y": 5, "children": [{"x": 0, "y": 6}]},
                        {"symbol": "?", "x": 2, "y": 5, "children": [{"x": 2, "y": 6}]},
                    ],
                    [
                        {"symbol": "T", "x": 0, "y": 6, "children": [{"x": 0, "y": 7}]},
                        {"symbol": "R", "x": 2, "y": 6},
                    ],
                    [
                        {"symbol": "E", "x": 0, "y": 7},
                    ],
                ],
            },
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        route_eval = game["route_evaluation"]
        self.assertEqual(route_eval["map_status"], "success")
        self.assertEqual(route_eval["node_count"], 5)
        self.assertEqual(route_eval["edge_count"], 3)
        self.assertLess(route_eval["options"][0]["lookahead_adjustment"], 0)
        self.assertTrue(route_eval["options"][0]["lookahead"]["forced_elite_within_3"])
        self.assertEqual(route_eval["options"][1]["lookahead"]["nearest_rest"], 1)

    def test_map_observation_error_keeps_immediate_route_fallback(self):
        game = {
            "screen_type": "MAP",
            "act": 1,
            "floor": 7,
            "current_hp": 70,
            "max_hp": 88,
            "screen_state": {
                "next_nodes": [
                    {"symbol": "M", "x": 2, "y": 7},
                    {"symbol": "?", "x": 3, "y": 7},
                ]
            },
            "map_observation": {"status": "error", "error": "Internal error: null"},
        }
        state = {"in_game": True, "game_state": game}

        decision = policy().decide(state)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(game["route_evaluation"]["map_status"], "error")
        self.assertEqual(game["route_evaluation"]["horizon"], 0)

    def test_map_injured_act1_prefers_question_over_monster_without_tempo_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 2,
                "current_hp": 62,
                "max_hp": 88,
                "potions": [{"id": "SkillPotion"}],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 0, "y": 2},
                        {"symbol": "?", "x": 1, "y": 2},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_injured_act1_prefers_question_over_monster_even_with_tempo_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 10,
                "current_hp": 59,
                "max_hp": 88,
                "potions": [{"id": "Fire Potion"}],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 4, "y": 10},
                        {"symbol": "?", "x": 5, "y": 10},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_act1_deck_without_premium_block_prefers_question_over_fourth_hallway(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 3,
                "current_hp": 78,
                "max_hp": 88,
                "deck": [
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Strike_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Defend_R"},
                    {"id": "Bash"},
                    {"id": "Feel No Pain"},
                    {"id": "Thunderclap"},
                    {"id": "Clothesline"},
                ],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 1, "y": 3},
                        {"symbol": "?", "x": 3, "y": 3},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_late_act1_low_hp_prefers_monster_over_question_at_probe47_risk(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 12,
                "current_hp": 51,
                "max_hp": 98,
                "potions": [],
                "deck": act1_deck_without_premium_block(),
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 0, "y": 12},
                        {"symbol": "?", "x": 2, "y": 12},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_map_early_act1_low_hp_still_prefers_question_over_monster(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 5,
                "current_hp": 55,
                "max_hp": 80,
                "potions": [],
                "deck": act1_deck_without_premium_block(),
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 0, "y": 5},
                        {"symbol": "?", "x": 1, "y": 5},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_extremely_low_hp_late_act1_does_not_force_monster_over_question(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 12,
                "current_hp": 20,
                "max_hp": 98,
                "potions": [],
                "deck": act1_deck_without_premium_block(),
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 0, "y": 12},
                        {"symbol": "?", "x": 2, "y": 12},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_injured_act1_prefers_shop_over_monster(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 4,
                "current_hp": 63,
                "max_hp": 80,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 0, "y": 4},
                        {"symbol": "$", "x": 1, "y": 4},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_map_low_hp_shop_penalty_is_not_shadowed_by_question_safety_rule(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 4,
                "current_hp": 55,
                "max_hp": 80,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "$", "x": 0, "y": 4},
                        {"symbol": "M", "x": 1, "y": 4},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_map_healthy_act1_can_still_choose_monster_over_shop(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 4,
                "current_hp": 78,
                "max_hp": 80,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 0, "y": 4},
                        {"symbol": "$", "x": 1, "y": 4},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_map_healthy_act1_can_still_choose_monster_over_question(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "MAP",
                "act": 1,
                "floor": 7,
                "current_hp": 70,
                "max_hp": 88,
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "M", "x": 2, "y": 7},
                        {"symbol": "?", "x": 3, "y": 7},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_rest_before_act1_danger_when_no_potions_and_injured(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "REST",
                "room_phase": "INCOMPLETE",
                "act": 1,
                "floor": 6,
                "current_hp": 63,
                "max_hp": 88,
                "potions": [],
                "screen_state": {"rest_options": ["Rest", "Smith"]},
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_rest_before_act1_danger_when_badly_injured_even_with_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "REST",
                "act": 1,
                "floor": 6,
                "current_hp": 51,
                "max_hp": 88,
                "potions": [{"id": "Fire Potion", "can_use": True}],
                "screen_state": {"rest_options": ["Rest", "Smith"]},
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_rest_before_act1_elite_risk_at_probe14_hp_even_with_potion(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "REST",
                "act": 1,
                "floor": 6,
                "current_hp": 56,
                "max_hp": 88,
                "potions": [{"id": "Fire Potion", "can_use": True}],
                "screen_state": {"rest_options": ["Rest", "Smith"]},
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_rest_before_act1_danger_with_potion_above_critical_hp_allows_smith(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "REST",
                "act": 1,
                "floor": 6,
                "current_hp": 70,
                "max_hp": 88,
                "potions": [{"id": "Fire Potion", "can_use": True}],
                "screen_state": {"rest_options": ["Rest", "Smith"]},
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_rest_before_act1_danger_with_non_tempo_potion_still_rests_at_probe49_hp(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "REST",
                "act": 1,
                "floor": 6,
                "current_hp": 60,
                "max_hp": 80,
                "potions": [{"id": "SkillPotion", "can_use": True}],
                "screen_state": {"rest_options": ["Rest", "Smith"]},
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_rest_complete_proceeds_before_resting_again(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "REST",
                "room_phase": "COMPLETE",
                "act": 1,
                "floor": 7,
                "current_hp": 53,
                "max_hp": 88,
                "potions": [],
                "screen_state": {"rest_options": ["Rest", "Smith"]},
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "proceed"}])

    def test_event_grid_picks_low_value_card_for_transform(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "GRID",
                "room_phase": "EVENT",
                "screen_state": {
                    "num_cards": 2,
                    "cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                        {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                        {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                    ],
                    "selected_cards": [],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])

    def test_event_grid_skips_already_selected_cards(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "GRID",
                "room_phase": "EVENT",
                "screen_state": {
                    "num_cards": 2,
                    "cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                        {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                        {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                    ],
                    "selected_cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_event_grid_skips_duplicate_card_ids_after_selection_for_probe52_neow_transform(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "GRID",
                "room_phase": "EVENT",
                "screen_state": {
                    "num_cards": 2,
                    "cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-2"},
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-3"},
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-4"},
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-5"},
                        {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                        {"id": "Defend_R", "name": "Defend", "uuid": "defend-2"},
                        {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                    ],
                    "selected_cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 6}])

    def test_event_grid_confirms_when_selection_count_is_complete(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "GRID",
                "room_phase": "EVENT",
                "screen_state": {
                    "num_cards": 2,
                    "cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                        {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                        {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                    ],
                    "selected_cards": [
                        {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                        {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                    ],
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "confirm"}])

    def test_low_hp_event_avoids_chinese_fight_when_leave_is_available(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "EVENT",
                "current_hp": 22,
                "max_hp": 80,
                "screen_state": {
                    "options": [
                        {"label": "\u8e29\u6241", "text": "\u8e29\u6241\u3002\u6218\u6597\u3002", "choice_index": 0},
                        {"label": "\u79bb\u5f00", "text": "\u79bb\u5f00\u3002", "choice_index": 1},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_low_hp_event_prefers_healing(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "EVENT",
                "current_hp": 14,
                "max_hp": 80,
                "screen_state": {
                    "options": [
                        {"label": "\u6536\u96c6\u91d1\u5e01", "text": "\u6536\u96c6\u91d1\u5e01\u3002\u5931\u53bb\u751f\u547d\u3002", "choice_index": 0},
                        {"label": "\u6cbb\u7597", "text": "\u6cbb\u7597\u3002", "choice_index": 1},
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_critical_hp_event_avoids_mojibake_life_loss(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "EVENT",
                "current_hp": 8,
                "max_hp": 88,
                "screen_state": {
                    "options": [
                        {
                            "label": "�ռ����",
                            "text": "[�ռ����] ��� 75 ��ҡ� ʧȥ 11 ������",
                            "choice_index": 0,
                        },
                        {
                            "label": "���ְ�",
                            "text": "[���ְ�] ʧȥ 35 ��ҡ�",
                            "choice_index": 1,
                        },
                    ]
                },
            },
        }
        decision = policy().decide(state)
        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_outcome_learning_adjusts_episode_card_delta(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            memory.learned = {
                "version": 1,
                "runs": {"victories": 0, "deaths": 0, "total": 0},
                "card_picks": {},
                "recent_outcomes": [],
            }
            state = {
                "game_state": {
                    "screen_state": {"victory": True, "score": 1000},
                    "floor": 50,
                    "class": "IRONCLAD",
                    "ascension_level": 0,
                }
            }
            memory.record_outcome(state, ["Shrug It Off"])
            self.assertEqual(memory.learned["card_picks"]["Shrug It Off"]["delta"], 1.0)

    def test_builds_frontier_targets_from_unlocked_ascensions(self):
        unlocks = {
            "IRONCLAD": type("Unlock", (), {"unlocked_ascension": 4})(),
            "SILENT": type("Unlock", (), {"unlocked_ascension": 0})(),
        }
        targets = build_targets(["IRONCLAD", "SILENT"], 5, ladder=False, unlocks=unlocks)
        self.assertEqual([target.key for target in targets], ["IRONCLAD:A4", "SILENT:A0"])

    def test_reads_completed_log_for_offline_learning(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            records = [
                {
                    "state": {"floor": 1, "class": "IRONCLAD", "ascension_level": 20},
                    "decision": {"learn_card_pick": "Shrug It Off"},
                },
                {
                    "state": {
                        "floor": 50,
                        "class": "IRONCLAD",
                        "ascension_level": 20,
                        "outcome": {"victory": True, "score": 1234},
                    },
                    "decision": {},
                },
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            learned = read_log(path)
            self.assertEqual(learned.picks, ["Shrug It Off"])
            self.assertTrue(learned.victory)
            self.assertEqual(learned.ascension, 20)

    def test_reads_game_over_without_victory_as_completed_loss(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            records = [
                {
                    "state": {
                        "screen_type": "CARD_REWARD",
                        "floor": 7,
                        "class": "IRONCLAD",
                        "ascension_level": 4,
                    },
                    "decision": {"learn_card_pick": "Iron Wave"},
                },
                {
                    "state": {
                        "screen_type": "GAME_OVER",
                        "floor": 16,
                        "class": "IRONCLAD",
                        "ascension_level": 4,
                        "outcome": {"victory": None, "score": None},
                    },
                    "decision": {},
                },
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            learned = read_log(path)
            self.assertEqual(learned.picks, ["Iron Wave"])
            self.assertFalse(learned.victory)
            self.assertEqual(learned.floor, 16)

    def test_card_model_training_uses_stable_card_ids_for_options(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            records = [
                {
                    "state": {
                        "screen_type": "CARD_REWARD",
                        "floor": 3,
                        "class": "IRONCLAD",
                        "ascension_level": 4,
                        "card_reward_options": [
                            {"name": "localized warcry", "id": "Warcry"},
                            {"name": "localized seeing red", "id": "Seeing Red"},
                            {"name": "localized perfected strike", "id": "Perfected Strike"},
                        ],
                    },
                    "decision": {"learn_card_pick": "Perfected Strike"},
                },
                {
                    "state": {
                        "floor": 16,
                        "class": "IRONCLAD",
                        "ascension_level": 4,
                        "outcome": {"victory": False, "score": 321},
                    },
                    "decision": {},
                },
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            examples = load_examples([path])
            self.assertEqual(len(examples), 1)
            self.assertEqual(examples[0].picked, "Perfected Strike")
            self.assertEqual(examples[0].options, ["Warcry", "Seeing Red", "Perfected Strike"])

    def test_reads_local_unlock_files(self):
        with TemporaryDirectory() as tmp:
            pref = Path(tmp) / "preferences"
            pref.mkdir()
            (pref / "STSDataVagabond").write_text('{"ASCENSION_LEVEL":"4","LAST_ASCENSION_LEVEL":"4","WIN_COUNT":"7","HIGHEST_FLOOR":"51"}', encoding="utf-8")
            unlocks = read_unlocks(Path(tmp))
            self.assertEqual(unlocks["IRONCLAD"].unlocked_ascension, 4)
            self.assertEqual(unlocks["SILENT"].unlocked_ascension, 0)

    def test_empty_progress_shape(self):
        with TemporaryDirectory() as tmp:
            progress = load_progress(Path(tmp) / "missing.json")
            self.assertEqual(progress["version"], 1)


if __name__ == "__main__":
    unittest.main()
