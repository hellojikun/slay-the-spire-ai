import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from slay_ai.decision_shadow_disagreement import (
    evaluate_live_shadow_disagreements,
    extract_live_shadow_examples,
    extract_purge_remove_examples,
    extract_reward_take_skip_examples,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class DecisionShadowDisagreementTests(unittest.TestCase):
    def test_extracts_and_scores_live_reward_and_purge_disagreements(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(log, live_rows())

            reward_rows = extract_reward_take_skip_examples([log])
            self.assertEqual([row["decision"] for row in reward_rows], ["take", "skip"])
            self.assertEqual(reward_rows[0]["picked"], "shrugitoff")
            self.assertEqual(reward_rows[1]["option_count"], 2)

            purge_rows = extract_purge_remove_examples([log])
            self.assertEqual([row["decision"] for row in purge_rows], ["remove", "keep_candidate", "remove"])
            self.assertEqual(purge_rows[0]["label_source"], "live_shop_purge_selected")
            self.assertEqual(purge_rows[1]["label_source"], "live_shop_purge_not_selected")
            self.assertEqual(purge_rows[2]["label_source"], "live_grid_purge_selected")

            all_rows = extract_live_shadow_examples([log])
            self.assertEqual(len(all_rows), 5)

            with patch("slay_ai.decision_shadow_disagreement.score_row", side_effect=[0.82, 0.81, 0.9, 0.88, 0.3]):
                summary = evaluate_live_shadow_disagreements(
                    [log],
                    model_path=root / "decision_multitask_model.pt",
                    output_path=root / "live_shadow_disagreements.jsonl",
                    summary_output=root / "live_shadow_disagreement_summary.json",
                )

            self.assertEqual(summary["status"], "evaluated")
            self.assertEqual(summary["examples"], 5)
            self.assertEqual(summary["task_counts"], {"purge_remove": 3, "take_skip": 2})
            self.assertEqual(summary["disagreements"], 3)
            self.assertEqual(summary["disagreements_by_task"], {"purge_remove": 2, "take_skip": 1})
            self.assertEqual(summary["high_confidence_disagreements"], 2)
            self.assertEqual(summary["actual_counts"], {"keep_candidate": 1, "remove": 2, "skip": 1, "take": 1})
            self.assertFalse(summary["model_authority"]["runtime_authority"])
            self.assertFalse(summary["model_authority"]["direct_mcp_control"])
            self.assertEqual(summary["supported_surfaces"]["purge_remove"]["examples"], 3)
            self.assertEqual(summary["promotion_readiness"]["status"], "shadow_only")
            self.assertFalse(summary["promotion_readiness"]["can_promote_to_assist"])
            self.assertIn("insufficient_live_purge_examples", summary["promotion_readiness"]["blocking_reasons"])
            self.assertNotIn("purge_live_shadow_not_evaluated", summary["promotion_readiness"]["blocking_reasons"])

            records = [
                json.loads(line)
                for line in (root / "live_shadow_disagreements.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[1]["actual_decision"], "skip")
            self.assertEqual(records[1]["predicted_decision"], "take")
            self.assertFalse(records[1]["correct"])
            self.assertEqual(records[3]["actual_decision"], "keep_candidate")
            self.assertEqual(records[3]["predicted_decision"], "remove")
            self.assertFalse(records[3]["correct"])
            self.assertEqual(records[4]["actual_decision"], "remove")
            self.assertEqual(records[4]["predicted_decision"], "keep_candidate")
            self.assertFalse(records[4]["correct"])
            self.assertFalse(records[1]["model_authority"]["runtime_authority"])


def live_rows() -> list[dict]:
    deck = [
        {"id": "Strike_R", "type": "ATTACK", "cost": 1},
        {"id": "Strike_R", "type": "ATTACK", "cost": 1},
        {"id": "Defend_R", "type": "SKILL", "cost": 1},
        {"id": "Bash", "type": "ATTACK", "cost": 2},
    ]
    return [
        {
            "step": 3,
            "state": {
                "screen_type": "CARD_REWARD",
                "floor": 1,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "deck_cards": deck,
                "card_reward_options": [
                    {"id": "Shrug It Off", "name": "Shrug It Off"},
                    {"id": "Clash", "name": "Clash"},
                ],
            },
            "decision": {
                "actions": [{"action": "choose", "choice_index": 1}],
                "learn_card_pick": "Shrug It Off",
            },
        },
        {
            "step": 8,
            "state": {
                "screen_type": "CARD_REWARD",
                "floor": 9,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "deck_cards": deck,
                "card_reward_options": [
                    {"id": "Clash", "name": "Clash"},
                    {"id": "Flex", "name": "Flex"},
                ],
            },
            "decision": {"actions": [{"action": "skip"}], "learn_card_pick": None},
        },
        {
            "step": 12,
            "state": shop_state(deck, floor=5),
            "decision": {
                "actions": [{"action": "choose", "choice_index": 1}],
                "reason": "Shop purge Strike for 75 gold; score 54.2.",
            },
        },
        {
            "step": 21,
            "state": shop_state(deck, floor=8),
            "decision": {
                "actions": [{"action": "choose", "choice_index": 2}],
                "reason": "Shop buy Shrug It Off for 75 gold; score 60.0.",
            },
        },
        {
            "step": 30,
            "state": {
                "screen_type": "GRID",
                "floor": 10,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "deck_cards": deck,
                "grid": {
                    "for_purge": True,
                    "selected_cards": [{"id": "Strike_R", "name": "Strike"}],
                    "num_cards": 1,
                    "confirm_up": False,
                },
            },
            "decision": {"actions": [{"action": "choose", "choice_index": 1}], "reason": "Grid choose Strike score 10.0."},
        },
    ]


def shop_state(deck: list[dict], *, floor: int) -> dict:
    return {
        "screen_type": "SHOP_SCREEN",
        "floor": floor,
        "act": 1,
        "class": "IRONCLAD",
        "ascension_level": 0,
        "gold": 120,
        "deck_cards": deck,
        "shop": {
            "purge_available": True,
            "purge_cost": 75,
            "cards": [{"id": "Shrug It Off", "name": "Shrug It Off", "price": 75}],
            "relics": [],
            "potions": [],
        },
    }


if __name__ == "__main__":
    unittest.main()