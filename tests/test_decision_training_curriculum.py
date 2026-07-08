import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.decision_training_curriculum import build_decision_training_curriculum, write_decision_training_curriculum


class DecisionTrainingCurriculumTests(unittest.TestCase):
    def test_builds_collection_targets_from_live_shadow_gaps(self):
        summary = {
            "model_path": "model.pt",
            "prediction_path": "predictions.jsonl",
            "promotion_readiness": {
                "status": "shadow_only",
                "blocking_reasons": ["external_prior_only_requires_live_shadow_validation"],
            },
            "live_shadow_disagreement": {
                "summary_output": "live_summary.json",
                "task_counts": {"take_skip": 12, "purge_remove": 3},
                "actual_counts_by_task": {
                    "take_skip": {"take": 10, "skip": 2},
                    "purge_remove": {"remove": 1, "keep_candidate": 2},
                },
                "promotion_readiness": {
                    "blocking_reasons": ["insufficient_live_skip_examples", "insufficient_live_purge_examples"]
                },
            },
        }

        curriculum = build_decision_training_curriculum(summary)

        self.assertEqual(curriculum["status"], "active")
        self.assertEqual(curriculum["active_target_count"], 5)
        targets = {target["key"]: target for target in curriculum["targets"]}
        self.assertEqual(targets["reward_total"]["observed"], 12)
        self.assertEqual(targets["reward_total"]["deficit"], 38)
        self.assertEqual(targets["reward_skip"]["observed"], 2)
        self.assertEqual(targets["reward_skip"]["deficit"], 8)
        self.assertEqual(targets["purge_remove"]["observed"], 1)
        self.assertEqual(targets["purge_keep"]["observed"], 2)
        self.assertIn("prefer_runs_that_visit_shop_or_grid_purge_contexts", curriculum["next_training_actions"])
        self.assertFalse(curriculum["authority_boundary"]["runtime_authority"])
        self.assertFalse(curriculum["authority_boundary"]["direct_mcp_control"])
        self.assertIn("insufficient_live_purge_examples", curriculum["blocking_reasons"])

    def test_writes_curriculum_artifact(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "curriculum.json"
            payload = write_decision_training_curriculum(
                {"live_shadow_disagreement": {"task_counts": {}}},
                output_path=output,
            )

            self.assertTrue(output.exists())
            self.assertEqual(payload["output_path"], str(output))
            self.assertEqual(payload["status"], "active")


if __name__ == "__main__":
    unittest.main()