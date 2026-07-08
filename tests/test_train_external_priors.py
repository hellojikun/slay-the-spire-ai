import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from slay_ai.import_external_runs import CARD_PRIOR_ROWS_FILE
from slay_ai.model import CardValueModel
from slay_ai.shadow_quality import source_quality_allowed
from slay_ai.train_external_priors import (
    external_prior_runtime_output_risk,
    load_examples_with_stats,
    main,
    train_external_card_prior_model,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class TrainExternalPriorsTests(unittest.TestCase):
    def test_external_prior_rows_are_excluded_from_default_pristine_shadow_gate(self):
        row = {"source_validation_grade": "external_prior"}
        self.assertFalse(source_quality_allowed(row, "pristine"))
        self.assertFalse(source_quality_allowed(row, "usable"))
        self.assertTrue(source_quality_allowed(row, "external"))

    def test_trains_external_card_prior_model_from_manifest_artifact(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows_path = root / "external" / CARD_PRIOR_ROWS_FILE
            write_jsonl(
                rows_path,
                [
                    {
                        "character": "IRONCLAD",
                        "ascension": 0,
                        "floor": 1,
                        "picked": "Shrug It Off",
                        "options": ["Shrug It Off", "Flex"],
                        "has_reward_options": True,
                        "victory": True,
                        "final_floor": 50,
                        "source_validation_grade": "external_prior",
                        "source_dataset": "unit_source",
                        "source_weight": 1.0,
                    },
                    {
                        "character": "IRONCLAD",
                        "ascension": 0,
                        "floor": 2,
                        "picked": "Shrug It Off",
                        "options": ["Shrug It Off", "Anger"],
                        "has_reward_options": True,
                        "victory": False,
                        "final_floor": 42,
                        "source_validation_grade": "external_prior",
                        "source_dataset": "unit_source",
                        "source_weight": 1.0,
                    },
                    {
                        "character": "IRONCLAD",
                        "ascension": 0,
                        "floor": 3,
                        "picked": "Clash",
                        "options": ["Clash"],
                        "has_reward_options": False,
                        "victory": False,
                        "final_floor": 8,
                        "source_validation_grade": "external_prior",
                        "source_dataset": "unit_source",
                        "source_weight": 1.0,
                    },
                    {
                        "character": "IRONCLAD",
                        "picked": "Bad Source",
                        "victory": True,
                        "final_floor": 50,
                        "source_validation_grade": "pristine",
                    },
                ],
            )
            manifest_path = root / "external" / "external_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "manifest_type": "external_prior_manifest",
                        "source_id": "unit_source",
                        "artifacts": {"card_reward_priors": str(rows_path)},
                    }
                ),
                encoding="utf-8",
            )

            load_result = load_examples_with_stats([manifest_path])
            model_path = root / "models" / "card_prior_model.json"
            model = train_external_card_prior_model(
                load_result.examples,
                model_path,
                min_count=1,
                max_delta=4,
                prior_scale=1.0,
                load_quality=load_result.stats,
            )
            model.metadata["training_source"] = load_result.training_source
            model.metadata["training_source_quality"] = "external_prior"
            model.save()
            loaded = CardValueModel.load(model_path)

        self.assertEqual(load_result.stats["rows"], 4)
        self.assertEqual(load_result.stats["accepted"], 3)
        self.assertEqual(load_result.stats["skipped"], 1)
        self.assertEqual(load_result.stats["skip_reasons"], {"source_quality": 1})
        self.assertEqual(load_result.stats["has_reward_options_count"], 2)
        self.assertEqual(load_result.stats["picked_only_count"], 1)
        self.assertGreater(loaded.score_delta("Shrug It Off", "IRONCLAD"), 0)
        self.assertLessEqual(loaded.score_delta("Flex", "IRONCLAD"), 0)
        self.assertLess(loaded.score_delta("Clash", "IRONCLAD"), 0)
        self.assertEqual(loaded.metadata["model_kind"], "external_card_prior")
        self.assertEqual(loaded.metadata["training_source_quality"], "external_prior")
        self.assertFalse(loaded.metadata["runtime_authority"])
        self.assertFalse(loaded.metadata["runtime_default_enabled"])
        self.assertTrue(loaded.metadata["does_not_control_live_mcp"])
        self.assertIn("runtime_authority", loaded.metadata["forbidden_uses"])
        self.assertEqual(loaded.metadata["training_source"]["source_id"], "unit_source")

    def test_cli_defaults_to_external_models_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows_path = root / "external" / CARD_PRIOR_ROWS_FILE
            write_jsonl(
                rows_path,
                [
                    {
                        "character": "IRONCLAD",
                        "picked": "Inflame",
                        "options": ["Inflame"],
                        "victory": True,
                        "final_floor": 50,
                        "source_validation_grade": "external_prior",
                        "source_dataset": "cli_source",
                    }
                ],
            )
            manifest_path = root / "external" / "external_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "manifest_type": "external_prior_manifest",
                        "source_id": "cli_source",
                        "artifacts": {"card_reward_priors": str(rows_path)},
                    }
                ),
                encoding="utf-8",
            )
            model_dir = root / "external_models"

            with patch(
                "slay_ai.train_external_priors.hardware_metadata",
                return_value={"requested_backend": "stats", "torch_available": False, "cuda_available": False},
            ):
                self.assertEqual(
                    main(
                        [
                            str(manifest_path),
                            "--model-dir",
                            str(model_dir),
                            "--min-count",
                            "1",
                            "--backend",
                            "stats",
                        ]
                    ),
                    0,
                )

            model = CardValueModel.load(model_dir / "card_prior_model.json")
            summary = json.loads((model_dir / "external_prior_training_summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["training_source"]["source_id"], "cli_source")
        self.assertFalse(summary["runtime_authority"])
        self.assertFalse(summary["runtime_default_enabled"])
        self.assertTrue(summary["does_not_control_live_mcp"])
        self.assertIn("runtime_authority", summary["forbidden_uses"])
        self.assertEqual(model.metadata["training_source_quality"], "external_prior")
        self.assertIn("IRONCLAD::Inflame", model.card_deltas)

    def test_external_prior_cli_refuses_runtime_card_model_output_by_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows_path = root / "external" / CARD_PRIOR_ROWS_FILE
            write_jsonl(
                rows_path,
                [
                    {
                        "character": "IRONCLAD",
                        "picked": "Inflame",
                        "victory": True,
                        "final_floor": 50,
                        "source_validation_grade": "external_prior",
                    }
                ],
            )
            blocked_path = root / "models" / "card_value_model.json"

            with self.assertRaises(SystemExit):
                main([str(rows_path), "--model-path", str(blocked_path), "--min-count", "1"])

            self.assertFalse(blocked_path.exists())
            self.assertEqual(external_prior_runtime_output_risk(blocked_path), "card_value_model_under_models_dir")

    def test_external_prior_cli_allows_runtime_output_only_when_explicit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows_path = root / "external" / CARD_PRIOR_ROWS_FILE
            write_jsonl(
                rows_path,
                [
                    {
                        "character": "IRONCLAD",
                        "picked": "Inflame",
                        "victory": True,
                        "final_floor": 50,
                        "source_validation_grade": "external_prior",
                    }
                ],
            )
            model_path = root / "models" / "card_value_model.json"

            with patch(
                "slay_ai.train_external_priors.hardware_metadata",
                return_value={"requested_backend": "stats", "torch_available": False, "cuda_available": False},
            ):
                self.assertEqual(
                    main(
                        [
                            str(rows_path),
                            "--model-path",
                            str(model_path),
                            "--allow-runtime-output",
                            "--min-count",
                            "1",
                            "--backend",
                            "stats",
                        ]
                    ),
                    0,
                )

            model = CardValueModel.load(model_path)
            self.assertEqual(model.metadata["training_source_quality"], "external_prior")
            self.assertFalse(model.metadata["runtime_authority"])


if __name__ == "__main__":
    unittest.main()
