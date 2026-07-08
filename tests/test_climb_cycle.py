import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from slay_ai.climb_cycle import _train_combat_value_model_from_shadow, run_climb_cycle
from slay_ai.import_external_runs import CARD_PRIOR_ROWS_FILE


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class ClimbCycleTests(unittest.TestCase):
    def test_skip_live_cycle_replays_logs_and_trains_gated_models(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "runs" / "clean.jsonl"
            write_jsonl(log, clean_rows())
            external_rows = root / "external" / CARD_PRIOR_ROWS_FILE
            raw_external = root / "external" / "raw_runs.jsonl"
            write_jsonl(
                raw_external,
                [
                    {
                        "character_chosen": "IRONCLAD",
                        "ascension_level": 0,
                        "victory": True,
                        "floor_reached": 57,
                        "score": 3210,
                        "card_choices": [
                            {"floor": 1, "picked": "Shrug It Off", "not_picked": ["Flex", "Clash"]},
                            {"floor": 4, "picked": "SKIP", "not_picked": ["Clash", "Fire Breathing"]},
                        ],
                        "items_purged": ["Strike_R"],
                        "items_purged_floors": [8],
                        "purchased_purges": 1,
                        "master_deck": ["Strike_R", "Defend_R", "Bash", "Shrug It Off"],
                    }
                ],
            )
            write_jsonl(
                external_rows,
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
                        "source_dataset": "unit_external",
                        "source_weight": 0.2,
                    }
                ],
            )
            external_manifest = root / "external" / "external_manifest.json"
            external_manifest.write_text(
                json.dumps(
                    {
                        "manifest_type": "external_prior_manifest",
                        "source_id": "unit_external",
                        "resolved_files": [str(raw_external)],
                        "artifacts": {"card_reward_priors": str(external_rows)},
                    }
                ),
                encoding="utf-8",
            )

            result = run_climb_cycle(
                name="unit_cycle",
                run_live=False,
                attempts_per_target=0,
                logs=[log],
                output_dir=root / "cycles",
                knowledge_dir=None,
                model_dir=root / "models",
                card_model_path=root / "models" / "card_value_model.json",
                learned_path=root / "learned_memory.json",
                reset_learned_memory=True,
                source_quality="pristine",
                min_count=1,
                external_prior_inputs=[external_manifest],
                external_prior_min_count=1,
                external_prior_blend_scale=0.1,
                external_prior_blend_max_delta=0.2,
                model_authority="assist",
                gate_min_reached=1,
                gate_min_cleared=0,
                gate_min_pristine_cleared=0,
            )

            manifest_path = Path(result["offline_batch"]["manifest_path"])
            shadow_dir = Path(result["offline_batch"]["shadow_dir"])
            card_model_path = root / "models" / "card_value_model.json"
            learned_path = root / "learned_memory.json"

            self.assertFalse(result["live"]["ran"])
            self.assertTrue(manifest_path.exists())
            self.assertTrue((shadow_dir / "route_risk.jsonl").exists())
            self.assertEqual(result["training"]["shadow_models"]["status"], "trained")
            self.assertGreater(result["training"]["shadow_models"]["accepted_rows"], 0)
            self.assertEqual(result["training"]["card_model"]["status"], "trained")
            self.assertTrue(card_model_path.exists())
            self.assertTrue((root / "models" / "route_risk_model.json").exists())
            self.assertTrue((root / "models" / "potion_tempo_model.json").exists())
            self.assertTrue((root / "models" / "deck_quality_model.json").exists())
            self.assertFalse((root / "models" / "combat_search_model.json").exists())
            self.assertEqual(
                result["training"]["shadow_models"]["models"]["combat_search"]["status"],
                "skipped",
            )
            self.assertEqual(result["training"]["combat_value_model"]["status"], "skipped")
            self.assertFalse(result["training"]["combat_value_model"].get("runtime_authority", False))
            self.assertEqual(result["training"]["external_priors"]["status"], "trained")
            self.assertFalse(result["training"]["external_priors"]["runtime_authority"])
            self.assertTrue(Path(result["training"]["external_priors"]["model_path"]).exists())
            self.assertEqual(result["training"]["external_structure_priors"]["status"], "trained")
            self.assertFalse(result["training"]["external_structure_priors"]["runtime_authority"])
            self.assertEqual(result["training"]["external_structure_priors"]["reward_decision_rows"], 2)
            self.assertEqual(result["training"]["external_structure_priors"]["purge_rows"], 1)
            self.assertEqual(result["training"]["external_structure_priors"]["deck_cycle_rows"], 1)
            self.assertTrue(Path(result["training"]["external_structure_priors"]["model_path"]).exists())
            external_decision = result["training"]["external_structure_decision_model"]
            self.assertEqual(external_decision["status"], "trained")
            self.assertEqual(external_decision["model_kind"], "decision_multitask_shadow")
            self.assertFalse(external_decision["runtime_authority"])
            self.assertFalse(external_decision["runtime_default_enabled"])
            self.assertEqual(external_decision["runtime_authority_level"], "shadow")
            self.assertTrue(external_decision["does_not_control_live_mcp"])
            self.assertFalse(external_decision["direct_mcp_control"])
            self.assertTrue(external_decision["requires_audited_promotion"])
            self.assertIn("runtime_authority", external_decision["forbidden_uses"])
            self.assertEqual(external_decision["row_counts"]["card_reward_decisions"], 2)
            self.assertEqual(external_decision["row_counts"]["card_purge_priors"], 1)
            self.assertEqual(external_decision["row_counts"]["deck_cycle_priors"], 1)
            self.assertGreaterEqual(external_decision["examples"], 4)
            self.assertTrue(Path(external_decision["model_path"]).exists())
            self.assertTrue(Path(external_decision["summary_path"]).exists())
            self.assertTrue(Path(external_decision["prediction_path"]).exists())
            self.assertIn("take_skip", external_decision["evaluation"]["all"])
            self.assertIn("accuracy", external_decision["evaluation"]["all"]["take_skip"])
            self.assertIn("precision", external_decision["evaluation"]["all"]["purge_remove"])
            self.assertIn("mae", external_decision["evaluation"]["all"]["deck_cycle_quality"])
            self.assertEqual(result["training"]["card_external_prior_blend"]["status"], "trained")
            self.assertFalse(result["training"]["card_external_prior_blend"]["runtime_authority"])
            blend_path = Path(result["training"]["card_external_prior_blend"]["model_path"])
            self.assertTrue(blend_path.exists())
            self.assertNotEqual(blend_path, card_model_path)
            blend_payload = json.loads(blend_path.read_text(encoding="utf-8"))
            self.assertEqual(blend_payload["metadata"]["model_kind"], "card_value_external_prior_blend")
            self.assertTrue(blend_payload["metadata"]["requires_audited_promotion"])
            self.assertTrue(blend_payload["metadata"]["does_not_control_live_mcp"])
            self.assertEqual(blend_payload["metadata"]["external_prior_blend_scale"], 0.1)
            self.assertEqual(blend_payload["metadata"]["external_prior_max_contribution"], 0.2)
            self.assertTrue(learned_path.exists())
            self.assertIn("stage", result)
            self.assertIn("shadow=trained", result["status_line"])
            self.assertIn("combat_value=skipped", result["status_line"])
            self.assertIn("external_prior=trained", result["training"]["status_line"])
            self.assertIn("external_structure=trained", result["training"]["status_line"])
            self.assertIn("external_decision=trained", result["training"]["status_line"])
            self.assertIn("external_decision=trained", result["status_line"])
            self.assertIn("external_blend=trained", result["training"]["status_line"])
            self.assertEqual(result["policy_boundary"]["heuristic_strategy_investment"], "frozen")
            self.assertEqual(result["policy_boundary"]["model_authority"], "assist")
            self.assertEqual(
                result["policy_boundary"]["model_authority_scope"],
                "route_risk_card_reward_and_potion_tempo_assist",
            )
            self.assertEqual(result["run_reviews"]["status"], "written")
            review_text = Path(result["run_reviews"]["reviews"][0]["path"]).read_text(encoding="utf-8")
            self.assertIn("# 单局复盘", review_text)
            self.assertIn("## 学习与模型的影响", review_text)
            self.assertIn("本轮外部数据 prior 状态", review_text)
            self.assertIn("本轮外部融合候选状态", review_text)
            self.assertIn("下列行为由模型/学习信号实际参与接管或改选", review_text)
            self.assertIn("route-risk assist", review_text)
            self.assertIn("External structure decision shadow", review_text)
            self.assertIn("direct_mcp_control=False", review_text)
            self.assertIn("Shrug It Off", review_text)

    def test_cycle_skips_model_writes_when_no_clean_training_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "runs" / "diagnostic.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 1,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                        },
                    }
                ],
            )

            result = run_climb_cycle(
                name="empty_cycle",
                run_live=False,
                attempts_per_target=0,
                logs=[log],
                output_dir=root / "cycles",
                knowledge_dir=None,
                model_dir=root / "models",
                card_model_path=root / "models" / "card_value_model.json",
                learned_path=root / "learned_memory.json",
                source_quality="pristine",
                min_count=1,
            )

            self.assertEqual(result["training"]["shadow_models"]["status"], "skipped")
            self.assertEqual(result["training"]["card_model"]["status"], "skipped")
            self.assertEqual(result["training"]["combat_value_model"]["status"], "skipped")
            self.assertEqual(result["training"]["learned_memory"]["status"], "skipped")
            self.assertEqual(result["training"]["external_priors"]["status"], "skipped")
            self.assertEqual(result["training"]["external_structure_priors"]["status"], "skipped")
            self.assertEqual(result["training"]["external_structure_decision_model"]["status"], "skipped")
            self.assertFalse(result["training"]["external_structure_decision_model"]["runtime_authority"])
            self.assertFalse(result["training"]["external_structure_decision_model"]["direct_mcp_control"])
            self.assertEqual(result["training"]["card_external_prior_blend"]["status"], "skipped")
            self.assertEqual(
                result["training"]["external_priors"]["reason"],
                "external_prior_inputs_not_configured",
            )
            self.assertFalse((root / "models" / "route_risk_model.json").exists())
            self.assertFalse((root / "models" / "card_value_model.json").exists())

    def test_cycle_skips_external_prior_when_rows_are_not_external_quality(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "runs" / "clean.jsonl"
            write_jsonl(log, clean_rows())
            external_rows = root / "external" / CARD_PRIOR_ROWS_FILE

            write_jsonl(
                external_rows,
                [
                    {
                        "character": "IRONCLAD",
                        "picked": "Inflame",
                        "victory": True,
                        "final_floor": 50,
                        "source_validation_grade": "pristine",
                    }
                ],
            )

            result = run_climb_cycle(
                name="bad_external_cycle",
                run_live=False,
                attempts_per_target=0,
                logs=[log],
                output_dir=root / "cycles",
                knowledge_dir=None,
                model_dir=root / "models",
                card_model_path=root / "models" / "card_value_model.json",
                learned_path=root / "learned_memory.json",
                reset_learned_memory=True,
                source_quality="pristine",
                min_count=1,
                external_prior_inputs=[external_rows],
                external_prior_min_count=1,
            )

            external = result["training"]["external_priors"]
            self.assertEqual(external["status"], "skipped")
            self.assertEqual(external["reason"], "no_external_prior_examples")
            self.assertEqual(external["load_quality"]["skipped"], 1)
            self.assertEqual(external["load_quality"]["skip_reasons"], {"source_quality": 1})
            self.assertFalse(Path(external["model_path"]).exists())
            self.assertEqual(result["training"]["card_external_prior_blend"]["status"], "skipped")

    def test_combat_value_uses_weighted_training_for_usable_cycle_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_validation_grade": "usable_with_recoveries",
                        "source_category": "clean_trainable",
                        "source_reason": "completed_clean",
                        "source_recovered_actions": 1,
                        "source_failed_actions": 0,
                        "act": 2,
                        "floor": 31,
                        "turn": 4,
                        "hp_ratio": 0.25,
                        "current_hp": 14,
                        "max_hp": 56,
                        "current_block": 0,
                        "current_energy": 2,
                        "incoming": 21,
                        "hand_size": 5,
                        "playable_count": 4,
                        "enemy_count": 2,
                        "enemy_ids": ["Centurion", "Mystic"],
                        "hand_ids": ["Ghostly Armor", "Defend_R", "Berserk"],
                        "label_first_card_key": "Ghostly Armor",
                        "label_sequence_card_keys": ["Ghostly Armor", "Defend_R"],
                        "initial_loss": 21,
                        "projected_loss": 3,
                        "attacks_removed": 0,
                        "kills": 0,
                        "retaliation_damage": 0,
                        "avoided_lethal": False,
                    }
                ],
            )

            fake_summary = {
                "backend": "pytorch_mlp",
                "examples": 1,
                "validation_examples": 0,
                "device": "cpu",
                "train_loss": 0.1,
                "val_loss": 0.1,
                "source_quality_counts": {"usable_with_recoveries": 1},
            }
            with patch("slay_ai.climb_cycle.train_combat_value_torch_model") as train:
                train.return_value = {"summary": fake_summary}
                result = _train_combat_value_model_from_shadow(
                    shadow,
                    model_path=root / "combat_value_model.pt",
                    summary_output=root / "combat_value_summary.json",
                    source_quality="usable",
                )

            self.assertEqual(result["status"], "trained")
            self.assertEqual(result["quality_policy"], "weighted")
            self.assertFalse(result["runtime_authority"])
            self.assertEqual(result["source_quality_counts"], {"usable_with_recoveries": 1})
            train.assert_called_once()
            _, kwargs = train.call_args
            self.assertEqual(kwargs["quality_policy"], "weighted")
            self.assertAlmostEqual(train.call_args.args[0][0].sample_weight, 0.35)


