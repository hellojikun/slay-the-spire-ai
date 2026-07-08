import unittest

from slay_ai.policy_rest import RestGridPolicy


class FakeMemory:
    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return {"Strike": 10, "Defend": 18, "Bash": 68}.get(name, 30)

    def upgrade_score(self, name: str, character: str = "IRONCLAD") -> float:
        return {"Bash": 90, "Defend": 20}.get(name, 30)


def rest_grid_policy() -> RestGridPolicy:
    return RestGridPolicy(FakeMemory())  # type: ignore[arg-type]


class RestGridPolicyTests(unittest.TestCase):
    def test_rest_low_hp_chooses_rest(self):
        game = {
            "screen_type": "REST",
            "act": 1,
            "floor": 6,
            "current_hp": 30,
            "max_hp": 80,
            "screen_state": {"rest_options": ["Smith", "Rest"]},
        }

        decision = rest_grid_policy().decide_rest(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.reason, "Low HP; rest.")

    def test_rest_safe_hp_chooses_smith(self):
        game = {
            "screen_type": "REST",
            "act": 1,
            "floor": 6,
            "current_hp": 75,
            "max_hp": 80,
            "potions": [{"id": "Fire Potion"}],
            "screen_state": {"rest_options": ["Rest", "Smith"]},
        }

        decision = rest_grid_policy().decide_rest(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.reason, "HP is safe; smith.")

    def test_act2_late_low_buffer_chooses_rest(self):
        game = {
            "screen_type": "REST",
            "act": 2,
            "floor": 23,
            "current_hp": 57,
            "max_hp": 88,
            "screen_state": {"rest_options": ["Rest", "Smith"]},
        }

        decision = rest_grid_policy().decide_rest(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(decision.reason, "Act 2 buffer is low; rest.")

    def test_act2_boss_approach_injured_chooses_rest(self):
        game = {
            "screen_type": "REST",
            "act": 2,
            "floor": 32,
            "current_hp": 51,
            "max_hp": 97,
            "screen_state": {"rest_options": ["Smith", "Rest"]},
        }

        decision = rest_grid_policy().decide_rest(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.reason, "Act 2 buffer is low; rest.")

    def test_act2_late_high_buffer_still_smiths(self):
        game = {
            "screen_type": "REST",
            "act": 2,
            "floor": 25,
            "current_hp": 72,
            "max_hp": 88,
            "screen_state": {"rest_options": ["Rest", "Smith"]},
        }

        decision = rest_grid_policy().decide_rest(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(decision.reason, "HP is safe; smith.")

    def test_grid_upgrade_chooses_highest_upgrade_score(self):
        game = {
            "screen_type": "GRID",
            "screen_state": {
                "for_upgrade": True,
                "num_cards": 1,
                "cards": [
                    {"id": "Defend_R", "name": "Defend"},
                    {"id": "Bash", "name": "Bash"},
                ],
                "selected_cards": [],
            },
        }

        decision = rest_grid_policy().decide_grid(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("Bash", decision.reason)

    def test_event_grid_skips_selected_card_ids(self):
        game = {
            "screen_type": "GRID",
            "room_phase": "EVENT",
            "screen_state": {
                "num_cards": 2,
                "cards": [
                    {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                    {"id": "Strike_R", "name": "Strike", "uuid": "strike-2"},
                    {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                    {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                ],
                "selected_cards": [
                    {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                ],
            },
        }

        decision = rest_grid_policy().decide_grid(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 3}])

    def test_bottle_grid_confirms_existing_selection_without_confirm_up(self):
        game = {
            "screen_type": "GRID",
            "room_phase": "COMPLETE",
            "screen_state": {
                "num_cards": 1,
                "confirm_up": False,
                "for_transform": False,
                "for_upgrade": False,
                "for_purge": False,
                "cards": [
                    {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                    {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                    {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                ],
                "selected_cards": [
                    {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                ],
            },
        }

        decision = rest_grid_policy().decide_grid(game)

        self.assertEqual(decision.actions, [{"action": "confirm"}])
        self.assertEqual(decision.reason, "Confirm grid selection.")

    def test_multiselect_transform_grid_continues_with_unselected_low_value_card(self):
        game = {
            "screen_type": "GRID",
            "room_phase": "COMPLETE",
            "screen_state": {
                "num_cards": 3,
                "confirm_up": False,
                "for_transform": False,
                "for_upgrade": False,
                "for_purge": False,
                "cards": [
                    {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                    {"id": "Defend_R", "name": "Defend", "uuid": "defend-1"},
                    {"id": "Bash", "name": "Bash", "uuid": "bash-1"},
                    {"id": "Offering", "name": "Offering", "uuid": "offering-1"},
                ],
                "selected_cards": [
                    {"id": "Strike_R", "name": "Strike", "uuid": "strike-1"},
                ],
            },
        }

        decision = rest_grid_policy().decide_grid(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])
        self.assertIn("Defend", decision.reason)


if __name__ == "__main__":
    unittest.main()
