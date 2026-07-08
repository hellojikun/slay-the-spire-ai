import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.train_decision_multitask_model import (
    TASK_DECK_CYCLE,
    TASK_PURGE_REMOVE,
    TASK_TAKE_SKIP,
    load_decision_rows,
    score_row,
    train_decision_multitask_model,
)
from slay_ai.train_external_structure_priors import (
    CARD_PURGE_PRIORS_FILE,
    CARD_REWARD_DECISIONS_FILE,
    DECK_CYCLE_PRIORS_FILE,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def base_row(**overrides: object) -> dict:
    row = {
        "ascension": 20,
        "character": "IRONCLAD",
        "final_floor": 57,
        "score": 3000,
        "source_category": "external_run_history",
        "source_dataset": "unit_source",
        "source_file": "unit.run",
        "source_index": 1,
        "source_reason": "external_prior",
        "source_uri": "https://example.test/unit",
        "source_validation_flags": ["external", "not_mcp", "not_pristine"],
        "source_validation_grade": "external_prior",
        "source_weight": 0.2,
        "transform_version": 1,
        "victory": True,
    }
    row.update(overrides)
    return row


class TrainDecisionMultitaskModelTests(unittest.TestCase):
    def test_trains_shadow_only_multitask_checkpoint_from_structure_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows_dir = root / "rows"
            model_path = root / "models" / "decision_multitask_model.pt"
            summary_path = root / "models" / "summary.json"
            prediction_path = root / "models" / "predictions.jsonl"
            write_jsonl(
                rows_dir / CARD_REWARD_DECISIONS_FILE,
                [
                    base_row(
                        floor=1,
                        decision="take",
                        picked="Battle Trance",
                        options=["Battle Trance", "Clash", "Flex"],
                        option_count=3,
                        transform_target="take_vs_skip",
                    ),
                    base_row(
                        floor=4,
                        decision="skip",
                        picked=None,
                        options=["Clash", "Fire Breathing", "Flex"],
                        option_count=3,
                        transform_target="take_vs_skip",
                    ),
                    base_row(
                        floor=7,
                        decision="take",
                        picked="Shrug It Off",
                        options=["Shrug It Off", "Wild Strike"],
                        option_count=2,
                        transform_target="take_vs_skip",
                    ),
                    base_row(
                        floor=9,
                        decision="skip",
                        picked=None,
                        options=["Anger", "Thunderclap"],
                        option_count=2,
                        transform_target="take_vs_skip",
                    ),
                ],
            )
            write_jsonl(
                rows_dir / CARD_PURGE_PRIORS_FILE,
                [
                    base_row(
                        floor=8,
                        purge_index=1,
                        removed="Strike_R",
                        removed_base="Strike_R",
                        purchased_purge_count=1,
                        likely_shop_purge=True,
                        transform_target="remove_vs_keep",
                    ),
                    base_row(
                        floor=14,
                        purge_index=2,
                        removed="Clash",
                        removed_base="Clash",
                        purchased_purge_count=2,
                        likely_shop_purge=True,
                        transform_target="remove_vs_keep",
                    ),
                ],
            )
            write_jsonl(
                rows_dir / DECK_CYCLE_PRIORS_FILE,
                [
                    base_row(
                        deck_size=12,
                        unique_card_count=9,
                        starter_count=4,
                        strike_count=1,
                        defend_count=2,
                        curse_count=0,
                        draw_card_count=3,
                        draw_density=0.25,
                        exhaust_card_count=2,
                        zero_cost_count=1,
                        attack_count=5,
                        skill_count=6,
                        power_count=1,
                        purge_count=2,
                        card_reward_take_count=6,
                        card_reward_skip_count=2,
                        card_reward_skip_rate=0.25,
                        starter_density=0.3333,
                        transform_target="draw_cycle_quality",
                    ),
                    base_row(
                        final_floor=12,
                        victory=False,
                        deck_size=24,
                        unique_card_count=13,
                        starter_count=9,
                        strike_count=5,
                        defend_count=4,
                        curse_count=1,
                        draw_card_count=1,
                        draw_density=0.0417,
                        exhaust_card_count=0,
                        zero_cost_count=0,
                        attack_count=10,
                        skill_count=12,
                        power_count=1,
                        purge_count=0,
                        card_reward_take_count=12,
                        card_reward_skip_count=0,
                        card_reward_skip_rate=0.0,
                        starter_density=0.375,
                        transform_target="draw_cycle_quality",
                    ),
                ],
            )

            result = train_decision_multitask_model(
                [rows_dir],
                model_path=model_path,
                summary_output=summary_path,
                prediction_output=prediction_path,
                epochs=2,
                hidden_dim=16,
                batch_size=4,
                device_name="cpu",
                weak_negative_weight=0.2,
                seed=3,
            )
            summary = result["summary"]
            written_summary = json.loads(summary_path.read_text(encoding="utf-8"))
            model_exists = model_path.exists()
            prediction_exists = prediction_path.exists()
            prediction_rows = read_jsonl(prediction_path)
            take_score = score_row(
                model_path,
                TASK_TAKE_SKIP,
                {"character": "IRONCLAD", "floor": 5, "options": ["Battle Trance", "Pommel Strike"], "option_count": 2},
            )
            purge_score = score_row(
                model_path,
                TASK_PURGE_REMOVE,
                {"character": "IRONCLAD", "floor": 8, "candidate_card": "Strike_R", "purge_index": 1},
            )

        self.assertTrue(model_exists)
        self.assertTrue(prediction_exists)
        self.assertEqual(summary["model_kind"], "decision_multitask_shadow")
        self.assertEqual(summary["backend"], "pytorch_multitask_mlp")
        self.assertEqual(summary["training_source_quality"], "external_prior")
        self.assertEqual(summary["source_validation_grade"], "external_prior")
        self.assertFalse(summary["runtime_authority"])
        self.assertFalse(summary["runtime_default_enabled"])
        self.assertEqual(summary["runtime_authority_level"], "shadow")
        self.assertTrue(summary["does_not_control_live_mcp"])
        self.assertFalse(summary["direct_mcp_control"])
        self.assertTrue(summary["requires_audited_promotion"])
        self.assertIn("runtime_authority", summary["forbidden_uses"])
        self.assertIn(TASK_TAKE_SKIP, summary["heads"])
        self.assertIn(TASK_PURGE_REMOVE, summary["heads"])
        self.assertIn(TASK_DECK_CYCLE, summary["heads"])
        self.assertEqual(summary["row_counts"]["card_reward_decisions"], 4)
        self.assertEqual(summary["row_counts"]["card_purge_priors"], 2)
        self.assertEqual(summary["row_counts"]["deck_cycle_priors"], 2)
        self.assertIn("evaluation", summary)
        self.assertIn(TASK_TAKE_SKIP, summary["evaluation"]["all"])
        self.assertIn("accuracy", summary["evaluation"]["all"][TASK_TAKE_SKIP])
        self.assertIn("precision", summary["evaluation"]["all"][TASK_PURGE_REMOVE])
        self.assertIn("mae", summary["evaluation"]["all"][TASK_DECK_CYCLE])
        self.assertIn("prediction_audit", summary)
        self.assertIn("promotion_readiness", summary)
        self.assertEqual(summary["promotion_readiness"]["status"], "shadow_only")
        self.assertFalse(summary["promotion_readiness"]["can_promote_to_assist"])
        self.assertIn("external_prior_only_requires_live_shadow_validation", summary["promotion_readiness"]["blocking_reasons"])
        self.assertIn(TASK_TAKE_SKIP, summary["promotion_readiness"]["head_readiness"])
        self.assertGreater(summary["task_counts"][TASK_PURGE_REMOVE], 2)
        self.assertEqual(written_summary["path"], str(model_path))
        self.assertEqual(len(prediction_rows), summary["examples"])
        self.assertEqual(prediction_rows[0]["model_authority"]["level"], "shadow")
        self.assertFalse(prediction_rows[0]["model_authority"]["runtime_authority"])
        self.assertFalse(prediction_rows[0]["model_authority"]["direct_mcp_control"])
        self.assertIn("prediction", prediction_rows[0])
        self.assertIn("target", prediction_rows[0])
        self.assertGreaterEqual(take_score, 0.0)
        self.assertLessEqual(take_score, 1.0)
        self.assertGreaterEqual(purge_score, 0.0)
        self.assertLessEqual(purge_score, 1.0)

    def test_manifest_parent_rows_are_loaded_without_runtime_authority(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows_dir = root / "external_rows"
            manifest = rows_dir / "external_manifest.json"
            write_jsonl(
                rows_dir / CARD_REWARD_DECISIONS_FILE,
                [
                    base_row(
                        floor=1,
                        decision="take",
                        picked="Pommel Strike",
                        options=["Pommel Strike", "Flex"],
                        option_count=2,
                        transform_target="take_vs_skip",
                    )
                ],
            )
            write_jsonl(
                rows_dir / CARD_PURGE_PRIORS_FILE,
                [
                    base_row(
                        floor=8,
                        purge_index=1,
                        removed="Strike_R",
                        removed_base="Strike_R",
                        purchased_purge_count=1,
                        likely_shop_purge=True,
                        transform_target="remove_vs_keep",
                    )
                ],
            )
            write_jsonl(
                rows_dir / DECK_CYCLE_PRIORS_FILE,
                [
                    base_row(
                        deck_size=10,
                        unique_card_count=8,
                        starter_count=4,
                        draw_card_count=2,
                        draw_density=0.2,
                        exhaust_card_count=1,
                        zero_cost_count=1,
                        purge_count=1,
                        card_reward_take_count=4,
                        card_reward_skip_count=1,
                        card_reward_skip_rate=0.2,
                        starter_density=0.4,
                        transform_target="draw_cycle_quality",
                    )
                ],
            )
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_type": "external_prior_manifest",
                        "source_id": "manifest_unit",
                        "source_uri": "https://example.test/manifest",
                        "source_weight": 0.15,
                    }
                ),
                encoding="utf-8",
            )

            loaded = load_decision_rows([manifest])

        self.assertEqual(loaded.source.source_id, "manifest_unit")
        self.assertEqual(loaded.source.source_uri, "https://example.test/manifest")
        self.assertEqual(loaded.source.source_weight, 0.15)
        self.assertEqual(len(loaded.reward_rows), 1)
        self.assertEqual(len(loaded.purge_rows), 1)
        self.assertEqual(len(loaded.deck_rows), 1)


if __name__ == "__main__":
    unittest.main()
