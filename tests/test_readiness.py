import unittest

from slay_ai.readiness import act1_readiness


def starting_ironclad_deck() -> list[dict[str, str]]:
    return [
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
    ]


class FakeKnowledge:
    def card_for(self, card):
        card_id = card.get("id") if isinstance(card, dict) else card
        if card_id == "CustomBlock":
            return {
                "id": "CustomBlock",
                "name": "Custom Block",
                "type": "SKILL",
                "tags": ["block", "premium_block"],
                "values": {"base_block": 16},
            }
        return None

    def potion_for(self, potion):
        potion_id = potion.get("id") if isinstance(potion, dict) else potion
        if potion_id == "CustomTempoPotion":
            return {
                "id": "CustomTempoPotion",
                "name": "Custom Tempo Potion",
                "roles": ["elite_tempo"],
                "values": {"damage": 20},
            }
        return None


class Act1TagKnowledge:
    cards = {
        "Strike_R": {
            "id": "Strike_R",
            "type": "ATTACK",
            "tags": ["damage", "starter", "low_value"],
            "values": {"base_damage": 6},
        },
        "Defend_R": {
            "id": "Defend_R",
            "type": "SKILL",
            "tags": ["block", "starter", "low_value"],
            "values": {"base_block": 5},
        },
        "Bash": {
            "id": "Bash",
            "type": "ATTACK",
            "tags": ["damage", "vulnerable", "starter"],
            "values": {"base_damage": 8, "vulnerable": 2},
        },
        "Intimidate": {
            "id": "Intimidate",
            "type": "SKILL",
            "tags": ["weak", "aoe_debuff", "zero_cost", "defense"],
            "values": {"weak": 1},
        },
        "Headbutt": {
            "id": "Headbutt",
            "type": "ATTACK",
            "tags": ["damage"],
            "values": {"base_damage": 9},
        },
        "Pommel Strike": {
            "id": "Pommel Strike",
            "type": "ATTACK",
            "tags": ["damage", "draw", "frontload"],
            "values": {"base_damage": 9, "draw": 1},
        },
        "Shrug It Off": {
            "id": "Shrug It Off",
            "type": "SKILL",
            "tags": ["block", "draw", "premium_defense"],
            "values": {"base_block": 8, "draw": 1},
        },
    }
    potions = {
        "CultistPotion": {
            "id": "Cultist Potion",
            "roles": ["offense", "scaling", "boss_damage", "elite_tempo", "long_fight"],
            "values": {"ritual": 1},
        },
        "FirePotion": {
            "id": "Fire Potion",
            "roles": ["offense", "lethal", "frontload", "elite_tempo"],
            "values": {"damage": 20},
        },
    }

    def card_for(self, card):
        card_id = card.get("id") if isinstance(card, dict) else card
        return self.cards.get(card_id)

    def potion_for(self, potion):
        potion_id = potion.get("id") if isinstance(potion, dict) else potion
        return self.potions.get(potion_id)


