import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.model import RouteRiskModel
from slay_ai.train_route_risk_model import load_examples, load_examples_with_stats, train_stats_model


def write_jsonl(path: Path, rows: list[dict]) -> None:
    rows = [{**row, "source_validation_grade": row.get("source_validation_grade", "pristine")} for row in rows]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class RouteRiskModelTrainingTests(unittest.TestCase):
    def test_loads_route_risk_rows_from_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [
                    {"floor": 14, "final_floor": 16, "victory": False, "floor_delta": 2},
                    {"floor": 10, "final_floor": 16, "victory": False, "floor_delta": 6},
                    {"floor": 14, "final_floor": 51, "victory": True, "floor_delta": 37},
                ],
            )

            examples = load_examples([shadow])

        self.assertEqual(len(examples), 3)
        self.assertTrue(examples[0].died_within_3_floors)
        self.assertFalse(examples[1].died_within_3_floors)
        self.assertFalse(examples[2].died_within_3_floors)

    def test_filters_source_quality_by_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            rows = [
                {"floor": 14, "final_floor": 16, "victory": False, "source_validation_grade": "pristine"},
                {
                    "floor": 10,
                    "final_floor": 16,
                    "victory": False,
                    "source_validation_grade": "usable_with_recoveries",
                },
                {"floor": 8, "final_floor": 8, "victory": False, "source_validation_grade": "diagnostic"},
                {"floor": 7, "final_floor": 9, "victory": False, "source_validation_grade": ""},
            ]
            write_jsonl(shadow / "route_risk.jsonl", rows)

            pristine_result = load_examples_with_stats([shadow])
            pristine = pristine_result.examples
            usable = load_examples([shadow], source_quality="usable")
            legacy = load_examples([shadow], source_quality="legacy")
            all_rows = load_examples([shadow], source_quality="all")

        self.assertEqual(len(pristine), 1)
        self.assertEqual(pristine_result.stats["rows"], 4)
        self.assertEqual(pristine_result.stats["accepted"], 1)
        self.assertEqual(pristine_result.stats["skipped"], 3)
        self.assertEqual(pristine_result.stats["skip_reasons"], {"source_quality": 3})
        self.assertEqual(len(usable), 2)
        self.assertEqual(len(legacy), 2)
        self.assertEqual(len(all_rows), 4)

    def test_trains_model_without_outcome_leakage(self):
        rows = [
            {
                "floor": 14,
                "act": 1,
                "hp_ratio": 0.22,
                "route_score": -15,
                "forced_elite_within_3": True,
                "forced_combat_within_2": True,
                "nearest_rest": 3,
                "nearest_shop": 4,
                "readiness_penalty": 35,
                "deck_tag_frontload": 0,
                "potion_count": 0,
                "relic_known_count": 1,
                "boss_max_expected_attack": 38,
                "boss_total_expected_attack": 110,
                "boss_max_mode_shift_threshold": 30,
                "boss_need_frontload": 3,
                "readiness_score_elite": 28,
                "readiness_flags": ["low_hp_before_elite"],
                "selected_symbol": "E",
                "final_floor": 16,
                "victory": False,
                "floor_delta": 2,
            },
            {
                "floor": 14,
                "act": 1,
                "hp_ratio": 0.9,
                "route_score": 92,
                "forced_elite_within_3": False,
                "forced_combat_within_2": False,
                "nearest_rest": 1,
                "nearest_shop": 1,
                "readiness_penalty": 0,
                "deck_tag_frontload": 2,
                "potion_count": 2,
                "relic_known_count": 2,
                "boss_max_expected_attack": 32,
                "boss_total_expected_attack": 84,
                "boss_max_mode_shift_threshold": 0,
                "boss_need_frontload": 1,
                "readiness_score_elite": 82,
                "readiness_flags": [],
                "selected_symbol": "R",
                "final_floor": 22,
                "victory": False,
                "floor_delta": 8,
            },
        ]
        examples = load_examples_from_rows(rows)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "route_risk_model.json"
            model = train_stats_model(examples, path)
            model.save()
            loaded = RouteRiskModel.load(path)

        self.assertEqual(loaded.metadata["examples"], 2)
        self.assertEqual(loaded.metadata["positive_examples"], 1)
        self.assertEqual(loaded.metadata["source_quality"], {"pristine": 2})
        self.assertEqual(
            loaded.metadata["load_quality"],
            {"files": None, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertEqual(loaded.metadata["target"], "died_within_3_floors")
        self.assertIn("hp_ratio", loaded.feature_weights)
        self.assertIn("forced_elite_within_3", loaded.feature_weights)
        self.assertIn("readiness_penalty", loaded.feature_weights)
        self.assertIn("readiness_score_elite", loaded.feature_weights)
        self.assertIn("boss_max_expected_attack", loaded.feature_weights)
        self.assertIn("boss_total_expected_attack", loaded.feature_weights)
        self.assertIn("boss_max_mode_shift_threshold", loaded.feature_weights)
        self.assertNotIn("final_floor", loaded.feature_weights)
        self.assertNotIn("victory", loaded.feature_weights)
        self.assertNotIn("floor_delta", loaded.feature_weights)
        self.assertNotIn("selected_symbol", loaded.feature_weights)
        self.assertNotIn("readiness_flags", loaded.feature_weights)
        self.assertGreater(loaded.score_row(rows[0]), loaded.score_row(rows[1]))


def load_examples_from_rows(rows: list[dict]):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "route_risk.jsonl"
        write_jsonl(path, rows)
        return load_examples([path])


if __name__ == "__main__":
    unittest.main()
