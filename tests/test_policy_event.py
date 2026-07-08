import unittest

from slay_ai.policy_event import EventPolicy


class FakeMemory:
    def __init__(self, scores=None) -> None:
        self.scores = scores or {}

    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return float(self.scores.get(name, 0.0))


def event_policy(scores=None) -> EventPolicy:
    return EventPolicy(FakeMemory(scores), character="IRONCLAD")


class EventPolicyTests(unittest.TestCase):
    def test_hand_select_drops_lowest_value_cards(self):
        game = {
            "screen_state": {
                "hand": [
                    {"id": "Bash", "name": "Bash"},
                    {"id": "Strike_R", "name": "Strike"},
                    {"id": "Defend_R", "name": "Defend"},
                ],
                "max_cards": 2,
            }
        }

        decision = event_policy({"Bash": 8, "Strike_R": 1, "Defend_R": 3}).decide_hand_select(game)

        self.assertEqual(decision.actions, [{"action": "select_cards", "drop": [2, 3]}])

    def test_low_hp_event_prefers_leave_over_fight(self):
        game = {
            "current_hp": 22,
            "max_hp": 80,
            "screen_state": {
                "options": [
                    {"label": "Stomp", "text": "Stomp. Fight.", "choice_index": 0},
                    {"label": "Leave", "text": "Leave.", "choice_index": 1},
                ]
            },
        }

        decision = event_policy().decide_event(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_critical_hp_event_avoids_mojibake_life_loss(self):
        game = {
            "current_hp": 8,
            "max_hp": 88,
            "screen_state": {
                "options": [
                    {
                        "label": "coins",
                        "text": "[coins] gain 75 gold ʧȥ 11 ������",
                        "choice_index": 0,
                    },
                    {
                        "label": "safe",
                        "text": "[safe] lose 35 gold",
                        "choice_index": 1,
                    },
                ]
            },
        }

        decision = event_policy().decide_event(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])

    def test_disabled_event_options_are_ignored(self):
        game = {
            "current_hp": 80,
            "max_hp": 80,
            "screen_state": {
                "options": [
                    {"label": "Disabled relic", "text": "Gain relic.", "choice_index": 0, "disabled": True},
                    {"label": "Leave", "text": "Leave.", "choice_index": 1},
                ]
            },
        }

        decision = event_policy().decide_event(game)

        self.assertEqual(decision.actions, [{"action": "choose", "choice_index": 2}])


if __name__ == "__main__":
    unittest.main()
