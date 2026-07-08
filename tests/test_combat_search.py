import unittest

from slay_ai.combat_search import find_best_combat_sequence


class CombatSearchTests(unittest.TestCase):
    def test_monster_hp_field_is_used_when_current_hp_is_missing(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 7,
            "combat_state": {
                "player": {"current_hp": 7, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Twin Strike",
                        "id": "Twin Strike",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 5,
                        "is_playable": True,
                        "has_target": True,
                    }
                ],
                "monsters": [
                    {
                        "name": "Hexaghost",
                        "id": "Hexaghost",
                        "hp": 4,
                        "max_hp": 250,
                        "move": {"damage": 24},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game, max_depth=1)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Twin Strike",))
        self.assertEqual(result.kills, 1)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 1})

    def test_single_card_guardian_mode_shift_can_be_searched(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 12,
            "combat_state": {
                "player": {"current_hp": 12, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    }
                ],
                "monsters": [
                    {
                        "name": "The Guardian",
                        "id": "TheGuardian",
                        "current_hp": 120,
                        "max_hp": 240,
                        "move": {"damage": 12},
                        "powers": [{"id": "ModeShift", "amount": 6}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Strike_R",))
        self.assertEqual(result.initial_loss, 12)
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.attacks_removed, 12)
        self.assertTrue(result.avoided_lethal)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 1})

    def test_single_card_slime_split_can_be_searched(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 20,
            "combat_state": {
                "player": {"current_hp": 20, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    }
                ],
                "monsters": [
                    {
                        "name": "Slime Boss",
                        "id": "SlimeBoss",
                        "current_hp": 73,
                        "max_hp": 140,
                        "move": {"damage": 28},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Strike_R",))
        self.assertEqual(result.initial_loss, 28)
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.attacks_removed, 28)
        self.assertTrue(result.avoided_lethal)

    def test_slime_split_stops_current_attack(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 20,
            "combat_state": {
                "player": {"current_hp": 20, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Defend",
                        "id": "Defend_R",
                        "type": "SKILL",
                        "cost": 1,
                        "block": 5,
                        "is_playable": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Slime Boss",
                        "id": "SlimeBoss",
                        "current_hp": 73,
                        "max_hp": 140,
                        "move": {"damage": 28},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 28)
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.attacks_removed, 28)
        self.assertTrue(result.avoided_lethal)
        self.assertEqual(result.first_card_key, "Strike_R")
        self.assertIn("attacks_removed=28", result.reason)
        self.assertIn("avoided_lethal=True", result.reason)

    def test_target_choice_prefers_split_that_removes_attack(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 20,
            "combat_state": {
                "player": {"current_hp": 20, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Defend",
                        "id": "Defend_R",
                        "type": "SKILL",
                        "cost": 1,
                        "block": 5,
                        "is_playable": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Jaw Worm",
                        "id": "JawWorm",
                        "current_hp": 40,
                        "max_hp": 40,
                        "move": {"damage": 18},
                    },
                    {
                        "name": "Acid Slime",
                        "id": "AcidSlime_L",
                        "current_hp": 38,
                        "max_hp": 70,
                        "move": {"damage": 12},
                    },
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 30)
        self.assertEqual(result.projected_loss, 18)
        self.assertEqual(result.attacks_removed, 12)
        self.assertTrue(result.avoided_lethal)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 2})

    def test_disarm_reduces_multihit_attack_in_search(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 10,
            "combat_state": {
                "player": {"current_hp": 10, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {
                        "name": "Disarm",
                        "id": "Disarm",
                        "type": "SKILL",
                        "cost": 1,
                        "has_target": True,
                        "is_playable": True,
                    },
                    {
                        "name": "Defend",
                        "id": "Defend_R",
                        "type": "SKILL",
                        "cost": 1,
                        "block": 5,
                        "is_playable": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Hexaghost",
                        "id": "Hexaghost",
                        "current_hp": 160,
                        "max_hp": 250,
                        "move": {"hits": 6, "damage": 3},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 18)
        self.assertEqual(result.projected_loss, 1)
        self.assertEqual(result.attacks_removed, 12)
        self.assertTrue(result.avoided_lethal)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 1})

    def test_disarm_respects_artifact_in_search(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 10,
            "combat_state": {
                "player": {"current_hp": 10, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Disarm",
                        "id": "Disarm",
                        "type": "SKILL",
                        "cost": 1,
                        "has_target": True,
                        "is_playable": True,
                    },
                    {
                        "name": "Defend",
                        "id": "Defend_R",
                        "type": "SKILL",
                        "cost": 1,
                        "block": 5,
                        "is_playable": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Lagavulin",
                        "id": "Lagavulin",
                        "current_hp": 100,
                        "max_hp": 110,
                        "move": {"damage": 18},
                        "powers": [{"id": "Artifact", "amount": 1}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 18)
        self.assertEqual(result.projected_loss, 13)
        self.assertFalse(result.avoided_lethal)
        self.assertEqual(result.first_card_key, "Defend_R")

    def test_disarm_plus_name_uses_upgraded_strength_down(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 10,
            "combat_state": {
                "player": {"current_hp": 10, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Disarm+",
                        "id": "Disarm",
                        "type": "SKILL",
                        "cost": 1,
                        "has_target": True,
                        "is_playable": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Hexaghost",
                        "id": "Hexaghost",
                        "current_hp": 160,
                        "max_hp": 250,
                        "move": {"hits": 6, "damage": 3},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.attacks_removed, 18)
        self.assertEqual(result.first_card_key, "Disarm")

    def test_bash_vulnerable_boosts_followup_attack_in_search(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Bash",
                        "id": "Bash",
                        "type": "ATTACK",
                        "cost": 2,
                        "damage": 8,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Slaver",
                        "id": "SlaverBlue",
                        "current_hp": 17,
                        "max_hp": 48,
                        "move": {"damage": 0},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Bash", "Strike_R"))
        self.assertEqual(result.kills, 1)

    def test_bash_vulnerable_respects_artifact_in_search(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Bash",
                        "id": "Bash",
                        "type": "ATTACK",
                        "cost": 2,
                        "damage": 8,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Lagavulin",
                        "id": "Lagavulin",
                        "current_hp": 17,
                        "max_hp": 110,
                        "move": {"damage": 0},
                        "powers": [{"id": "Artifact", "amount": 1}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Bash", "Strike_R"))
        self.assertEqual(result.kills, 0)

    def test_uppercut_single_artifact_still_applies_vulnerable(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Uppercut",
                        "id": "Uppercut",
                        "type": "ATTACK",
                        "cost": 2,
                        "damage": 13,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Lagavulin",
                        "id": "Lagavulin",
                        "current_hp": 22,
                        "max_hp": 110,
                        "move": {"damage": 0},
                        "powers": [{"id": "Artifact", "amount": 1}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Uppercut", "Strike_R"))
        self.assertEqual(result.kills, 1)

    def test_uppercut_two_artifact_blocks_weak_and_vulnerable(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Uppercut",
                        "id": "Uppercut",
                        "type": "ATTACK",
                        "cost": 2,
                        "damage": 13,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Lagavulin",
                        "id": "Lagavulin",
                        "current_hp": 22,
                        "max_hp": 110,
                        "move": {"damage": 0},
                        "powers": [{"id": "Artifact", "amount": 2}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Uppercut", "Strike_R"))
        self.assertEqual(result.kills, 0)

    def test_thunderclap_applies_vulnerable_for_followup_attack(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {
                        "name": "Thunderclap",
                        "id": "Thunderclap",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 4,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {"name": "Slaver", "id": "SlaverBlue", "current_hp": 13, "max_hp": 48, "move": {"damage": 0}},
                    {"name": "Louse", "id": "FuzzyLouseNormal", "current_hp": 13, "max_hp": 13, "move": {"damage": 0}},
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Thunderclap", "Strike_R"))
        self.assertEqual(result.kills, 1)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1})

    def test_thunderclap_vulnerable_respects_artifact_in_search(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {
                        "name": "Thunderclap",
                        "id": "Thunderclap",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 4,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Lagavulin",
                        "id": "Lagavulin",
                        "current_hp": 13,
                        "max_hp": 110,
                        "move": {"damage": 0},
                        "powers": [{"id": "Artifact", "amount": 1}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Thunderclap", "Strike_R"))
        self.assertEqual(result.kills, 0)

    def test_thunderclap_plus_id_still_counts_as_aoe(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Thunderclap+",
                        "id": "Thunderclap+",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 4,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {"name": "Slaver", "id": "SlaverBlue", "current_hp": 4, "max_hp": 48, "move": {"damage": 0}},
                    {"name": "Louse", "id": "FuzzyLouseNormal", "current_hp": 4, "max_hp": 13, "move": {"damage": 0}},
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Thunderclap",))
        self.assertEqual(result.kills, 2)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1})

    def test_shockwave_applies_vulnerable_for_followup_attack(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Shockwave",
                        "id": "Shockwave",
                        "type": "SKILL",
                        "cost": 2,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {"name": "Slaver", "id": "SlaverBlue", "current_hp": 9, "max_hp": 48, "move": {"damage": 0}},
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.sequence_card_keys, ("Shockwave", "Strike_R"))
        self.assertEqual(result.kills, 1)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1})

    def test_shockwave_reduces_multihit_attack_in_search(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 10,
            "combat_state": {
                "player": {"current_hp": 10, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Shockwave",
                        "id": "Shockwave",
                        "type": "SKILL",
                        "cost": 2,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Hexaghost",
                        "id": "Hexaghost",
                        "current_hp": 160,
                        "max_hp": 250,
                        "move": {"hits": 6, "damage": 3},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 18)
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.attacks_removed, 18)
        self.assertTrue(result.avoided_lethal)
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1})

    def test_shockwave_strength_down_respects_stacked_artifact(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 10,
            "combat_state": {
                "player": {"current_hp": 10, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {
                        "name": "Shockwave",
                        "id": "Shockwave",
                        "type": "SKILL",
                        "cost": 2,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Defend",
                        "id": "Defend_R",
                        "type": "SKILL",
                        "cost": 1,
                        "block": 5,
                        "is_playable": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Lagavulin",
                        "id": "Lagavulin",
                        "current_hp": 100,
                        "max_hp": 110,
                        "move": {"damage": 18},
                        "powers": [{"id": "Artifact", "amount": 3}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 18)
        self.assertEqual(result.projected_loss, 13)
        self.assertEqual(result.attacks_removed, 0)
        self.assertFalse(result.avoided_lethal)

    def test_shockwave_plus_id_uses_upgraded_strength_down(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 5,
            "combat_state": {
                "player": {"current_hp": 5, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {
                        "name": "Shockwave+",
                        "id": "Shockwave+",
                        "type": "SKILL",
                        "cost": 2,
                        "is_playable": True,
                        "has_target": False,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Hexaghost",
                        "id": "Hexaghost",
                        "current_hp": 160,
                        "max_hp": 250,
                        "move": {"hits": 6, "damage": 6},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.initial_loss, 36)
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.attacks_removed, 36)
        self.assertTrue(result.avoided_lethal)
        self.assertEqual(result.first_card_key, "Shockwave")

    def test_sharp_hide_reflect_counts_in_projected_loss_for_safe_lethal(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 9,
            "combat_state": {
                "player": {"current_hp": 9, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {
                        "name": "Pommel Strike",
                        "id": "Pommel Strike",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 9,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "The Guardian",
                        "id": "TheGuardian",
                        "current_hp": 10,
                        "max_hp": 240,
                        "move": {"hits": 2, "damage": 6},
                        "powers": [{"id": "Sharp Hide", "amount": 3}],
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 1})
        self.assertEqual(result.sequence_card_keys, ("Pommel Strike", "Strike_R"))
        self.assertEqual(result.kills, 1)
        self.assertEqual(result.projected_loss, 3)
        self.assertTrue(result.avoided_lethal)

    def test_flame_barrier_retaliation_is_valued_against_multihit_boss(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "current_hp": 40,
            "combat_state": {
                "player": {"current_hp": 40, "max_hp": 80, "current_energy": 2, "block": 0},
                "hand": [
                    {
                        "name": "Impervious",
                        "id": "Impervious",
                        "type": "SKILL",
                        "cost": 2,
                        "block": 30,
                        "is_playable": True,
                    },
                    {
                        "name": "Flame Barrier",
                        "id": "Flame Barrier",
                        "type": "SKILL",
                        "cost": 2,
                        "block": 12,
                        "is_playable": True,
                    },
                ],
                "monsters": [
                    {
                        "name": "Hexaghost",
                        "id": "Hexaghost",
                        "current_hp": 250,
                        "max_hp": 250,
                        "move": {"hits": 6, "damage": 2},
                    }
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.first_card_key, "Flame Barrier")
        self.assertEqual(result.projected_loss, 0)
        self.assertEqual(result.retaliation_damage, 24)
        self.assertIn("retaliation_damage=24", result.reason)

    def test_low_hp_prefers_non_self_damage_kill_when_fight_continues(self):
        game = {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "floor": 16,
            "act": 1,
            "current_hp": 8,
            "max_hp": 95,
            "combat_state": {
                "turn": 10,
                "player": {"current_hp": 8, "max_hp": 95, "current_energy": 2, "block": 0},
                "hand": [
                    {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                    {
                        "name": "Hemokinesis",
                        "id": "Hemokinesis",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 17,
                        "is_playable": True,
                    },
                ],
                "monsters": [
                    {"name": "Spike Slime", "id": "SpikeSlime_L", "current_hp": 43, "max_hp": 73, "move": {"damage": 0}},
                    {"name": "Acid Slime", "id": "AcidSlime_M", "current_hp": 1, "max_hp": 28, "move": {"damage": 7}},
                ],
            },
        }

        result = find_best_combat_sequence(game)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.first_card_key, "Strike_R")
        self.assertEqual(result.first_action, {"action": "play_card", "card_index": 1, "target_index": 2})


if __name__ == "__main__":
    unittest.main()
