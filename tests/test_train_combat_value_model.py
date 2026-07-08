import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.train_combat_value_model import load_examples, score_row, train_torch_model


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def search_record(step: int = 1) -> dict:
    return {
        "step": step,
        "state": {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "floor": 7,
            "act": 1,
            "class": "IRONCLAD",
            "ascension_level": 0,
            "current_hp": 12,
            "max_hp": 80,
            "combat": {
                "turn": 3,
                "incoming_damage": 18,
                "player": {"current_hp": 12, "max_hp": 80, "block": 0, "current_energy": 2},
                "hand_cards": [
                    {"id": "Defend_R", "name": "Defend", "is_playable": True},
                    {"id": "Bash", "name": "Bash", "is_playable": True},
                ],
                "monsters": [{"id": "JawWorm", "name": "Jaw Worm", "intent": "ATTACK"}],
            },
        },
        "decision": {
            "actions": [{"action": "play_card", "card_index": 2, "target_index": 1}],
            "metadata": {
                "search": {
                    "type": "one_turn_search",
                    "sequence_card_keys": ["Bash"],
                    "first_card_key": "Bash",
                    "initial_loss": 18,
                    "projected_loss": 8,
                    "kills": 0,
                    "attacks_removed": 1,
                    "avoided_lethal": True,
                }
            },
        },
    }


class CombatValueModelTrainingTests(unittest.TestCase):
    def test_direct_run_log_respects_source_quality_gate(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            write_jsonl(
                path,
                [
                    search_record(),
                    {"event": "action_result", "action_status": "preflight_mismatch", "recovered": True, "last_error": "Preflight unavailable action(s): ['choose']"},
                ],
            )

            strict_examples = load_examples([path])
            weighted_examples = load_examples([path], quality_policy="weighted", diagnostic_weight=0.2)

        self.assertEqual(strict_examples, [])
        self.assertEqual(len(weighted_examples), 1)
        self.assertEqual(weighted_examples[0].sample_weight, 0.2)
        self.assertEqual(weighted_examples[0].row["source_validation_grade"], "diagnostic")
        self.assertTrue(weighted_examples[0].row["source_has_recovered_action_race"])
        self.assertEqual(weighted_examples[0].row["source_action_recovery_kinds"], {"unavailable_action": 1})

    def test_direct_run_log_distinguishes_stale_potion_target_recovery(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            write_jsonl(
                path,
                [
                    search_record(),
                    {
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale targeted use_potion: non-numeric potion_slot=1, target_index='bad'.",
                    },
                ],
            )

            weighted_examples = load_examples([path], quality_policy="weighted", diagnostic_weight=0.2)

        self.assertEqual(len(weighted_examples), 1)
        self.assertEqual(
            weighted_examples[0].row["source_action_recovery_kinds"],
            {"stale_potion_target_index": 1},
        )

    def test_trains_pytorch_shadow_model_and_scores_rows(self):
        rows = [
            {
                "source_validation_grade": "pristine",
                "source_category": "clean_trainable",
                "source_reason": "completed_clean",
                "act": 1,
                "floor": 7,
                "turn": 3,
                "hp_ratio": 0.15,
                "current_hp": 12,
                "max_hp": 80,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 18,
                "hand_size": 2,
                "playable_count": 2,
                "enemy_count": 1,
                "enemy_boss_count": 1,
                "enemy_elite_or_boss_count": 1,
                "enemy_total_expected_attack": 36,
                "enemy_average_expected_attack": 36,
                "enemy_status_pressure_count": 1,
                "enemy_scaling_pressure_count": 1,
                "enemy_frontload_check_count": 1,
                "enemy_potion_tempo_check_count": 0,
                "boss_known_count": 1,
                "boss_possible_count": 1,
                "boss_max_expected_attack": 36,
                "boss_total_expected_attack": 36,
                "boss_max_hit_count": 6,
                "boss_max_burn_damage": 2,
                "boss_max_upgraded_burn_damage": 4,
                "enemy_ids": ["JawWorm"],
                "hand_ids": ["Defend_R", "Bash"],
                "label_first_card_key": "Bash",
                "label_sequence_card_keys": ["Bash"],
                "initial_loss": 18,
                "projected_loss": 8,
                "attacks_removed": 1,
                "avoided_lethal": True,
            },
            {
                "source_validation_grade": "pristine",
                "source_category": "clean_trainable",
                "source_reason": "completed_clean",
                "act": 1,
                "floor": 3,
                "turn": 2,
                "hp_ratio": 0.8,
                "current_hp": 64,
                "max_hp": 80,
                "current_block": 12,
                "current_energy": 1,
                "incoming": 6,
                "hand_size": 2,
                "playable_count": 2,
                "enemy_count": 1,
                "enemy_ids": ["Louse"],
                "hand_ids": ["Defend_R", "Strike_R"],
                "label_first_card_key": "Defend_R",
                "label_sequence_card_keys": ["Defend_R"],
                "initial_loss": 0,
                "projected_loss": 0,
                "attacks_removed": 0,
                "avoided_lethal": False,
            },
        ]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(shadow / "combat_search_labels.jsonl", rows)
            examples = load_examples([shadow])
            model_path = root / "combat_value_model.pt"
            result = train_torch_model(examples, model_path=model_path, epochs=3, device_name="cpu", hidden_dim=16)
            bash_score = score_row(model_path, rows[0])
            numeric_features = result["checkpoint"]["feature_spec"]["numeric_features"]
            model_exists = model_path.exists()

        self.assertEqual(result["summary"]["examples"], 2)
        self.assertEqual(result["summary"]["source_quality_counts"], {"pristine": 2})
        self.assertEqual(result["summary"]["backend"], "pytorch_mlp")
        self.assertIn("boss_max_hit_count", numeric_features)
        self.assertIn("boss_max_burn_damage", numeric_features)
        self.assertIn("boss_max_upgraded_burn_damage", numeric_features)
        self.assertIn("enemy_total_expected_attack", numeric_features)
        self.assertIn("enemy_status_pressure_count", numeric_features)
        self.assertIn("enemy_scaling_pressure_count", numeric_features)
        self.assertIn("enemy_frontload_check_count", numeric_features)
        self.assertIn("enemy_potion_tempo_check_count", numeric_features)
        self.assertTrue(model_exists)
        self.assertGreater(bash_score, 0.5)


if __name__ == "__main__":
    unittest.main()
