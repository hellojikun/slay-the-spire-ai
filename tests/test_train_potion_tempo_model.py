import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.model import PotionTempoModel
from slay_ai.train_potion_tempo_model import load_examples, load_examples_with_stats, train_stats_model


def write_jsonl(path: Path, rows: list[dict]) -> None:
    rows = [{**row, "source_validation_grade": row.get("source_validation_grade", "pristine")} for row in rows]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class PotionTempoModelTrainingTests(unittest.TestCase):
    def test_loads_potion_tempo_rows_from_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "potion_tempo.jsonl",
                [
                    {"incoming": 28, "hp_ratio": 0.2, "used_potion": True},
                    {"incoming": 6, "hp_ratio": 0.9, "used_potion": False},
                ],
            )

            examples = load_examples([shadow])

        self.assertEqual(len(examples), 2)
        self.assertTrue(examples[0].used_potion)
        self.assertFalse(examples[1].used_potion)

    def test_load_stats_count_source_quality_skips(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "potion_tempo.jsonl",
                [
                    {"incoming": 28, "hp_ratio": 0.2, "used_potion": True, "source_validation_grade": "pristine"},
                    {
                        "incoming": 6,
                        "hp_ratio": 0.9,
                        "used_potion": False,
                        "source_validation_grade": "usable_with_recoveries",
                    },
                ],
            )

            result = load_examples_with_stats([shadow])

        self.assertEqual(len(result.examples), 1)
        self.assertEqual(result.stats["rows"], 2)
        self.assertEqual(result.stats["accepted"], 1)
        self.assertEqual(result.stats["skipped"], 1)
        self.assertEqual(result.stats["skip_reasons"], {"source_quality": 1})

    def test_trains_model_without_outcome_or_label_leakage(self):
        rows = [
            {
                "floor": 16,
                "act": 1,
                "turn": 2,
                "hp_ratio": 0.18,
                "incoming": 36,
                "potion_known_count": 2,
                "potion_role_emergency": 1,
                "enemy_boss_count": 1,
                "boss_max_expected_attack": 38,
                "boss_total_expected_attack": 110,
                "boss_max_sharp_hide_damage": 3,
                "boss_mechanic_mode_shift": 1,
                "used_potion": True,
                "used_potion_slot": 1,
                "died_same_floor": False,
                "final_floor": 20,
                "victory": False,
            },
            {
                "floor": 3,
                "act": 1,
                "turn": 1,
                "hp_ratio": 0.92,
                "incoming": 6,
                "potion_known_count": 1,
                "potion_role_emergency": 0,
                "enemy_boss_count": 0,
                "boss_max_expected_attack": 8,
                "boss_total_expected_attack": 12,
                "boss_max_sharp_hide_damage": 0,
                "boss_mechanic_mode_shift": 0,
                "used_potion": False,
                "used_potion_slot": None,
                "died_same_floor": True,
                "final_floor": 3,
                "victory": False,
            },
        ]
        examples = load_examples_from_rows(rows)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "potion_tempo_model.json"
            model = train_stats_model(examples, path)
            model.save()
            loaded = PotionTempoModel.load(path)

        self.assertEqual(loaded.metadata["examples"], 2)
        self.assertEqual(loaded.metadata["positive_examples"], 1)
        self.assertEqual(loaded.metadata["source_quality"], {"pristine": 2})
        self.assertEqual(
            loaded.metadata["load_quality"],
            {"files": None, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertIn("incoming", loaded.feature_weights)
        self.assertIn("hp_ratio", loaded.feature_weights)
        self.assertIn("potion_role_emergency", loaded.feature_weights)
        self.assertIn("boss_max_expected_attack", loaded.feature_weights)
        self.assertIn("boss_total_expected_attack", loaded.feature_weights)
        self.assertIn("boss_max_sharp_hide_damage", loaded.feature_weights)
        self.assertNotIn("used_potion", loaded.feature_weights)
        self.assertNotIn("used_potion_slot", loaded.feature_weights)
        self.assertNotIn("died_same_floor", loaded.feature_weights)
        self.assertNotIn("final_floor", loaded.feature_weights)
        self.assertNotIn("victory", loaded.feature_weights)
        self.assertGreater(loaded.score_row(rows[0]), loaded.score_row(rows[1]))


def load_examples_from_rows(rows: list[dict]):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "potion_tempo.jsonl"
        write_jsonl(path, rows)
        return load_examples([path])


if __name__ == "__main__":
    unittest.main()