class ReadinessTests(unittest.TestCase):
    def test_low_hp_starting_deck_is_not_elite_ready(self):
        state = {
            "in_game": True,
            "game_state": {
                "act": 1,
                "floor": 7,
                "class": "IRONCLAD",
                "current_hp": 32,
                "max_hp": 88,
                "deck": starting_ironclad_deck(),
                "potions": [],
                "screen_state": {
                    "next_nodes": [
                        {"symbol": "E", "x": 1, "y": 7},
                        {"symbol": "?", "x": 2, "y": 7},
                    ]
                },
            },
        }

        result = act1_readiness(state)

        self.assertLess(result["scores"]["elite"], 55)
        self.assertIn("defense_density_low", result["gaps"])
        self.assertIn("premium_block_missing", result["gaps"])
        self.assertIn("elite_not_ready", result["risk_flags"])
        self.assertIn("elite_low_hp_no_tempo_potion", result["risk_flags"])

    def test_boss_ready_deck_scores_well_with_block_and_potions(self):
        deck = starting_ironclad_deck() + [
            {"id": "Shrug It Off"},
            {"id": "Flame Barrier"},
            {"id": "Carnage"},
            {"id": "Thunderclap"},
            {"id": "Uppercut"},
            {"id": "Cleave"},
        ]
        state = {
            "game_state": {
                "act": 1,
                "floor": 15,
                "class": "IRONCLAD",
                "current_hp": 76,
                "max_hp": 88,
                "deck": deck,
                "potions": [{"id": "FirePotion"}, {"id": "DexterityPotion"}],
                "screen_state": {"boss_available": True},
            }
        }

        result = act1_readiness(state)

        self.assertGreaterEqual(result["scores"]["boss"], 65)
        self.assertGreaterEqual(result["scores"]["defense"], 60)
        self.assertGreaterEqual(result["scores"]["potion"], 50)
        self.assertNotIn("boss_not_ready", result["risk_flags"])
        self.assertNotIn("boss_lacks_premium_block", result["risk_flags"])

    def test_counts_aoe_weak_and_vulnerable_sources(self):
        deck = starting_ironclad_deck() + [
            {"id": "Cleave"},
            {"id": "Thunderclap"},
            {"id": "Clothesline"},
            {"id": "Uppercut"},
        ]
        result = act1_readiness(
            {
                "act": 1,
                "floor": 8,
                "current_hp": 70,
                "max_hp": 88,
                "deck": deck,
                "potions": [],
            }
        )

        features = result["features"]
        self.assertEqual(features["aoe_cards"], 2)
        self.assertEqual(features["weak_sources"], 2)
        self.assertEqual(features["vulnerable_sources"], 3)
        self.assertGreaterEqual(result["scores"]["aoe"], 50)
        self.assertGreaterEqual(result["scores"]["debuff"], 60)

    def test_optional_knowledge_enriches_custom_cards_and_potions(self):
        state = {
            "game_state": {
                "act": 1,
                "floor": 6,
                "current_hp": 60,
                "max_hp": 80,
                "deck": starting_ironclad_deck() + [{"id": "CustomBlock"}],
                "potions": [{"id": "CustomTempoPotion"}],
                "screen_state": {"next_nodes": [{"symbol": "E", "x": 1, "y": 6}]},
            }
        }

        result = act1_readiness(state, knowledge=FakeKnowledge())

        self.assertGreaterEqual(result["features"]["premium_block_cards"], 1)
        self.assertGreaterEqual(result["features"]["potion_damage_value"], 20)
        self.assertGreaterEqual(result["features"]["high_impact_potions"], 1)
        self.assertNotIn("elite_low_hp_no_tempo_potion", result["risk_flags"])

    def test_act2_context_is_flagged_but_still_scores(self):
        result = act1_readiness(
            {
                "act": 2,
                "floor": 20,
                "current_hp": 45,
                "max_hp": 80,
                "deck": starting_ironclad_deck(),
                "potions": [],
            }
        )

        self.assertIn("non_act1_context", result["risk_flags"])
        self.assertIn("overall", result["scores"])

    def test_act2_low_hp_forced_combat_route_is_flagged(self):
        state = {
            "act": 2,
            "floor": 20,
            "current_hp": 22,
            "max_hp": 88,
            "deck": starting_ironclad_deck() + [{"id": "Shrug It Off"}, {"id": "Cleave"}],
            "potions": [],
            "map_options": [{"symbol": "M", "x": 0, "y": 3}],
            "route_evaluation": {
                "options": [
                    {
                        "choice_index": 1,
                        "symbol": "M",
                        "score": 42.0,
                        "lookahead": {
                            "forced_combat_within_2": True,
                            "forced_combat_within_4": True,
                            "nearest_rest": 2,
                            "nearest_shop": 6,
                        },
                    }
                ]
            },
        }

        result = act1_readiness(state)

        self.assertIn("non_act1_context", result["risk_flags"])
        self.assertIn("act2_critical_hp_forced_combat", result["risk_flags"])
        self.assertIn("act2_no_recovery_buffer", result["risk_flags"])
        self.assertIn("act2_no_emergency_potion", result["risk_flags"])
        self.assertIn("act2_defense_gap", result["risk_flags"])
        self.assertIn("act2_weak_gap", result["risk_flags"])
        self.assertIn("prefer_act2_recovery_or_safe_event", result["recommendations"])

    def test_forced_elite_flags_aoe_weak_and_potion_gaps_before_immediate_elite(self):
        state = {
            "act": 1,
            "floor": 3,
            "class": "IRONCLAD",
            "current_hp": 82,
            "max_hp": 88,
            "deck": starting_ironclad_deck() + [{"id": "Shrug It Off"}, {"id": "True Grit"}],
            "potions": [],
            "map_options": [{"symbol": "M", "x": 0, "y": 3}, {"symbol": "$", "x": 1, "y": 3}],
            "route_evaluation": {
                "options": [
                    {
                        "choice_index": 1,
                        "symbol": "M",
                        "score": 30.0,
                        "lookahead": {"forced_elite_within_3": True, "forced_elite_within_5": True},
                    },
                    {
                        "choice_index": 2,
                        "symbol": "$",
                        "score": 13.0,
                        "lookahead": {"forced_elite_within_3": False, "forced_elite_within_5": False},
                    },
                ]
            },
        }

        result = act1_readiness(state)

        self.assertFalse(result["features"]["elite_available"])
        self.assertTrue(result["features"]["forced_elite_within_3"])
        self.assertIn("forced_elite_aoe_gap", result["risk_flags"])
        self.assertIn("forced_elite_weak_gap", result["risk_flags"])
        self.assertIn("forced_elite_no_tempo_potion", result["risk_flags"])
        self.assertIn("prioritize_aoe_before_forced_elite", result["recommendations"])
        self.assertIn("seek_or_save_elite_tempo_potion", result["recommendations"])

    def test_sentries_threat_marks_specific_weak_gap_without_overriding_aoe_or_potion(self):
        state = {
            "act": 1,
            "floor": 10,
            "class": "IRONCLAD",
            "current_hp": 60,
            "max_hp": 88,
            "deck": starting_ironclad_deck() + [{"id": "Cleave"}, {"id": "Shrug It Off"}],
            "potions": [{"id": "FirePotion"}],
            "combat_state": {
                "monsters": [
                    {"id": "Sentry", "current_hp": 39},
                    {"id": "Sentry", "current_hp": 39},
                    {"id": "Sentry", "current_hp": 39},
                ]
            },
        }

        result = act1_readiness(state)

        self.assertTrue(result["features"]["sentries_threat"])
        self.assertIn("sentries_no_weak", result["risk_flags"])
        self.assertNotIn("sentries_no_aoe", result["risk_flags"])
        self.assertNotIn("sentries_no_tempo_potion", result["risk_flags"])

    def test_cultist_potion_counts_as_scaling_tempo_without_static_knowledge(self):
        state = {
            "act": 1,
            "floor": 9,
            "class": "IRONCLAD",
            "current_hp": 45,
            "max_hp": 88,
            "deck": starting_ironclad_deck() + [{"id": "Clothesline"}],
            "potions": [{"id": "CultistPotion"}],
            "map_options": [{"symbol": "E", "x": 0, "y": 9}],
        }

        result = act1_readiness(state)

        self.assertEqual(result["features"]["high_impact_potions"], 1)
        self.assertEqual(result["features"]["offensive_potions"], 1)
        self.assertNotIn("elite_potion_missing", result["gaps"])

    def test_static_block_tag_does_not_make_starter_defend_premium(self):
        result = act1_readiness(
            {
                "act": 1,
                "floor": 4,
                "current_hp": 88,
                "max_hp": 88,
                "deck": starting_ironclad_deck(),
                "potions": [],
            },
            knowledge=Act1TagKnowledge(),
        )

        self.assertEqual(result["features"]["block_cards"], 4)
        self.assertEqual(result["features"]["premium_block_cards"], 0)
        self.assertIn("premium_block_missing", result["gaps"])

    def test_probe74_low_buffer_hallway_without_immediate_tempo_is_flagged(self):
        deck = starting_ironclad_deck() + [
            {"id": "Intimidate"},
            {"id": "Headbutt"},
            {"id": "Pommel Strike"},
        ]
        state = {
            "act": 1,
            "floor": 9,
            "class": "IRONCLAD",
            "current_hp": 55,
            "max_hp": 95,
            "deck": deck,
            "potions": [{"id": "CultistPotion"}],
            "map_options": [{"symbol": "M", "x": 1, "y": 9}],
            "route_evaluation": {
                "options": [
                    {
                        "choice_index": 1,
                        "symbol": "M",
                        "score": 52.0,
                        "lookahead": {
                            "forced_combat_within_2": True,
                            "forced_combat_within_4": True,
                            "nearest_rest": 5,
                            "nearest_shop": None,
                        },
                    }
                ]
            },
        }

        result = act1_readiness(state, knowledge=Act1TagKnowledge())

        self.assertEqual(result["features"]["nearest_rest"], 5)
        self.assertEqual(result["features"]["immediate_tempo_potions"], 0)
        self.assertEqual(result["features"]["scaling_potions"], 1)
        self.assertIn("act1_low_buffer_no_recovery", result["risk_flags"])
        self.assertIn("hallway_no_immediate_tempo_potion", result["risk_flags"])
        self.assertIn("hallway_lacks_premium_block", result["risk_flags"])
        self.assertIn("seek_or_save_hallway_tempo_potion", result["recommendations"])

    def test_low_buffer_immediate_hallway_before_rest_is_flagged(self):
        state = {
            "act": 1,
            "floor": 4,
            "class": "IRONCLAD",
            "current_hp": 53,
            "max_hp": 80,
            "deck": starting_ironclad_deck(),
            "potions": [],
            "map_options": [{"symbol": "M", "x": 1, "y": 4}],
            "route_evaluation": {
                "options": [
                    {
                        "choice_index": 1,
                        "symbol": "M",
                        "score": 62.0,
                        "lookahead": {
                            "forced_combat_within_2": True,
                            "forced_combat_within_4": True,
                            "nearest_rest": 1,
                            "nearest_shop": 5,
                        },
                    }
                ]
            },
        }

        result = act1_readiness(state, knowledge=Act1TagKnowledge())

        self.assertIn("act1_low_buffer_no_recovery", result["risk_flags"])
        self.assertIn("hallway_no_immediate_tempo_potion", result["risk_flags"])
        self.assertIn("hallway_lacks_premium_block", result["risk_flags"])


if __name__ == "__main__":
    unittest.main()
