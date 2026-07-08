import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.model import CombatSearchModel, DeckQualityModel, PotionTempoModel, RouteRiskModel
from slay_ai.shadow_advice import ShadowModels, score_shadow_examples, score_shadow_inputs, write_advice
from slay_ai.shadow_inputs import resolve_shadow_training_source


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class ShadowAdviceTests(unittest.TestCase):
    def test_scores_all_shadow_row_types_without_policy_control(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [
                    {
                        "source_log": "run.jsonl",
                        "step": 3,
                        "floor": 8,
                        "act": 1,
                        "character": "IRONCLAD",
                        "hp_ratio": 0.2,
                        "forced_elite_within_3": True,
                        "selected_choice": 1,
                        "selected_symbol": "E",
                        "readiness_penalty": 35,
                        "readiness_flags": ["low_hp_before_elite"],
                    }
                ],
            )
            write_jsonl(
                shadow / "potion_tempo.jsonl",
                [
                    {
                        "source_log": "run.jsonl",
                        "step": 9,
                        "floor": 16,
                        "act": 1,
                        "turn": 2,
                        "incoming": 30,
                        "potion_ids": ["BlockPotion"],
                        "enemy_ids": ["Hexaghost"],
                        "used_potion": False,
                        "used_potion_slot": None,
                        "used_potion_targeted": False,
                        "potion_block_value": 12,
                        "potion_damage_value": 0,
                        "potion_draw_value": 0,
                        "enemy_total_expected_attack": 36,
                        "enemy_boss_count": 1,
                        "boss_identity_known": True,
                        "boss_known_count": 1,
                    }
                ],
            )
            write_jsonl(
                shadow / "pre_boss_deck_quality.jsonl",
                [
                    {
                        "source_log": "run.jsonl",
                        "step": 8,
                        "floor": 15,
                        "act": 1,
                        "deck_size": 17,
                        "hp_ratio": 0.9,
                        "readiness_score_boss": 80,
                        "boss_potion_gap": False,
                    }
                ],
            )
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_log": "run.jsonl",
                        "step": 11,
                        "floor": 7,
                        "act": 1,
                        "turn": 3,
                        "current_hp": 12,
                        "current_energy": 2,
                        "incoming": 18,
                        "enemy_count": 1,
                        "enemy_ids": ["JawWorm"],
                        "hand_ids": ["Defend_R", "Bash"],
                        "label_first_card_key": "Bash",
                        "label_sequence_card_keys": ["Bash"],
                        "initial_loss": 18,
                        "projected_loss": 8,
                        "loss_delta": 10,
                        "attacks_removed": 1,
                        "retaliation_damage": 0,
                        "avoided_lethal": True,
                    }
                ],
            )
            route_path = root / "route.json"
            potion_path = root / "potion.json"
            deck_path = root / "deck.json"
            combat_path = root / "combat.json"
            RouteRiskModel(
                path=route_path,
                intercept=0.0,
                feature_weights={"hp_ratio": -2.0, "forced_elite_within_3": 2.0},
                feature_means={"hp_ratio": 0.5, "forced_elite_within_3": 0.0},
                feature_scales={"hp_ratio": 0.5, "forced_elite_within_3": 1.0},
                metadata={
                    "examples": 4,
                    "training_source_quality": "pristine",
                    "training_source": {
                        "category": "route_risk",
                        "resolved_files": [str(shadow / "route_risk.jsonl")],
                    },
                },
            ).save()
            PotionTempoModel(
                path=potion_path,
                intercept=0.0,
                feature_weights={"incoming": 2.0},
                feature_means={"incoming": 10.0},
                feature_scales={"incoming": 20.0},
                metadata={"examples": 4},
            ).save()
            DeckQualityModel(
                path=deck_path,
                intercept=0.0,
                feature_weights={"readiness_score_boss": 2.0},
                feature_means={"readiness_score_boss": 50.0},
                feature_scales={"readiness_score_boss": 50.0},
                metadata={"examples": 4},
            ).save()
            CombatSearchModel(
                path=combat_path,
                card_priors={"bash": 2.0, "defendr": 0.1},
                context_card_scores={"incoming:lethal": {"bash": 0.5}},
                metadata={
                    "examples": 4,
                    "training_source_quality": "usable",
                    "training_source": {
                        "category": "combat_search",
                        "resolved_files": [str(shadow / "combat_search_labels.jsonl")],
                    },
                    "load_quality": {
                        "files": 1,
                        "rows": 5,
                        "accepted": 4,
                        "skipped": 1,
                        "skip_reasons": {"missed_direct_kill": 1},
                    },
                },
            ).save()

            models = ShadowModels.load(
                route_model_path=route_path,
                potion_model_path=potion_path,
                deck_model_path=deck_path,
                combat_model_path=combat_path,
            )
            advice = score_shadow_inputs([shadow], models=models)
            summary = write_advice(root / "advice", advice, shadow_inputs=resolve_shadow_training_source([shadow]))

            route = advice["route_risk"][0]
            potion = advice["potion_tempo"][0]
            deck = advice["pre_boss_deck_quality"][0]
            combat = advice["combat_search"][0]

        self.assertEqual(route["advice"], "avoid_or_require_recovery")
        self.assertGreater(route["route_risk_score"], 0.65)
        self.assertEqual(route["model_examples"], 4)
        self.assertEqual(route["readiness_flags"], ["low_hp_before_elite"])
        self.assertEqual(route["shadow_source_mode"], "shadow_rows")
        self.assertEqual(route["shadow_source_category"], "route_risk")
        self.assertEqual(route["shadow_source_file"], str(shadow / "route_risk.jsonl"))
        self.assertEqual(route["model_training_source_quality"], "pristine")
        self.assertEqual(route["model_training_source"]["category"], "route_risk")
        self.assertEqual(potion["advice"], "consider_potion")
        self.assertGreater(potion["potion_tempo_score"], 0.65)
        self.assertEqual(potion["potion_ids"], ["BlockPotion"])
        self.assertEqual(potion["enemy_ids"], ["Hexaghost"])
        self.assertEqual(potion["potion_block_value"], 12)
        self.assertEqual(potion["enemy_total_expected_attack"], 36)
        self.assertTrue(potion["boss_identity_known"])
        self.assertEqual(deck["advice"], "boss_ready")
        self.assertGreater(deck["deck_quality_score"], 0.65)
        self.assertEqual(combat["advice"], "model_matches_search")
        self.assertEqual(combat["label_first_card_key"], "bash")
        self.assertEqual(combat["model_top_card_key"], "bash")
        self.assertTrue(combat["model_agrees_with_label"])
        self.assertEqual(combat["retaliation_damage"], 0)
        self.assertEqual(combat["model_load_quality"]["skipped"], 1)
        self.assertEqual(combat["model_load_quality"]["skip_reasons"], {"missed_direct_kill": 1})
        self.assertEqual(combat["shadow_source_file"], str(shadow / "combat_search_labels.jsonl"))
        self.assertEqual(combat["model_training_source_quality"], "usable")
        self.assertEqual(summary["advice_rows"]["route_risk"], 1)
        self.assertEqual(summary["advice_rows"]["combat_search"], 1)
        self.assertEqual(summary["shadow_inputs"]["mode"], "shadow_rows")
        self.assertEqual(summary["shadow_inputs"]["resolved_file_count"], 4)
        self.assertEqual(
            summary["shadow_inputs"]["categories"]["combat_search"]["resolved_files"],
            [str(shadow / "combat_search_labels.jsonl")],
        )
        self.assertEqual(summary["shadow_inputs"]["warnings"], [])
        self.assertEqual(summary["model_training_source_quality"]["route_risk"], "pristine")
        self.assertEqual(summary["model_training_source"]["combat_search"]["category"], "combat_search")
        self.assertEqual(summary["model_load_quality"]["combat_search"]["skipped"], 1)
        self.assertTrue(summary["models_available"]["route_risk"])
        self.assertTrue(summary["models_available"]["potion_tempo"])
        self.assertTrue(summary["models_available"]["pre_boss_deck_quality"])
        self.assertTrue(summary["models_available"]["combat_search"])

    def test_combat_search_advice_prioritizes_missed_direct_kill(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_log": "run.jsonl",
                        "step": 269,
                        "floor": 16,
                        "act": 1,
                        "turn": 15,
                        "current_hp": 7,
                        "current_energy": 1,
                        "incoming": 12,
                        "enemy_count": 1,
                        "enemy_ids": ["Hexaghost"],
                        "hand_ids": ["Twin Strike", "Defend_R"],
                        "label_first_card_key": "Defend_R",
                        "label_sequence_card_keys": ["Defend_R"],
                        "initial_loss": 12,
                        "projected_loss": 7,
                        "direct_kill_available": True,
                        "direct_kill_card_indices": [1],
                        "direct_kill_card_keys": ["Twin Strike"],
                        "direct_kill_enemy_id": "Hexaghost",
                        "direct_kill_enemy_hp": 4,
                        "label_missed_direct_kill": True,
                    }
                ],
            )
            combat_path = root / "combat.json"
            CombatSearchModel(
                path=combat_path,
                card_priors={"defendr": 2.0, "twinstrike": 0.1},
                context_card_scores={},
                metadata={"examples": 4},
            ).save()

            models = ShadowModels.load(
                route_model_path=root / "missing_route.json",
                potion_model_path=root / "missing_potion.json",
                deck_model_path=root / "missing_deck.json",
                combat_model_path=combat_path,
            )
            advice = score_shadow_inputs([shadow], models=models)

        combat = advice["combat_search"][0]
        self.assertEqual(combat["advice"], "review_missed_lethal")
        self.assertTrue(combat["label_missed_direct_kill"])
        self.assertEqual(combat["direct_kill_card_keys"], ["Twin Strike"])
        self.assertEqual(combat["direct_kill_enemy_id"], "Hexaghost")
        self.assertEqual(combat["direct_kill_enemy_hp"], 4)

    def test_combat_search_advice_prioritizes_missed_single_card_search(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_log": "run.jsonl",
                        "step": 201,
                        "floor": 16,
                        "act": 1,
                        "turn": 9,
                        "current_hp": 12,
                        "current_energy": 1,
                        "incoming": 12,
                        "enemy_count": 1,
                        "enemy_ids": ["TheGuardian"],
                        "hand_ids": ["Flame Barrier", "Defend_R"],
                        "actual_action": "play_card",
                        "actual_card_index": 2,
                        "label_card_index": 1,
                        "label_target_index": 1,
                        "label_first_card_key": "Flame Barrier",
                        "label_sequence_card_keys": ["Flame Barrier"],
                        "search_type": "single_card_search_diagnostic",
                        "initial_loss": 12,
                        "projected_loss": 0,
                        "attacks_removed": 0,
                        "retaliation_damage": 24,
                        "avoided_lethal": True,
                        "label_missed_single_card_search": True,
                    }
                ],
            )
            combat_path = root / "combat.json"
            CombatSearchModel(
                path=combat_path,
                card_priors={"flamebarrier": 2.0, "defendr": 0.5},
                context_card_scores={},
                metadata={"examples": 4},
            ).save()

            models = ShadowModels.load(
                route_model_path=root / "missing_route.json",
                potion_model_path=root / "missing_potion.json",
                deck_model_path=root / "missing_deck.json",
                combat_model_path=combat_path,
            )
            advice = score_shadow_inputs([shadow], models=models)

        combat = advice["combat_search"][0]
        self.assertEqual(combat["advice"], "review_missed_single_card_search")
        self.assertTrue(combat["label_missed_single_card_search"])
        self.assertEqual(combat["actual_card_index"], 2)
        self.assertEqual(combat["label_card_index"], 1)
        self.assertEqual(combat["label_target_index"], 1)
        self.assertEqual(combat["attacks_removed"], 0)
        self.assertEqual(combat["retaliation_damage"], 24)

    def test_missing_models_still_emit_diagnostic_advice_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [{"source_log": "run.jsonl", "step": 1, "floor": 4, "act": 1, "hp_ratio": 0.3}],
            )

            models = ShadowModels.load(
                route_model_path=root / "missing_route.json",
                potion_model_path=root / "missing_potion.json",
                deck_model_path=root / "missing_deck.json",
                combat_model_path=root / "missing_combat.json",
            )
            advice = score_shadow_inputs([shadow], models=models)

        self.assertEqual(len(advice["route_risk"]), 1)
        self.assertFalse(advice["route_risk"][0]["model_available"])
        self.assertIsNone(advice["route_risk"][0]["route_risk_score"])
        self.assertEqual(advice["route_risk"][0]["advice"], "model_missing")

    def test_in_memory_shadow_examples_record_source_mode_without_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            models = ShadowModels.load(
                route_model_path=root / "missing_route.json",
                potion_model_path=root / "missing_potion.json",
                deck_model_path=root / "missing_deck.json",
                combat_model_path=root / "missing_combat.json",
            )
            advice = score_shadow_examples(
                {"route_risk": [{"source_log": "run.jsonl", "step": 1, "floor": 4, "act": 1, "hp_ratio": 0.3}]},
                models=models,
            )
            summary = write_advice(root / "advice", advice)

        row = advice["route_risk"][0]
        self.assertEqual(row["shadow_source_mode"], "in_memory_examples")
        self.assertEqual(row["shadow_source_category"], "route_risk")
        self.assertNotIn("shadow_source_file", row)
        self.assertEqual(summary["shadow_inputs"]["mode"], "in_memory_examples")
        self.assertEqual(summary["shadow_inputs"]["resolved_files"], [])
        self.assertEqual(summary["shadow_inputs"]["categories"]["route_risk"]["row_count"], 1)
        self.assertEqual(summary["shadow_inputs"]["categories"]["route_risk"]["warnings"], [])

    def test_write_advice_preserves_empty_shadow_files_when_source_given(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [{"source_log": "run.jsonl", "step": 1, "floor": 4, "act": 1, "hp_ratio": 0.3}],
            )
            (shadow / "combat_search_labels.jsonl").write_text("", encoding="utf-8")
            models = ShadowModels.load(
                route_model_path=root / "missing_route.json",
                potion_model_path=root / "missing_potion.json",
                deck_model_path=root / "missing_deck.json",
                combat_model_path=root / "missing_combat.json",
            )
            advice = score_shadow_inputs([shadow], models=models)
            summary = write_advice(root / "advice", advice, shadow_inputs=resolve_shadow_training_source([shadow]))

        self.assertEqual(summary["shadow_inputs"]["mode"], "shadow_rows")
        self.assertIn(str(shadow / "combat_search_labels.jsonl"), summary["shadow_inputs"]["resolved_files"])
        self.assertEqual(summary["shadow_inputs"]["categories"]["combat_search"]["row_count"], 0)
        self.assertEqual(
            summary["shadow_inputs"]["categories"]["combat_search"]["resolved_files"],
            [str(shadow / "combat_search_labels.jsonl")],
        )
        self.assertEqual(summary["shadow_inputs"]["categories"]["combat_search"]["warnings"], [])


if __name__ == "__main__":
    unittest.main()
