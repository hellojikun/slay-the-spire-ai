import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from slay_ai.learn import main as learn_main
from slay_ai.run_status import main as run_status_main
from slay_ai.train_card_model import load_examples, main as train_card_model_main
from slay_ai.training_manifest import CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED


def write_log(path: Path, *, pick: str, victory: bool, floor: int) -> None:
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "step": 1,
                    "state": {
                        "screen_type": "CARD_REWARD",
                        "floor": 3,
                        "class": "IRONCLAD",
                        "ascension_level": 0,
                        "card_reward_options": [
                            {"id": pick},
                            {"id": "Skip"},
                        ],
                    },
                    "decision": {"learn_card_pick": pick},
                },
                {
                    "step": 2,
                    "state": {
                        "screen_type": "GAME_OVER",
                        "floor": floor,
                        "class": "IRONCLAD",
                        "ascension_level": 0,
                        "outcome": {"victory": victory, "score": 100 + floor},
                    },
                    "decision": {},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )


class LogIterConsumerTests(unittest.TestCase):
    def test_learning_consumers_expand_directory_inputs_in_stable_order(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            first = logs / "a.jsonl"
            second = logs / "b.jsonl"
            write_log(second, pick="Anger", victory=False, floor=16)
            write_log(first, pick="Shrug It Off", victory=True, floor=50)
            (logs / "notes.txt").write_text("ignored", encoding="utf-8")

            examples = load_examples([logs])
            self.assertEqual([example.picked for example in examples], ["Shrug It Off", "Anger"])

            learned_path = root / "learned.json"
            self.assertEqual(learn_main([str(logs), "--learned-path", str(learned_path), "--reset"]), 0)
            learned = json.loads(learned_path.read_text(encoding="utf-8"))
            self.assertEqual(learned["runs"]["total"], 2)
            self.assertEqual(learned["runs"]["victories"], 1)
            replay = learned["last_learning_replay"]
            self.assertEqual(replay["source"]["mode"], "raw_logs")
            self.assertEqual(replay["source"]["quality_policy"], "ungated_logs")
            self.assertEqual(replay["source"]["inputs"], [str(logs)])
            self.assertEqual(replay["source"]["resolved_logs"], [str(first), str(second)])
            self.assertEqual(replay["source"]["resolved_log_count"], 2)
            self.assertEqual(replay["source"]["warnings"], [])
            self.assertEqual(replay["read_logs"], 2)
            self.assertEqual(replay["applied_completed_runs"], 2)
            self.assertEqual(replay["skipped_incomplete_runs"], 0)
            self.assertEqual(learned["learning_replays"], [replay])

    def test_run_status_expands_directory_inputs_in_stable_order(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            first = logs / "a.jsonl"
            second = logs / "b.jsonl"
            write_log(second, pick="Anger", victory=False, floor=16)
            write_log(first, pick="Shrug It Off", victory=True, floor=50)

            output = root / "status.json"
            self.assertEqual(
                run_status_main(
                    [
                        str(logs),
                        "--knowledge-dir",
                        str(root / "missing_static_knowledge"),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["inputs"], [str(logs)])
            self.assertEqual(payload["resolved_logs"], [str(first), str(second)])
            self.assertEqual(payload["warnings"], [])
            self.assertEqual(payload["summary"]["input_count"], 1)
            self.assertEqual(payload["summary"]["resolved_log_count"], 2)
            self.assertEqual(payload["summary"]["run_count"], 2)
            self.assertIn("run_status_summary:", payload["summary"]["status_line"])
            self.assertIn("runs=2", payload["summary"]["status_line"])
            self.assertEqual([run["path"] for run in payload["runs"]], [str(first), str(second)])

    def test_run_status_warns_when_directory_input_resolves_no_logs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            output = root / "status.json"

            self.assertEqual(
                run_status_main(
                    [
                        str(logs),
                        "--knowledge-dir",
                        str(root / "missing_static_knowledge"),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["inputs"], [str(logs)])
            self.assertEqual(payload["resolved_logs"], [])
            self.assertEqual(payload["warnings"], ["no_resolved_logs"])
            self.assertEqual(payload["summary"]["warnings"], ["no_resolved_logs"])
            self.assertEqual(payload["summary"]["resolved_log_count"], 0)
            self.assertIn("warnings=no_resolved_logs", payload["summary"]["status_line"])
            self.assertEqual(payload["summary"]["run_count"], 0)
            self.assertEqual(payload["runs"], [])

    def test_train_card_model_records_raw_log_training_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            first = logs / "a.jsonl"
            second = logs / "b.jsonl"
            write_log(second, pick="Anger", victory=False, floor=16)
            write_log(first, pick="Shrug It Off", victory=True, floor=50)
            model_path = root / "card_model.json"

            with patch(
                "slay_ai.train_card_model.hardware_metadata",
                return_value={"requested_backend": "stats", "torch_available": False, "cuda_available": False},
            ):
                self.assertEqual(
                    train_card_model_main([str(logs), "--model-path", str(model_path), "--backend", "stats"]),
                    0,
                )

            metadata = json.loads(model_path.read_text(encoding="utf-8"))["metadata"]
            source = metadata["training_source"]
            self.assertEqual(metadata["training_source_quality"], "ungated_logs")
            self.assertEqual(source["mode"], "raw_logs")
            self.assertEqual(source["quality_policy"], "ungated_logs")
            self.assertEqual(source["inputs"], [str(logs)])
            self.assertIsNone(source["manifest_path"])
            self.assertEqual(source["resolved_logs"], [str(first), str(second)])
            self.assertEqual(source["resolved_log_count"], 2)
            self.assertEqual(source["warnings"], [])

    def test_learn_records_manifest_clean_training_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            clean = logs / "clean.jsonl"
            diagnostic = logs / "diagnostic.jsonl"
            write_log(clean, pick="Shrug It Off", victory=True, floor=50)
            write_log(diagnostic, pick="Anger", victory=False, floor=16)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "categories": {
                            CLEAN_TRAINABLE: [{"path": str(clean)}],
                            DIAGNOSTIC_EXCLUDED: [{"path": str(diagnostic)}],
                            INFRA_BLOCKED: [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            learned_path = root / "learned.json"

            self.assertEqual(
                learn_main(["--manifest", str(manifest_path), "--learned-path", str(learned_path), "--reset"]),
                0,
            )

            learned = json.loads(learned_path.read_text(encoding="utf-8"))
            self.assertEqual(learned["runs"]["total"], 1)
            self.assertEqual(learned["runs"]["victories"], 1)
            replay = learned["last_learning_replay"]
            source = replay["source"]
            self.assertEqual(source["mode"], "manifest_clean_trainable")
            self.assertEqual(source["quality_policy"], CLEAN_TRAINABLE)
            self.assertEqual(source["inputs"], [str(manifest_path)])
            self.assertEqual(source["manifest_path"], str(manifest_path))
            self.assertEqual(source["manifest_clean_paths"], [str(clean)])
            self.assertEqual(source["manifest_clean_count"], 1)
            self.assertEqual(source["missing_clean_paths"], [])
            self.assertEqual(source["resolved_logs"], [str(clean)])
            self.assertEqual(source["resolved_log_count"], 1)
            self.assertEqual(source["warnings"], [])
            self.assertEqual(replay["read_logs"], 1)
            self.assertEqual(replay["applied_completed_runs"], 1)
            self.assertEqual(replay["skipped_incomplete_runs"], 0)
            self.assertEqual(learned["learning_replays"], [replay])

    def test_train_card_model_records_manifest_clean_training_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            clean = logs / "clean.jsonl"
            diagnostic = logs / "diagnostic.jsonl"
            write_log(clean, pick="Shrug It Off", victory=True, floor=50)
            write_log(diagnostic, pick="Anger", victory=False, floor=16)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "categories": {
                            CLEAN_TRAINABLE: [{"path": str(clean)}],
                            DIAGNOSTIC_EXCLUDED: [{"path": str(diagnostic)}],
                            INFRA_BLOCKED: [],
                        }
                    }
                ),
                encoding="utf-8",
            )
            model_path = root / "card_model.json"

            with patch(
                "slay_ai.train_card_model.hardware_metadata",
                return_value={"requested_backend": "stats", "torch_available": False, "cuda_available": False},
            ):
                self.assertEqual(
                    train_card_model_main(
                        ["--manifest", str(manifest_path), "--model-path", str(model_path), "--backend", "stats"]
                    ),
                    0,
                )

            metadata = json.loads(model_path.read_text(encoding="utf-8"))["metadata"]
            source = metadata["training_source"]
            self.assertEqual(metadata["training_source_quality"], "clean_trainable")
            self.assertEqual(source["mode"], "manifest_clean_trainable")
            self.assertEqual(source["quality_policy"], "clean_trainable")
            self.assertEqual(source["inputs"], [str(manifest_path)])
            self.assertEqual(source["manifest_path"], str(manifest_path))
            self.assertEqual(source["manifest_clean_paths"], [str(clean)])
            self.assertEqual(source["manifest_clean_count"], 1)
            self.assertEqual(source["missing_clean_paths"], [])
            self.assertEqual(source["resolved_logs"], [str(clean)])
            self.assertEqual(source["resolved_log_count"], 1)
            self.assertEqual(source["warnings"], [])


if __name__ == "__main__":
    unittest.main()
