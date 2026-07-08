import unittest

from slay_ai.policy_card_reward import CardRewardPolicy


class FakeMemory:
    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return {
            "Anger": 56,
            "Barricade": 30,
            "Demon Form": 63,
            "Feel No Pain": 78,
            "Flex": 30,
            "Metallicize": 48,
            "Shrug It Off": 80,
            "Thunderclap": 56,
        }.get(name, 30)


class FakeLearnedMemory:
    scores = {
        "Power Through": {
            "card": "Power Through",
            "base_score": 40.0,
            "learned_delta": 0.0,
            "model_delta": 0.0,
            "total": 40.0,
        },
        "Anger": {
            "card": "Anger",
            "base_score": 30.0,
            "learned_delta": 5.0,
            "model_delta": 0.0,
            "total": 35.0,
        },
    }

    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return self.card_score_breakdown(name, character)["total"]

    def card_score_breakdown(self, name: str, character: str = "IRONCLAD") -> dict:
        return self.scores.get(
            name,
            {
                "card": name,
                "base_score": 30.0,
                "learned_delta": 0.0,
                "model_delta": 0.0,
                "total": 30.0,
            },
        )


class FakeThreeChoiceMemory:
    scores = {
        "Shrug It Off": {
            "card": "Shrug It Off",
            "base_score": 60.0,
            "learned_delta": 0.0,
            "model_delta": 0.0,
            "total": 60.0,
        },
        "Limit Break": {
            "card": "Limit Break",
            "base_score": 25.0,
            "learned_delta": 20.0,
            "model_delta": 0.0,
            "total": 45.0,
        },
        "Cleave": {
            "card": "Cleave",
            "base_score": 55.0,
            "learned_delta": 5.0,
            "model_delta": 0.0,
            "total": 60.0,
        },
    }

    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return self.card_score_breakdown(name, character)["total"]

    def card_score_breakdown(self, name: str, character: str = "IRONCLAD") -> dict:
        return self.scores[name]


class FakeNegativeSignalMemory:
    scores = {
        "Clash": {
            "card": "Clash",
            "base_score": 22.0,
            "learned_delta": 0.2,
            "model_delta": 0.0,
            "total": 22.2,
        },
        "Sever Soul": {
            "card": "Sever Soul",
            "base_score": 52.0,
            "learned_delta": -2.4,
            "model_delta": 0.0,
            "total": 49.6,
        },
        "Inflame": {
            "card": "Inflame",
            "base_score": 62.0,
            "learned_delta": -7.5,
            "model_delta": 0.0,
            "total": 54.5,
        },
    }

    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return self.card_score_breakdown(name, character)["total"]

    def card_score_breakdown(self, name: str, character: str = "IRONCLAD") -> dict:
        return self.scores[name]


class FakeSelfDamageEngineAssistMemory:
    scores = {
        "Armaments": {
            "card": "Armaments",
            "base_score": 60.0,
            "learned_delta": 0.0,
            "model_delta": 0.0,
            "total": 60.0,
        },
        "Brutality": {
            "card": "Brutality",
            "base_score": 33.0,
            "learned_delta": 28.0,
            "model_delta": 0.0,
            "total": 61.0,
        },
    }

    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return self.card_score_breakdown(name, character)["total"]

    def card_score_breakdown(self, name: str, character: str = "IRONCLAD") -> dict:
        return self.scores[name]


def card_policy() -> CardRewardPolicy:
    return CardRewardPolicy(FakeMemory())  # type: ignore[arg-type]


