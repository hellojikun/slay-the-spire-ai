import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.model import CombatSearchModel
from slay_ai.train_combat_search_model import load_examples, load_examples_with_stats, train_stats_model


def write_jsonl(path: Path, rows: list[dict]) -> None:
    rows = [{**row, "source_validation_grade": row.get("source_validation_grade", "pristine")} for row in rows]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class CombatSearchModelTrainingTests(unittest.TestCase):
    def test_loads_combat_search_label_rows_from_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "label_first_card_key": "Bash+",
                        "label_sequence_card_keys": ["Bash+", "Strike_R"],
                        "initial_loss": 18,
                        "projected_loss": 6,
                        "attacks_removed": 1,
                        "avoided_lethal": True,
                    },
                    {"label_first_card_key": "", "initial_loss": 10, "projected_loss": 0},
                ],
            )

            examples = load_examples([shadow])

        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0].first_card_key, "bash")
        self.assertGreater(examples[0].label_value, 2.0)

    def test_skips_missed_direct_kill_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_labels.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "label_first_card_key": "Defend_R",
                        "label_sequence_card_keys": ["Defend_R"],
                        "initial_loss": 12,
                        "projected_loss": 7,
                        "direct_kill_available": True,
                        "direct_kill_card_keys": ["Twin Strike"],
                        "label_missed_direct_kill": True,
                    },
                    {
                        "label_first_card_key": "Strike_R",
                        "label_sequence_card_keys": ["Strike_R"],
                        "search_type": "single_card_search_diagnostic",
                        "initial_loss": 12,
                        "projected_loss": 0,
                        "attacks_removed": 12,
                        "avoided_lethal": True,
                        "label_missed_single_card_search": True,
                    },
                    {
                        "label_first_card_key": "Bash",
                        "label_sequence_card_keys": ["Bash"],
                        "initial_loss": 18,
                        "projected_loss": 0,
                        "attacks_removed": 18,
                        "avoided_lethal": True,
                    },
                ],
            )

            load_result = load_examples_with_stats([path])
            examples = load_result.examples
            model_path = Path(tmp) / "model.json"
            model = train_stats_model(examples, model_path, load_quality=load_result.stats)
            model.save()
            loaded = CombatSearchModel.load(model_path)

        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0].first_card_key, "bash")
        self.assertEqual(load_result.stats["rows"], 3)
        self.assertEqual(load_result.stats["accepted"], 1)
        self.assertEqual(load_result.stats["skipped"], 2)
        self.assertEqual(
            load_result.stats["skip_reasons"],
            {"missed_direct_kill": 1, "missed_single_card_search": 1},
        )
        self.assertEqual(
            loaded.metadata["load_quality"]["skip_reasons"],
            {"missed_direct_kill": 1, "missed_single_card_search": 1},
        )

    def test_trains_model_and_ranks_hand_without_outcome_leakage(self):
        rows = [
            {
                "act": 1,
                "turn": 3,
                "hp_ratio": 0.2,
                "current_hp": 12,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 18,
                "enemy_count": 1,
                "enemy_ids": ["JawWorm"],
                "hand_ids": ["Defend_R", "Bash"],
                "label_first_card_key": "Bash",
                "label_sequence_card_keys": ["Bash"],
                "initial_loss": 18,
                "projected_loss": 8,
                "attacks_removed": 1,
                "kills": 0,
                "avoided_lethal": True,
                "final_floor": 7,
                "victory": False,
            },
            {
                "act": 1,
                "turn": 4,
                "hp_ratio": 0.18,
                "current_hp": 10,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 16,
                "enemy_count": 1,
                "enemy_ids": ["JawWorm"],
                "hand_ids": ["Defend_R", "Bash"],
                "label_first_card_key": "Bash",
                "initial_loss": 16,
                "projected_loss": 5,
                "attacks_removed": 1,
                "kills": 0,
                "avoided_lethal": True,
                "final_floor": 7,
                "victory": False,
            },
            {
                "act": 1,
                "turn": 2,
                "hp_ratio": 0.8,
                "current_hp": 64,
                "current_block": 12,
                "current_energy": 1,
                "incoming": 6,
                "enemy_count": 1,
                "enemy_ids": ["Louse"],
                "hand_ids": ["Defend_R", "Strike_R"],
                "label_first_card_key": "Defend_R",
                "initial_loss": 0,
                "projected_loss": 0,
                "attacks_removed": 0,
                "kills": 0,
                "avoided_lethal": False,
                "final_floor": 12,
                "victory": False,
            },
        ]
        examples = load_examples_from_rows(rows)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_model.json"
            model = train_stats_model(examples, path)
            model.save()
            loaded = CombatSearchModel.load(path)

        self.assertEqual(loaded.metadata["examples"], 3)
        self.assertEqual(loaded.metadata["target"], "label_first_card_key")
        self.assertEqual(loaded.metadata["source_quality"], {"pristine": 3})
        self.assertEqual(
            loaded.metadata["load_quality"],
            {"files": None, "rows": 3, "accepted": 3, "skipped": 0, "skip_reasons": {}},
        )
        self.assertIn("bash", loaded.card_priors)
        self.assertIn("defendr", loaded.card_priors)
        self.assertNotIn("final_floor", loaded.context_card_scores)
        self.assertNotIn("victory", loaded.context_card_scores)
        ranked = loaded.rank_hand(
            {
                "act": 1,
                "turn": 3,
                "hp_ratio": 0.2,
                "current_hp": 12,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 18,
                "enemy_count": 1,
                "enemy_ids": ["JawWorm"],
                "hand_ids": ["Defend_R", "Bash+"],
            }
        )
        self.assertEqual(ranked[0]["card_key"], "bash")
        self.assertGreater(loaded.score_card("Bash+", rows[0]), loaded.score_card("Defend_R", rows[0]))

    def test_combat_search_model_uses_boss_search_hint_context(self):
        rows = [
            {
                "act": 1,
                "turn": 2,
                "hp_ratio": 0.75,
                "current_hp": 60,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 20,
                "enemy_count": 1,
                "enemy_boss_count": 1,
                "enemy_ids": ["TheGuardian"],
                "hand_ids": ["Defend_R", "Bash"],
                "boss_search_hint_mode_shift_attack_cancel": 1,
                "label_first_card_key": "Bash",
                "initial_loss": 20,
                "projected_loss": 0,
                "attacks_removed": 20,
                "kills": 0,
                "avoided_lethal": False,
            },
            {
                "act": 1,
                "turn": 2,
                "hp_ratio": 0.75,
                "current_hp": 60,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 20,
                "enemy_count": 1,
                "enemy_boss_count": 1,
                "enemy_ids": ["TheGuardian"],
                "hand_ids": ["Defend_R", "Bash"],
                "label_first_card_key": "Defend_R",
                "initial_loss": 20,
                "projected_loss": 15,
                "attacks_removed": 0,
                "kills": 0,
                "avoided_lethal": False,
            },
        ]
        examples = load_examples_from_rows(rows)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_model.json"
            model = train_stats_model(examples, path)
            model.save()
            loaded = CombatSearchModel.load(path)

        self.assertIn("boss_search_hint:mode_shift_attack_cancel", loaded.context_card_scores)
        self.assertIn("bash", loaded.context_card_scores["boss_search_hint:mode_shift_attack_cancel"])
        self.assertGreater(
            loaded.score_card("Bash", rows[0]),
            loaded.score_card("Defend_R", rows[0]),
        )


def load_examples_from_rows(rows: list[dict]):
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "combat_search_labels.jsonl"
        write_jsonl(path, rows)
        return load_examples([path])


if __name__ == "__main__":
    unittest.main()
