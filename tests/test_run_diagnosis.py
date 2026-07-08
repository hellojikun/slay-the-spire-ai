import json
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.run_diagnosis import diagnose_manifest, diagnose_manifest_runs, main, summarize_advice


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class RunDiagnosisTests(unittest.TestCase):
    def test_clean_manifest_with_shadow_advice_summarizes_failure_and_signals(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            advice = root / "advice"
            advice.mkdir()
            write_jsonl(
                advice / "route_risk_advice.jsonl",
                [
                    {
                        "advice": "route_ok",
                        "model_available": True,
                        "route_risk_score": 0.2,
                        "shadow_source_file": "shadow/route_risk.jsonl",
                        "shadow_source_mode": "shadow_rows",
                        "floor": 3,
                    },
                    {
                        "advice": "avoid_or_require_recovery",
                        "model_available": True,
                        "route_risk_score": 0.82,
                        "shadow_source_file": "shadow/route_risk.jsonl",
                        "shadow_source_mode": "shadow_rows",
                        "floor": 14,
                    },
                ],
            )
            write_jsonl(
                advice / "potion_tempo_advice.jsonl",
                [
                    {
                        "advice": "watch_potion_tempo",
                        "model_available": True,
                        "potion_tempo_score": 0.48,
                        "floor": 16,
                        "incoming": 24,
                        "potion_ids": ["BlockPotion", "FearPotion"],
                        "used_potion": False,
                        "used_potion_slot": None,
                        "used_potion_targeted": False,
                        "enemy_ids": ["TheGuardian"],
                        "potion_block_value": 12,
                        "potion_vulnerable_value": 3,
                        "enemy_total_expected_attack": 32,
                        "enemy_boss_count": 1,
                        "boss_identity_known": True,
                        "boss_known_count": 1,
                    }
                ],
            )
            write_jsonl(
                advice / "pre_boss_deck_quality_advice.jsonl",
                [{"advice": "boss_ready", "model_available": True, "deck_quality_score": 0.8, "floor": 15}],
            )
            write_jsonl(
                advice / "combat_search_advice.jsonl",
                [
                    {
                        "advice": "review_search_label",
                        "model_available": True,
                        "combat_search_score": 2.1,
                        "label_first_card_key": "bash",
                        "model_top_card_key": "defendr",
                        "model_agrees_with_label": False,
                        "loss_delta": 12,
                        "attacks_removed": 0,
                        "retaliation_damage": 24,
                        "shadow_source_file": "shadow/combat_search_labels.jsonl",
                        "shadow_source_mode": "shadow_rows",
                        "model_training_source_quality": "usable",
                        "model_training_source": {
                            "category": "combat_search",
                            "resolved_files": ["shadow/combat_search_labels.jsonl"],
                        },
                        "model_load_quality": {
                            "files": 1,
                            "rows": 4,
                            "accepted": 3,
                            "skipped": 1,
                            "skip_reasons": {"missed_direct_kill": 1},
                        },
                        "floor": 16,
                    }
                ],
            )
            write_json(
                advice / "summary.json",
                {
                    "version": 1,
                    "shadow_inputs": {
                        "mode": "shadow_rows",
                        "resolved_files": [
                            "shadow/route_risk.jsonl",
                            "shadow/potion_tempo.jsonl",
                            "shadow/pre_boss_deck_quality.jsonl",
                            "shadow/combat_search_labels.jsonl",
                        ],
                        "resolved_file_count": 4,
                        "categories": {
                            "route_risk": {
                                "row_count": 2,
                                "resolved_files": ["shadow/route_risk.jsonl"],
                                "resolved_file_count": 1,
                                "warnings": [],
                            },
                            "combat_search": {
                                "row_count": 1,
                                "resolved_files": ["shadow/combat_search_labels.jsonl"],
                                "resolved_file_count": 1,
                                "warnings": [],
                            },
                        },
                        "warnings": [],
                    },
                    "model_training_source": {
                        "combat_search": {
                            "category": "combat_search",
                            "resolved_files": ["shadow/combat_search_labels.jsonl"],
                        }
                    },
                    "model_training_source_quality": {"combat_search": "usable"},
                },
            )
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "summary": {
                        "shadow_examples": {"route_risk": 2},
                        "shadow_feature_coverage": {
                            "total_rows": 2,
                            "categories": {
                                "route_risk": {
                                    "rows": 2,
                                    "source_quality": {"pristine": 2},
                                    "feature_prefixes": {
                                        "deck_": {"field_count": 3, "rows_with_nonzero": 2}
                                    },
                                }
                            },
                        },
                        "shadow_label_quality": {
                            "combat_search_labels": {
                                "total": 4,
                                "trainable": 3,
                                "excluded_from_training": 1,
                                "direct_kill_available": 1,
                                "exclusion_reasons": {"missed_direct_kill": 1},
                                "exclusion_examples": [
                                    {
                                        "source_log": "run.jsonl",
                                        "reasons": ["missed_direct_kill"],
                                        "turn": 9,
                                        "label_first_card_key": "bash",
                                        "direct_kill_card_keys": ["strike_r"],
                                        "enemy_ids": ["TheGuardian"],
                                    }
                                ],
                            }
                        },
                    },
                    "shadow_advice": {"path": str(advice)},
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "victory": False,
                                "steps": 100,
                                "recovered_actions": 1,
                                "failed_actions": 0,
                                "action_recovery_summary": {
                                    "total": 1,
                                    "recovered": 1,
                                    "unrecovered": 0,
                                    "by_kind": {"stale_target_index": 1},
                                },
                                "failure_attribution": "potion_planning",
                                "failure_tags": ["boss_combat", "potion_planning"],
                                "validation_grade": "usable_with_recoveries",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": False,
                                        "enemy_ids": ["TheGuardian"],
                                        "last_turn": 9,
                                        "last_hp": 0,
                                        "potion_use_steps": [41, 42],
                                    }
                                },
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    },
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["category"], "clean_trainable")
        self.assertEqual(diagnosis["failure_attribution"], "potion_planning")
        self.assertEqual(diagnosis["next_action"], "inspect_potion_planning")
        self.assertEqual(diagnosis["recommended_assignment"]["owner"], "ai_agent")
        self.assertEqual(diagnosis["recommended_assignment"]["action"], "inspect_potion_planning")
        self.assertEqual(
            diagnosis["recommended_assignment"]["execution_contract"]["mode"],
            "offline_strategy_or_shadow_model",
        )
        self.assertFalse(
            diagnosis["recommended_assignment"]["execution_contract"]["requires_live_mcp_ownership"]
        )
        self.assertIn(
            "replace_live_policy_with_model",
            diagnosis["recommended_assignment"]["execution_contract"]["forbidden_operations"],
        )
        self.assertIn("Run diagnosis assignment", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("Agent: ai_agent", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("Action: inspect_potion_planning", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("Source log: run.jsonl", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("label_exclusions=combat_search:1/missed_direct_kill:1", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("label_exclusion_examples", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("TheGuardian", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertEqual(diagnosis["validation_grade"], "usable_with_recoveries")
        self.assertEqual(diagnosis["act1_boss"]["enemy_ids"], ["TheGuardian"])
        self.assertEqual(diagnosis["action_recovery_summary"]["by_kind"]["stale_target_index"], 1)
        self.assertEqual(diagnosis["shadow_feature_gaps"]["total_rows"], 2)
        self.assertEqual(diagnosis["shadow_feature_gaps"]["gap_count"], 0)
        self.assertEqual(diagnosis["shadow_advice"]["categories"]["route_risk"]["high_risk_count"], 1)
        self.assertEqual(diagnosis["shadow_advice"]["categories"]["potion_tempo"]["watch_count"], 1)
        self.assertEqual(diagnosis["shadow_advice"]["categories"]["combat_search"]["watch_count"], 1)
        potion_signal = diagnosis["shadow_advice"]["categories"]["potion_tempo"]["top_signal"]
        self.assertEqual(potion_signal["incoming"], 24)
        self.assertEqual(potion_signal["potion_ids"], ["BlockPotion", "FearPotion"])
        self.assertFalse(potion_signal["used_potion"])
        self.assertEqual(potion_signal["enemy_ids"], ["TheGuardian"])
        self.assertEqual(potion_signal["potion_block_value"], 12)
        self.assertEqual(potion_signal["potion_vulnerable_value"], 3)
        self.assertEqual(potion_signal["enemy_total_expected_attack"], 32)
        self.assertEqual(potion_signal["enemy_boss_count"], 1)
        self.assertTrue(potion_signal["boss_identity_known"])
        self.assertEqual(diagnosis["shadow_advice"]["shadow_inputs"]["resolved_file_count"], 4)
        self.assertEqual(
            diagnosis["shadow_advice"]["categories"]["route_risk"]["shadow_source_files"],
            ["shadow/route_risk.jsonl"],
        )
        self.assertEqual(
            diagnosis["shadow_advice"]["categories"]["combat_search"]["model_training_source"]["category"],
            "combat_search",
        )
        self.assertEqual(
            diagnosis["shadow_advice"]["categories"]["combat_search"]["model_training_source_quality"],
            "usable",
        )
        self.assertEqual(
            diagnosis["shadow_advice"]["categories"]["combat_search"]["model_load_quality"]["skip_reasons"],
            {"missed_direct_kill": 1},
        )
        combat_signal = diagnosis["shadow_advice"]["categories"]["combat_search"]["top_signal"]
        self.assertEqual(combat_signal["label_first_card_key"], "bash")
        self.assertEqual(combat_signal["model_top_card_key"], "defendr")
        self.assertEqual(combat_signal["loss_delta"], 12)
        self.assertEqual(combat_signal["attacks_removed"], 0)
        self.assertEqual(combat_signal["retaliation_damage"], 24)
        self.assertEqual(
            diagnosis["shadow_label_quality"]["combat_search_labels"]["excluded_from_training"],
            1,
        )
        self.assertEqual(
            diagnosis["recommended_assignment"]["evidence"]["label_exclusion_examples"][0]["reasons"],
            ["missed_direct_kill"],
        )
        self.assertIn("clean_trainable/potion_planning", diagnosis["status_line"])
        self.assertIn("recovery=stale_target_index:1", diagnosis["status_line"])
        self.assertIn("validation=usable_with_recoveries", diagnosis["status_line"])
        self.assertIn("act1_boss=reached:TheGuardian[T9,hp=0,potions=2]", diagnosis["status_line"])
        self.assertIn("features=2rows", diagnosis["status_line"])
        self.assertIn("label_exclusions=combat_search:1/missed_direct_kill:1", diagnosis["status_line"])
        self.assertIn("route_risk:high=1", diagnosis["status_line"])
        self.assertIn("combat_search:high=0,watch=1", diagnosis["status_line"])
        self.assertIn(
            "signals=potion_tempo[inc=24,potions=BlockPotion|FearPotion,enemy=TheGuardian,used=false,block=12,vuln=3,enemy_attack=32,boss_known=1]",
            diagnosis["status_line"],
        )
        self.assertIn(
            "combat_search[label=bash,model=defendr,loss_delta=12,attacks_removed=0,retaliation=24]",
            diagnosis["status_line"],
        )
        self.assertIn("model_skips=combat_search:1/missed_direct_kill:1", diagnosis["status_line"])

    def test_status_line_surfaces_shadow_feature_gaps(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "summary": {
                        "shadow_feature_coverage": {
                            "total_rows": 3,
                            "categories": {
                                "route_risk": {
                                    "rows": 3,
                                    "source_quality": {"pristine": 3},
                                    "unknown_static_features": {
                                        "deck_unknown_cards": {"total": 2, "nonzero": 1}
                                    },
                                    "feature_prefixes": {
                                        "deck_": {"field_count": 0, "rows_with_nonzero": 0}
                                    },
                                },
                                "potion_tempo": {
                                    "rows": 2,
                                    "source_quality": {"pristine": 2},
                                    "feature_prefixes": {
                                        "potion_": {"field_count": 4, "rows_with_nonzero": 2},
                                        "enemy_": {"field_count": 3, "rows_with_nonzero": 0},
                                    },
                                },
                            },
                        }
                    },
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 8,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "combat_planning",
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    },
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["shadow_feature_gaps"]["gap_count"], 1)
        self.assertEqual(diagnosis["shadow_feature_gaps"]["zero_count"], 1)
        self.assertEqual(
            diagnosis["shadow_feature_gaps"]["categories"]["route_risk"]["missing_prefixes"],
            ["deck_"],
        )
        self.assertEqual(
            diagnosis["shadow_feature_gaps"]["categories"]["potion_tempo"]["zero_prefixes"],
            ["enemy_"],
        )
        self.assertIn("features=3rows", diagnosis["status_line"])
        self.assertIn("feature_gaps=route_risk:deck_", diagnosis["status_line"])
        self.assertIn("feature_zero=potion_tempo:enemy_", diagnosis["status_line"])
        self.assertIn("feature_unknown=route_risk:deck_unknown_cards:2", diagnosis["status_line"])

    def test_diagnosis_falls_back_to_adjacent_shadow_rows_for_feature_coverage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe777_a0.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 8,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "combat_planning",
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    },
                },
            )
            write_jsonl(
                root / "shadow_probe777_a0" / "route_risk.jsonl",
                [{"source_validation_grade": "pristine", "floor": 3, "deck_total_current_damage": 38}],
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["shadow_feature_coverage_source"], "shadow_dir_fallback")
        self.assertEqual(diagnosis["shadow_feature_gaps"]["total_rows"], 1)
        self.assertIn("features=1rows", diagnosis["status_line"])

    def test_diagnosis_marks_empty_adjacent_shadow_rows_without_feature_coverage(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe778_a0.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 8,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "combat_planning",
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    },
                },
            )
            write_jsonl(root / "shadow_probe778_a0" / "route_risk.jsonl", [])

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["shadow_feature_coverage_source"], "shadow_dir_empty")
        self.assertFalse(diagnosis["shadow_feature_gaps"]["available"])
        self.assertIn("features=shadow_dir_empty", diagnosis["status_line"])

    def test_diagnostic_boss_reach_surfaces_validation_evidence(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [],
                        "diagnostic_excluded": [
                            {
                                "path": "run.jsonl",
                                "reason": "no_terminal_outcome",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "recovered_actions": 1,
                                "failed_actions": 0,
                                "action_recovery_summary": {"by_kind": {"unavailable_action": 1}},
                                "validation_grade": "diagnostic",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": False,
                                        "enemy_ids": ["SlimeBoss"],
                                        "last_turn": 8,
                                        "last_hp": 35,
                                        "potion_use_steps": [196],
                                    }
                                },
                            }
                        ],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["category"], "diagnostic_excluded")
        self.assertEqual(diagnosis["next_action"], "exclude_and_collect_terminal_boss_evidence")
        self.assertEqual(diagnosis["recommended_assignment"]["owner"], "runner_agent")
        self.assertEqual(
            diagnosis["recommended_assignment"]["execution_contract"]["mode"],
            "live_mcp_gated_collection",
        )
        self.assertTrue(
            diagnosis["recommended_assignment"]["execution_contract"]["requires_live_mcp_ownership"]
        )
        self.assertIn("Agent: runner_agent", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn(
            "Action: exclude_and_collect_terminal_boss_evidence",
            diagnosis["recommended_assignment"]["assignment_prompt"],
        )
        self.assertEqual(diagnosis["validation_grade"], "diagnostic")
        self.assertEqual(diagnosis["act1_boss"]["enemy_ids"], ["SlimeBoss"])
        self.assertIn("act1_boss=reached:SlimeBoss[T8,hp=35,potions=1]", diagnosis["status_line"])
        self.assertIn("validation=diagnostic", diagnosis["status_line"])

    def test_boss_clear_status_line_surfaces_prefix_pristine_evidence(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 30,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "route_risk",
                                "validation_grade": "usable_with_recoveries",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": True,
                                        "enemy_ids": ["TheGuardian"],
                                        "last_turn": 12,
                                        "last_hp": 29,
                                        "clear_step": 221,
                                        "prefix_pristine_clear": True,
                                        "prefix_blockers": [],
                                        "potion_use_steps": [206],
                                    }
                                },
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertIn(
            "act1_boss=cleared:TheGuardian[T12,hp=29,potions=1,clear_step=221,prefix_pristine=true]",
            diagnosis["status_line"],
        )

    def test_diagnostic_boss_clear_preserves_prefix_pristine_validation(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [],
                        "diagnostic_excluded": [
                            {
                                "path": "run.jsonl",
                                "reason": "no_terminal_outcome",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 33,
                                "recovered_actions": 3,
                                "failed_actions": 0,
                                "validation_grade": "diagnostic",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": True,
                                        "enemy_ids": ["TheGuardian"],
                                        "last_turn": 12,
                                        "last_hp": 29,
                                        "clear_step": 221,
                                        "prefix_pristine_clear": True,
                                        "prefix_blockers": [],
                                    }
                                },
                            }
                        ],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["next_action"], "preserve_boss_validation_evidence")
        self.assertEqual(diagnosis["recommended_assignment"]["owner"], "main_agent")
        self.assertEqual(diagnosis["recommended_assignment"]["execution_contract"]["mode"], "coordination_only")
        self.assertIn("Agent: main_agent", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("prefix_pristine", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("act1_boss=cleared:TheGuardian", diagnosis["status_line"])
        self.assertIn("prefix_pristine=true", diagnosis["status_line"])

    def test_diagnostic_boss_clear_with_prefix_blockers_collects_pristine_clear(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [],
                        "diagnostic_excluded": [
                            {
                                "path": "run.jsonl",
                                "reason": "no_terminal_outcome",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 33,
                                "recovered_actions": 3,
                                "failed_actions": 0,
                                "validation_grade": "diagnostic",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": True,
                                        "enemy_ids": ["TheGuardian"],
                                        "last_turn": 12,
                                        "last_hp": 29,
                                        "clear_step": 221,
                                        "prefix_pristine_clear": False,
                                        "prefix_blockers": ["recovered_action_race"],
                                    }
                                },
                            }
                        ],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["next_action"], "exclude_and_collect_pristine_boss_clear")
        self.assertEqual(diagnosis["recommended_assignment"]["owner"], "runner_agent")
        self.assertEqual(
            diagnosis["recommended_assignment"]["execution_contract"]["mode"],
            "live_mcp_gated_collection",
        )
        self.assertTrue(
            diagnosis["recommended_assignment"]["execution_contract"]["requires_live_mcp_ownership"]
        )
        self.assertIn("Agent: runner_agent", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("prefix_pristine=false", diagnosis["status_line"])
        self.assertIn("prefix_blockers=recovered_action_race", diagnosis["status_line"])

    def test_infra_manifest_falls_back_to_mcp_execution(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [],
                        "diagnostic_excluded": [],
                        "infra_blocked": [
                            {
                                "path": "run.jsonl",
                                "reason": "boss_reward_screen_stall",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 17,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_evidence": {
                                    "reason": "boss_reward_screen_stall",
                                    "screen_stall": {
                                        "screen_type": "BOSS_REWARD",
                                        "floor": 17,
                                        "repeat_count": 3,
                                        "first_step": 10,
                                        "last_step": 12,
                                        "last_relic_count": 3,
                                        "last_actions": [{"action": "choose", "choice_index": 1}],
                                    },
                                },
                            }
                        ],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["category"], "infra_blocked")
        self.assertEqual(diagnosis["failure_attribution"], "mcp_execution")
        self.assertEqual(diagnosis["failure_evidence"]["screen_stall"]["screen_type"], "BOSS_REWARD")
        self.assertEqual(diagnosis["next_action"], "fix_execution_layer")
        self.assertEqual(diagnosis["recommended_assignment"]["owner"], "engineering_agent")
        self.assertEqual(
            diagnosis["recommended_assignment"]["execution_contract"]["mode"],
            "offline_code_or_data_infra",
        )
        self.assertIn(
            "control_live_mcp_without_explicit_ownership",
            diagnosis["recommended_assignment"]["execution_contract"]["forbidden_operations"],
        )
        self.assertIn("Agent: engineering_agent", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("screen_stall", diagnosis["recommended_assignment"]["assignment_prompt"])
        self.assertIn("screen_stall=BOSS_REWARDx3[F17,steps=10->12,relics=3,action=choose:1]", diagnosis["status_line"])
        self.assertIn("advice=not_configured", diagnosis["status_line"])

    def test_diagnose_manifest_runs_returns_every_manifest_item(self):
        with TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "clean.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "combat_planning",
                            }
                        ],
                        "diagnostic_excluded": [
                            {
                                "path": "diagnostic.jsonl",
                                "reason": "no_terminal_outcome",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 17,
                                "recovered_actions": 1,
                                "failed_actions": 0,
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": True,
                                        "enemy_ids": ["TheGuardian"],
                                        "clear_step": 255,
                                        "prefix_pristine_clear": True,
                                    }
                                },
                            }
                        ],
                        "infra_blocked": [
                            {
                                "path": "infra.jsonl",
                                "reason": "mcp_null_state",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 4,
                                "recovered_actions": 0,
                                "failed_actions": 1,
                                "failure_evidence": {
                                    "reason": "mcp_null_state",
                                    "mcp_read": {
                                        "step": 88,
                                        "event": "synthetic_terminal_state",
                                        "diagnostics_status": "game_over",
                                        "last_state": {
                                            "screen_type": "NONE",
                                            "room_phase": "COMBAT",
                                            "floor": 4,
                                        },
                                    },
                                    "synthetic_terminal": {
                                        "step": 88,
                                        "terminal_recovery_attempted": True,
                                        "terminal_recovery_succeeded": False,
                                        "post_recovery_status": "state_broken",
                                    },
                                    "terminal_outcome_source": "synthetic_after_mcp_null",
                                },
                            }
                        ],
                    }
                },
            )

            primary = diagnose_manifest(manifest)
            diagnoses = diagnose_manifest_runs(manifest)

        self.assertEqual(primary["path"], "infra.jsonl")
        self.assertEqual([diagnosis["path"] for diagnosis in diagnoses], ["infra.jsonl", "diagnostic.jsonl", "clean.jsonl"])
        self.assertEqual([diagnosis["category"] for diagnosis in diagnoses], ["infra_blocked", "diagnostic_excluded", "clean_trainable"])
        self.assertEqual(
            [diagnosis["next_action"] for diagnosis in diagnoses],
            ["fix_execution_layer", "preserve_boss_validation_evidence", "inspect_combat_planning"],
        )
        self.assertIn("mcp_read=step88[status=game_over,NONE/F4/COMBAT]", diagnoses[0]["status_line"])
        self.assertIn(
            "synthetic_terminal=step88[outcome=synthetic_after_mcp_null,recover=failed,post=state_broken]",
            diagnoses[0]["status_line"],
        )

    def test_diagnose_manifest_runs_filters_shadow_advice_by_source_log(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            advice = root / "advice"
            advice.mkdir()
            write_jsonl(
                advice / "route_risk_advice.jsonl",
                [
                    {
                        "source_log": "run_a.jsonl",
                        "advice": "avoid_or_require_recovery",
                        "model_available": True,
                        "route_risk_score": 0.91,
                        "shadow_source_file": "shadow/route_risk.jsonl",
                        "shadow_source_mode": "shadow_rows",
                    },
                    {
                        "source_log": "run_b.jsonl",
                        "advice": "route_ok",
                        "model_available": True,
                        "route_risk_score": 0.12,
                        "shadow_source_file": "shadow/route_risk.jsonl",
                        "shadow_source_mode": "shadow_rows",
                    },
                ],
            )
            write_json(
                advice / "summary.json",
                {
                    "version": 1,
                    "shadow_inputs": {
                        "mode": "shadow_rows",
                        "resolved_files": ["shadow/route_risk.jsonl"],
                        "resolved_file_count": 1,
                        "categories": {
                            "route_risk": {
                                "row_count": 2,
                                "resolved_files": ["shadow/route_risk.jsonl"],
                                "resolved_file_count": 1,
                                "warnings": [],
                            }
                        },
                        "warnings": [],
                    },
                },
            )
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "shadow_advice": {"path": str(advice)},
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run_a.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 3,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "unknown_clean_failure",
                            },
                            {
                                "path": "run_b.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 3,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "unknown_clean_failure",
                            },
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    },
                },
            )

            aggregate = diagnose_manifest(manifest)
            diagnoses = diagnose_manifest_runs(manifest)

        self.assertEqual(aggregate["shadow_advice"]["categories"]["route_risk"]["rows"], 2)
        self.assertEqual(aggregate["shadow_advice"]["categories"]["route_risk"]["high_risk_count"], 1)
        self.assertEqual(aggregate["shadow_advice"]["shadow_inputs"]["resolved_files"], ["shadow/route_risk.jsonl"])
        self.assertEqual([diagnosis["path"] for diagnosis in diagnoses], ["run_a.jsonl", "run_b.jsonl"])
        self.assertEqual(diagnoses[0]["shadow_advice"]["categories"]["route_risk"]["rows"], 1)
        self.assertEqual(diagnoses[0]["shadow_advice"]["categories"]["route_risk"]["high_risk_count"], 1)
        self.assertEqual(
            diagnoses[0]["shadow_advice"]["categories"]["route_risk"]["shadow_source_files"],
            ["shadow/route_risk.jsonl"],
        )
        self.assertEqual(diagnoses[0]["next_action"], "inspect_shadow_route_risk")
        self.assertEqual(diagnoses[1]["shadow_advice"]["categories"]["route_risk"]["rows"], 1)
        self.assertEqual(diagnoses[1]["shadow_advice"]["categories"]["route_risk"]["high_risk_count"], 0)
        self.assertEqual(diagnoses[1]["next_action"], "inspect_clean_failure")

    def test_missing_advice_directory_is_explicit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = summarize_advice(root / "missing")

        self.assertFalse(summary["available"])
        self.assertEqual(summary["reason"], "not_found")

    def test_missed_lethal_advice_counts_as_combat_search_high_risk(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            advice = root / "advice"
            advice.mkdir()
            write_jsonl(advice / "route_risk_advice.jsonl", [])
            write_jsonl(advice / "potion_tempo_advice.jsonl", [])
            write_jsonl(advice / "pre_boss_deck_quality_advice.jsonl", [])
            write_jsonl(
                advice / "combat_search_advice.jsonl",
                [
                    {
                        "advice": "review_missed_lethal",
                        "model_available": True,
                        "combat_search_score": 1.2,
                        "floor": 16,
                    },
                    {
                        "advice": "review_missed_single_card_search",
                        "model_available": True,
                        "combat_search_score": 1.1,
                        "floor": 16,
                    }
                ],
            )

            summary = summarize_advice(advice)

        combat = summary["categories"]["combat_search"]
        self.assertEqual(combat["high_risk_count"], 2)
        self.assertEqual(combat["watch_count"], 0)

    def test_boss_endgame_missed_lethal_overrides_next_action(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 10,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 7,
                            "max_hp": 80,
                            "combat": {
                                "turn": 15,
                                "incoming_damage": 12,
                                "player": {"current_hp": 7, "max_hp": 80, "current_energy": 1, "block": 0},
                                "hand_cards": [
                                    {
                                        "name": "Twin Strike",
                                        "id": "Twin Strike",
                                        "type": "ATTACK",
                                        "cost": 1,
                                        "damage": 10,
                                        "is_playable": True,
                                        "has_target": True,
                                    },
                                    {
                                        "name": "Defend",
                                        "id": "Defend_R",
                                        "type": "SKILL",
                                        "cost": 1,
                                        "block": 5,
                                        "is_playable": True,
                                    },
                                ],
                                "monsters": [{"id": "Hexaghost", "hp": 4, "max_hp": 250}],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 2}]},
                    },
                    {
                        "step": 11,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 0,
                            "max_hp": 80,
                            "combat": {
                                "turn": 15,
                                "incoming_damage": 12,
                                "player": {"current_hp": 0, "max_hp": 80, "current_energy": 0, "block": 0},
                                "hand_cards": [],
                                "monsters": [{"id": "Hexaghost", "hp": 4, "max_hp": 250}],
                            },
                        },
                    },
                    {
                        "step": 12,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "room_phase": "COMPLETE",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 0,
                            "max_hp": 80,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "potion_planning",
                                "validation_grade": "pristine",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": False,
                                        "enemy_ids": ["Hexaghost"],
                                        "last_turn": 15,
                                        "last_hp": 4,
                                        "potion_use_steps": [],
                                    }
                                },
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["next_action"], "fix_combat_lethal_priority")
        self.assertTrue(diagnosis["combat_endgame"]["missed_lethal"])
        self.assertEqual(diagnosis["combat_endgame"]["kill_cards"][0]["name"], "Twin Strike")
        self.assertIn("missed_lethal=Twin Strike->Hexaghost hp=4", diagnosis["status_line"])

    def test_boss_endgame_missed_single_card_search_overrides_next_action(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 20,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 12,
                            "max_hp": 80,
                            "combat": {
                                "turn": 9,
                                "incoming_damage": 12,
                                "player": {"current_hp": 12, "max_hp": 80, "current_energy": 1, "block": 0},
                                "hand_cards": [
                                    {
                                        "name": "Strike",
                                        "id": "Strike_R",
                                        "type": "ATTACK",
                                        "cost": 1,
                                        "damage": 6,
                                        "is_playable": True,
                                        "has_target": True,
                                    },
                                    {
                                        "name": "Defend",
                                        "id": "Defend_R",
                                        "type": "SKILL",
                                        "cost": 1,
                                        "block": 5,
                                        "is_playable": True,
                                    },
                                ],
                                "monsters": [
                                    {
                                        "id": "TheGuardian",
                                        "name": "The Guardian",
                                        "hp": 120,
                                        "max_hp": 240,
                                        "move": {"damage": 12},
                                        "powers": [{"id": "ModeShift", "amount": 6}],
                                    }
                                ],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 2}]},
                    },
                    {
                        "step": 21,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "room_phase": "COMPLETE",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 0,
                            "max_hp": 80,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "combat_planning",
                                "validation_grade": "pristine",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": False,
                                        "enemy_ids": ["TheGuardian"],
                                        "last_turn": 9,
                                        "last_hp": 120,
                                        "potion_use_steps": [],
                                    }
                                },
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["next_action"], "fix_combat_search_priority")
        self.assertFalse(diagnosis["combat_endgame"]["missed_lethal"])
        self.assertTrue(diagnosis["combat_endgame"]["missed_single_card_search"])
        search = diagnosis["combat_endgame"]["single_card_search"]
        self.assertEqual(search["first_card_key"], "Strike_R")
        self.assertEqual(search["initial_loss"], 12)
        self.assertEqual(search["projected_loss"], 0)
        self.assertEqual(search["attacks_removed"], 12)
        self.assertIn("missed_search=Strike->TheGuardian loss=12->0 attacks_removed=12", diagnosis["status_line"])

    def test_boss_endgame_missed_retaliation_search_overrides_next_action(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 20,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 12,
                            "max_hp": 80,
                            "combat": {
                                "turn": 9,
                                "incoming_damage": 12,
                                "player": {"current_hp": 12, "max_hp": 80, "current_energy": 2, "block": 0},
                                "hand_cards": [
                                    {
                                        "name": "Strike",
                                        "id": "Strike_R",
                                        "type": "ATTACK",
                                        "cost": 1,
                                        "damage": 6,
                                        "is_playable": True,
                                        "has_target": True,
                                    },
                                    {
                                        "name": "Flame Barrier",
                                        "id": "Flame Barrier",
                                        "type": "SKILL",
                                        "cost": 2,
                                        "block": 12,
                                        "is_playable": True,
                                    },
                                ],
                                "monsters": [
                                    {
                                        "id": "Hexaghost",
                                        "name": "Hexaghost",
                                        "hp": 250,
                                        "max_hp": 250,
                                        "move": {"hits": 6, "damage": 2},
                                    }
                                ],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 1, "target_index": 1}]},
                    },
                    {
                        "step": 21,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "room_phase": "COMPLETE",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 0,
                            "max_hp": 80,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [
                            {
                                "path": "run.jsonl",
                                "reason": "completed_clean",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "victory": False,
                                "recovered_actions": 0,
                                "failed_actions": 0,
                                "failure_attribution": "combat_planning",
                                "validation_grade": "pristine",
                                "validation_evidence": {
                                    "act1_boss": {
                                        "reached": True,
                                        "cleared": False,
                                        "enemy_ids": ["Hexaghost"],
                                        "last_turn": 9,
                                        "last_hp": 250,
                                        "potion_use_steps": [],
                                    }
                                },
                            }
                        ],
                        "diagnostic_excluded": [],
                        "infra_blocked": [],
                    }
                },
            )

            diagnosis = diagnose_manifest(manifest)

        self.assertEqual(diagnosis["next_action"], "fix_combat_search_priority")
        self.assertTrue(diagnosis["combat_endgame"]["missed_single_card_search"])
        search = diagnosis["combat_endgame"]["single_card_search"]
        self.assertEqual(search["first_card_key"], "Flame Barrier")
        self.assertEqual(search["initial_loss"], 12)
        self.assertEqual(search["projected_loss"], 0)
        self.assertEqual(search["attacks_removed"], 0)
        self.assertEqual(search["retaliation_damage"], 24)
        self.assertIn("missed_search=Flame Barrier->Hexaghost loss=12->0 attacks_removed=0 retaliation=24", diagnosis["status_line"])

    def test_cli_writes_assignment_prompt_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            output = root / "diagnosis.json"
            assignment_output = root / "assignment.txt"
            write_json(
                manifest,
                {
                    "categories": {
                        "clean_trainable": [],
                        "diagnostic_excluded": [],
                        "infra_blocked": [
                            {
                                "path": "infra.jsonl",
                                "reason": "mcp_unreachable",
                                "character": "IRONCLAD",
                                "ascension": 0,
                                "floor": 16,
                                "recovered_actions": 0,
                                "failed_actions": 1,
                                "failure_attribution": "mcp_execution",
                            }
                        ],
                    }
                },
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        str(manifest),
                        "--output",
                        str(output),
                        "--assignment-output",
                        str(assignment_output),
                    ]
                )
            saved = json.loads(output.read_text(encoding="utf-8"))
            assignment_text = assignment_output.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertIn("Wrote run diagnosis assignment:", stdout.getvalue())
        self.assertEqual(saved["diagnoses"][0]["recommended_assignment"]["owner"], "engineering_agent")
        self.assertEqual(
            assignment_text,
            saved["diagnoses"][0]["recommended_assignment"]["assignment_prompt"].rstrip() + "\n",
        )
        self.assertIn("Run diagnosis assignment", assignment_text)
        self.assertIn("Agent: engineering_agent", assignment_text)
        self.assertIn("Action: fix_execution_layer", assignment_text)
        self.assertIn("Execution mode: offline_code_or_data_infra", assignment_text)
        self.assertIn("Source log: infra.jsonl", assignment_text)


if __name__ == "__main__":
    unittest.main()