class CardRewardPolicyTests(unittest.TestCase):
    def test_picks_highest_scored_card(self):
        game = {
            "floor": 2,
            "screen_state": {
                "cards": [
                    {"name": "Flex"},
                    {"name": "Shrug It Off"},
                    {"name": "Anger"},
                ]
            },
        }

        decision = card_policy().decide_card_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Shrug It Off")

    def test_penalizes_exhaust_payoff_without_enabler(self):
        game = {
            "act": 1,
            "floor": 1,
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
                ]
            },
        }

        decision = card_policy().decide_card_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Anger")

    def test_early_unsupported_feel_no_pain_loses_to_frontload(self):
        game = {
            "act": 1,
            "floor": 1,
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
            ],
            "screen_state": {
                "cards": [
                    {"name": "localized feel no pain", "id": "Feel No Pain"},
                    {"name": "localized thunderclap", "id": "Thunderclap"},
                ]
            },
        }

        decision = card_policy().decide_card_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Thunderclap")

    def test_feel_no_pain_keeps_value_with_exhaust_enabler(self):
        game = {
            "act": 1,
            "floor": 4,
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "True Grit"},
            ],
            "screen_state": {
                "cards": [
                    {"name": "localized feel no pain", "id": "Feel No Pain"},
                    {"name": "localized anger", "id": "Anger"},
                ]
            },
        }

        decision = card_policy().decide_card_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.learn_card_pick, "Feel No Pain")

    def test_temporary_power_potion_choice_avoids_slow_power_under_pressure(self):
        game = {
            "screen_type": "CARD_REWARD",
            "room_phase": "COMBAT",
            "act": 2,
            "floor": 23,
            "current_hp": 25,
            "max_hp": 85,
            "combat": {
                "incoming_damage": 18,
                "player": {"current_hp": 25, "max_hp": 85, "block": 0},
                "monsters": [{"id": "BookOfStabbing", "move": {"damage": 18}}],
            },
            "screen_state": {
                "cards": [
                    {"id": "Barricade", "name": "Barricade", "type": "POWER"},
                    {"id": "Metallicize", "name": "Metallicize", "type": "POWER"},
                    {"id": "Demon Form", "name": "Demon Form", "type": "POWER"},
                ]
            },
        }

        decision = card_policy().decide_card_reward(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIsNone(decision.learn_card_pick)
        self.assertIn("temporary", decision.reason)

    def test_act1_boss_prep_needs_potion_when_deck_has_resource_gap(self):
        game = {
            "act": 1,
            "floor": 10,
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
            ],
            "potions": [],
        }

        self.assertTrue(card_policy().act1_boss_prep_needs_potion(game))

        game["potions"] = [{"id": "Fire Potion"}]
        self.assertFalse(card_policy().act1_boss_prep_needs_potion(game))

    def test_shadow_model_authority_records_metadata_without_overriding(self):
        game = {
            "floor": 2,
            "screen_state": {
                "cards": [
                    {"id": "Power Through", "name": "Power Through"},
                    {"id": "Anger", "name": "Anger"},
                ]
            },
        }

        decision = CardRewardPolicy(FakeLearnedMemory(), model_authority="shadow").decide_card_reward(game)  # type: ignore[arg-type]

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        authority = decision.metadata["model_authority"]
        self.assertEqual(authority["level"], "shadow")
        self.assertFalse(authority["runtime_authority"])
        self.assertEqual(authority["selection_source"], "heuristic_with_memory_model_score")

    def test_assist_model_authority_can_break_close_card_reward_tie(self):
        game = {
            "floor": 2,
            "screen_state": {
                "cards": [
                    {"id": "Power Through", "name": "Power Through"},
                    {"id": "Anger", "name": "Anger"},
                ]
            },
        }

        decision = CardRewardPolicy(FakeLearnedMemory(), model_authority="assist").decide_card_reward(game)  # type: ignore[arg-type]

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.learn_card_pick, "Anger")
        authority = decision.metadata["model_authority"]
        self.assertEqual(authority["level"], "assist")
        self.assertTrue(authority["runtime_authority"])
        self.assertEqual(authority["selection_source"], "model_authority_tiebreaker")
        self.assertEqual(authority["selected_choice_index"], 2)

    def test_assist_uses_best_eligible_model_signal_when_top_signal_is_too_far_behind(self):
        game = {
            "floor": 2,
            "screen_state": {
                "cards": [
                    {"id": "Shrug It Off", "name": "Shrug It Off"},
                    {"id": "Limit Break", "name": "Limit Break"},
                    {"id": "Cleave", "name": "Cleave"},
                ]
            },
        }

        decision = CardRewardPolicy(FakeThreeChoiceMemory(), model_authority="assist").decide_card_reward(game)  # type: ignore[arg-type]

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 3}])
        authority = decision.metadata["model_authority"]
        self.assertTrue(authority["runtime_authority"])
        self.assertEqual(authority["selected_choice_index"], 3)

    def test_assist_does_not_override_with_only_negative_model_signal(self):
        game = {
            "floor": 4,
            "act": 1,
            "screen_state": {
                "cards": [
                    {"id": "Clash", "name": "Clash"},
                    {"id": "Sever Soul", "name": "Sever Soul"},
                    {"id": "Inflame", "name": "Inflame"},
                ]
            },
        }

        decision = CardRewardPolicy(FakeNegativeSignalMemory(), model_authority="assist").decide_card_reward(game)  # type: ignore[arg-type]

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 3}])
        authority = decision.metadata["model_authority"]
        self.assertFalse(authority["runtime_authority"])
        self.assertEqual(authority["selection_source"], "heuristic_with_memory_model_score")

    def test_assist_does_not_override_to_self_damage_engine_before_act1_boss(self):
        game = {
            "act": 1,
            "floor": 14,
            "current_hp": 56,
            "max_hp": 80,
            "potions": [],
            "deck": [
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Strike_R"},
                {"id": "Defend_R"},
                {"id": "Defend_R"},
                {"id": "Bash"},
                {"id": "Carnage"},
            ],
            "screen_state": {
                "cards": [
                    {"id": "Armaments", "name": "Armaments"},
                    {"id": "Brutality", "name": "Brutality"},
                ]
            },
        }

        decision = CardRewardPolicy(FakeSelfDamageEngineAssistMemory(), model_authority="assist").decide_card_reward(game)  # type: ignore[arg-type]

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        authority = decision.metadata["model_authority"]
        self.assertFalse(authority["runtime_authority"])
        self.assertEqual(authority["selection_source"], "heuristic_with_memory_model_score")

    def test_invalid_model_authority_falls_back_to_shadow(self):
        policy = CardRewardPolicy(FakeLearnedMemory(), model_authority="assit")  # type: ignore[arg-type]
        game = {
            "floor": 2,
            "screen_state": {
                "cards": [
                    {"id": "Power Through", "name": "Power Through"},
                    {"id": "Anger", "name": "Anger"},
                ]
            },
        }

        decision = policy.decide_card_reward(game)

        self.assertEqual(decision.metadata["model_authority"]["level"], "shadow")
        self.assertFalse(decision.metadata["model_authority"]["runtime_authority"])


if __name__ == "__main__":
    unittest.main()