def clean_rows() -> list[dict]:
    return [
        {
            "step": 1,
            "state": {
                "screen_type": "CARD_REWARD",
                "floor": 4,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 70,
                "max_hp": 80,
                "card_reward_options": [
                    {"id": "Shrug It Off", "name": "Shrug It Off"},
                    {"id": "Cleave", "name": "Cleave"},
                    {"id": "Anger", "name": "Anger"},
                ],
            },
            "decision": {
                "actions": [{"action": "choose", "choice_index": 1}],
                "learn_card_pick": "Shrug It Off",
                "metadata": {
                    "model_authority": {
                        "surface": "card_reward",
                        "level": "assist",
                        "selection_source": "model_authority_tiebreaker",
                        "selected_choice_index": 1,
                        "runtime_authority": True,
                        "options": [
                            {
                                "choice_index": 1,
                                "card": "Shrug It Off",
                                "heuristic_total_score": 58.0,
                                "model_signal": 6.0,
                            },
                            {
                                "choice_index": 2,
                                "card": "Cleave",
                                "heuristic_total_score": 62.0,
                                "model_signal": 0.0,
                            },
                        ],
                    }
                },
            },
        },
        {
            "step": 2,
            "state": {
                "screen_type": "MAP",
                "floor": 15,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 66,
                "max_hp": 80,
                "gold": 39,
                "deck": ["Strike_R", "Defend_R", "Bash", "Shrug It Off"],
                "relics": ["Burning Blood"],
                "potions": [{"id": "Dexterity Potion"}],
                "boss_available": True,
                "route_evaluation": {
                    "options": [
                        {
                            "choice_index": 1,
                            "symbol": "B",
                            "score": 100,
                            "model_adjustment": -18.0,
                            "model_assist": {
                                "status": "scored",
                                "model_type": "route_risk",
                                "risk_score": 0.78,
                                "penalty": -18.0,
                                "runtime_authority": False,
                                "runtime_authority_level": "assist",
                                "decision_influence": "route_score_adjustment",
                                "direct_mcp_control": False,
                                "does_not_control_live_mcp": True,
                            },
                            "lookahead": {
                                "forced_elite_within_3": False,
                                "forced_combat_within_2": False,
                                "nearest_rest": 0,
                                "nearest_shop": None,
                                "readiness_penalty": 12,
                            },
                        }
                    ]
                },
            },
            "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
        },
        {
            "step": 3,
            "state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 30,
                "max_hp": 80,
                "potions": [{"id": "Dexterity Potion"}],
                "combat": {
                    "turn": 2,
                    "incoming_damage": 18,
                    "monsters": [{"id": "Hexaghost"}],
                },
            },
            "decision": {"actions": [{"action": "use_potion", "potion_slot": 1}]},
        },
        {
            "step": 4,
            "state": {
                "screen_type": "GAME_OVER",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "outcome": {"victory": False, "score": 321},
            },
            "decision": {"actions": []},
        },
    ]


if __name__ == "__main__":
    unittest.main()
