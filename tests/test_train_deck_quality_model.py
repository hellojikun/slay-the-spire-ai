import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.model import DeckQualityModel
from slay_ai.train_deck_quality_model import load_examples, load_examples_with_stats, train_stats_model


def write_jsonl(path: Path, rows: list[dict]) -> None:
    rows = [{**row, "source_validation_grade": row.get("source_validation_grade", "pristine")} for row in rows]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class DeckQualityModelTrainingTests(unittest.TestCase):
    def test_loads_pre_boss_shadow_rows_from_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "pre_boss_deck_quality.jsonl",
                [
                    {"floor": 15, "final_floor": 18, "victory": False, "deck_tag_weak": 1},
                    {"floor": 15, "final_floor": 16, "victory": False, "deck_tag_weak": 0},
                ],
            )

            examples = load_examples([shadow])

        self.assertEqual(len(examples), 2)
        self.assertTrue(examples[0].cleared_act1_boss)
        self.assertFalse(examples[1].cleared_act1_boss)

    def test_load_stats_count_source_quality_skips(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "pre_boss_deck_quality.jsonl",
                [
                    {
                        "floor": 15,
                        "final_floor": 18,
                        "victory": False,
                        "source_validation_grade": "pristine",
                    },
                    {
                        "floor": 15,
                        "final_floor": 16,
                        "victory": False,
                        "source_validation_grade": "diagnostic",
                    },
                ],
            )

            result = load_examples_with_stats([shadow])

        self.assertEqual(len(result.examples), 1)
        self.assertEqual(result.stats["rows"], 2)
        self.assertEqual(result.stats["accepted"], 1)
        self.assertEqual(result.stats["skipped"], 1)
        self.assertEqual(result.stats["skip_reasons"], {"source_quality": 1})

    def test_trains_model_without_leaky_outcome_features(self):
        rows = [
            {
                "floor": 15,
                "act": 1,
                "final_floor": 18,
                "victory": False,
                "deck_tag_weak": 1,
                "deck_tag_premium_defense": 1,
                "potion_count": 2,
                "boss_potion_gap": False,
                "boss_max_expected_attack": 32,
                "boss_total_expected_attack": 84,
                "boss_max_post_split_enemy_count": 0,
                "readiness_score_boss": 75,
            },
            {
                "floor": 15,
                "act": 1,
                "final_floor": 16,
                "victory": False,
                "deck_tag_weak": 0,
                "deck_tag_premium_defense": 0,
                "potion_count": 0,
                "boss_potion_gap": True,
                "boss_max_expected_attack": 38,
                "boss_total_expected_attack": 110,
                "boss_max_post_split_enemy_count": 2,
                "readiness_score_boss": 42,
            },
        ]
        examples = load_examples_from_rows(rows)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "deck_quality_model.json"
            model = train_stats_model(examples, path)
            model.save()
            loaded = DeckQualityModel.load(path)

        self.assertEqual(loaded.metadata["examples"], 2)
        self.assertEqual(loaded.metadata["positive_examples"], 1)
        self.assertEqual(loaded.metadata["source_quality"], {"pristine": 2})
        self.assertEqual(
            loaded.metadata["load_quality"],
            {"files": None, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertIn("deck_tag_weak", loaded.feature_weights)
        self.assertIn("readiness_score_boss", loaded.feature_weights)
        self.assertIn("boss_potion_gap", loaded.feature_weights)
        self.assertIn("boss_max_expected_attack", loaded.feature_weights)
        self.assertIn("boss_total_expected_attack", loaded.feature_weights)
        self.assertIn("boss_max_post_split_enemy_count", loaded.feature_weights)
        self.assertNotIn("final_floor", loaded.feature_weights)
        self.assertNotIn("victory", loaded.feature_weights)
        good_score = loaded.score_row(rows[0])
        bad_score = loaded.score_row(rows[1])
        self.assertGreater(good_score, bad_score)


def load_examples_from_rows(rows: list[dict]):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "pre_boss_deck_quality.jsonl"
        write_jsonl(path, rows)
        return load_examples([path])


if __name__ == "__main__":
    unittest.main()
