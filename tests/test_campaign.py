import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from slay_ai.campaign import Target, _act1_boss_gate_progress_fields, run_target_attempts
from slay_ai.runner import EpisodeResult


class CampaignTests(unittest.TestCase):
    def test_campaign_forwards_manifest_advice_options_and_records_progress(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(
                endpoint="http://127.0.0.1:8080/mcp",
                existing_save="fail",
                max_steps=44,
                interval=0.08,
                startup_timeout=20.0,
                log_dir=root / "runs",
                write_manifest=True,
                no_manifest=False,
                manifest_dir=root / "manifests",
                manifest_knowledge_dir=root / "knowledge",
                manifest_shadow_dir=root / "shadow",
                manifest_advice_dir=root / "advice",
                route_risk_model_path=root / "route_model.json",
                potion_tempo_model_path=root / "potion_model.json",
                deck_quality_model_path=root / "deck_model.json",
                combat_search_model_path=root / "combat_model.json",
                act1_boss_gate_output=root / "act1_boss_gate.json",
                act1_boss_gate_min_reached=1,
                act1_boss_gate_min_cleared=0,
                act1_boss_gate_min_pristine_cleared=0,
                progress_file=root / "progress.json",
                attempts_per_target=1,
                cooldown=0.0,
                dry_run=False,
            )
            result = EpisodeResult(
                status="game_over",
                log_path=root / "runs" / "run.jsonl",
                steps=12,
                victory=False,
                floor=16,
                score=321,
                manifest_path=root / "manifests" / "run.manifest.json",
                manifest_category="clean_trainable",
                manifest_reason="completed_clean",
                shadow_advice_path=root / "advice" / "run",
            )
            result.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            result.manifest_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "shadow_feature_coverage": {
                                "total_rows": 2,
                                "categories": {
                                    "route_risk": {
                                        "rows": 2,
                                        "source_quality": {"usable_with_recoveries": 2},
                                        "feature_prefixes": {
                                            "deck_": {"field_count": 0, "rows_with_nonzero": 0},
                                            "potion_": {"field_count": 2, "rows_with_nonzero": 1},
                                        },
                                    }
                                },
                            },
                            "shadow_label_quality": {
                                "combat_search_labels": {
                                    "total": 3,
                                    "trainable": 2,
                                    "excluded_from_training": 1,
                                    "direct_kill_available": 1,
                                    "missed_single_card_search": 1,
                                    "exclusion_reasons": {"missed_direct_kill": 1},
                                }
                            },
                        },
                        "categories": {
                            "clean_trainable": [
                                {
                                    "character": "IRONCLAD",
                                    "ascension": 0,
                                    "floor": 16,
                                    "victory": False,
                                    "failure_attribution": "combat_planning",
                                    "validation_grade": "usable_with_recoveries",
                                    "validation_flags": ["recovered_action_race"],
                                    "action_recovery_summary": {
                                        "total": 1,
                                        "recovered": 1,
                                        "unrecovered": 0,
                                        "runs_with_action_recovery": 1,
                                        "runs_with_recovered_action": 1,
                                        "runs_with_unrecovered_action": 0,
                                        "by_status": {"preflight_mismatch": 1},
                                        "by_kind": {"stale_target_index": 1},
                                    },
                                    "failure_evidence": {
                                        "synthetic_terminal": {
                                            "terminal_recovery_attempted": True,
                                            "terminal_recovery_succeeded": True,
                                        }
                                    },
                                    "validation_evidence": {
                                        "act1_boss": {
                                            "reached": True,
                                            "cleared": False,
                                            "enemy_ids": ["TheGuardian"],
                                            "entry_step": 212,
                                            "entry_hp": 73,
                                            "entry_potion_count": 2,
                                            "clear_step": None,
                                            "prefix_pristine_clear": False,
                                            "prefix_blockers": ["not_cleared"],
                                            "last_turn": 9,
                                            "last_hp": 0,
                                            "potion_use_steps": [228, 230],
                                        }
                                    },
                                }
                            ],
                            "diagnostic_excluded": [],
                            "infra_blocked": [],
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            progress = {"version": 1, "targets": {}}
            client = object()
            memory = object()

            with (
                patch("slay_ai.campaign.run_episode", return_value=result) as run_episode,
                patch("slay_ai.campaign.return_to_menu") as return_to_menu,
                patch("slay_ai.campaign.time.sleep", return_value=None),
            ):
                run_target_attempts(args, Target("IRONCLAD", 0), progress, client, memory)

            call = run_episode.call_args.kwargs
            item = progress["targets"]["IRONCLAD:A0"]
            saved = json.loads(args.progress_file.read_text(encoding="utf-8"))
            gate = json.loads(args.act1_boss_gate_output.read_text(encoding="utf-8"))

        self.assertEqual(call["manifest_dir"], args.manifest_dir)
        self.assertEqual(call["manifest_knowledge_dir"], args.manifest_knowledge_dir)
        self.assertEqual(call["manifest_shadow_dir"], args.manifest_shadow_dir)
        self.assertEqual(call["manifest_advice_dir"], args.manifest_advice_dir)
        self.assertEqual(call["route_risk_model_path"], args.route_risk_model_path)
        self.assertEqual(call["potion_tempo_model_path"], args.potion_tempo_model_path)
        self.assertEqual(call["deck_quality_model_path"], args.deck_quality_model_path)
        self.assertEqual(call["combat_search_model_path"], args.combat_search_model_path)
        self.assertTrue(call["write_manifest"])
        self.assertEqual(item["last_shadow_advice"], str(result.shadow_advice_path))
        self.assertEqual(item["last_manifest_category"], "clean_trainable")
        self.assertEqual(item["manifest_history"], [str(result.manifest_path)])
        self.assertEqual(item["last_validation_grade"], "usable_with_recoveries")
        self.assertEqual(item["last_validation_flags"], ["recovered_action_race"])
        self.assertTrue(item["last_act1_boss_reached"])
        self.assertFalse(item["last_act1_boss_cleared"])
        self.assertEqual(item["last_act1_boss_enemy_ids"], ["TheGuardian"])
        self.assertEqual(item["last_act1_boss_entry_step"], 212)
        self.assertEqual(item["last_act1_boss_entry_hp"], 73)
        self.assertEqual(item["last_act1_boss_entry_potion_count"], 2)
        self.assertIsNone(item["last_act1_boss_clear_step"])
        self.assertFalse(item["last_act1_boss_prefix_pristine_clear"])
        self.assertEqual(item["last_act1_boss_prefix_blockers"], ["not_cleared"])
        self.assertEqual(item["last_act1_boss_last_turn"], 9)
        self.assertEqual(item["last_act1_boss_last_hp"], 0)
        self.assertEqual(item["last_act1_boss_potion_use_steps"], [228, 230])
        self.assertEqual(item["last_act1_boss_gate_report"], str(args.act1_boss_gate_output))
        self.assertTrue(item["last_act1_boss_gate_passed"])
        self.assertEqual(item["last_act1_boss_gate_counts"]["act1_boss_reached"], 1)
        self.assertEqual(item["last_act1_boss_gate_deficits"]["act1_boss_reached"], 0)
        self.assertEqual(item["last_act1_boss_gate_deficits"]["pristine_act1_boss_cleared"], 0)
        self.assertEqual(item["last_act1_boss_gate_progress"]["act1_boss_reached"], {
            "current": 1,
            "required": 1,
            "deficit": 0,
            "ok": True,
        })
        self.assertEqual(item["last_act1_boss_gate_progress"]["pristine_act1_boss_cleared"], {
            "current": 0,
            "required": 0,
            "deficit": 0,
            "ok": True,
        })
        self.assertEqual(item["last_act1_boss_gate_remaining_progress"], [])
        self.assertIsNone(item["last_act1_boss_gate_primary_remaining_progress"])
        self.assertEqual(item["last_act1_boss_gate_next_probe_goal"]["summary"], (
            "gate passed; promote to the next validation batch"
        ))
        self.assertEqual(item["last_act1_boss_gate_blocking_reasons"], [])
        self.assertIsNone(item["last_act1_boss_gate_primary_blocking_reason"])
        self.assertEqual(item["last_act1_boss_gate_focus"]["top_failure_attribution"], {
            "attribution": "combat_planning",
            "count": 1,
        })
        self.assertEqual(item["last_act1_boss_gate_focus"]["top_uncleared_boss"], {
            "enemy": "TheGuardian",
            "total": 1,
            "top_failure_attribution": "combat_planning",
            "top_failure_attribution_count": 1,
        })
        self.assertEqual(
            item["last_act1_boss_gate_execution_recovery"],
            {
                "action_recovery": {
                    "total": 1,
                    "recovered": 1,
                    "unrecovered": 0,
                    "runs_with_action_recovery": 1,
                    "runs_with_recovered_action": 1,
                    "runs_with_unrecovered_action": 0,
                    "by_status": {"preflight_mismatch": 1},
                    "by_kind": {"stale_target_index": 1},
                },
                "terminal_recovery": {
                    "attempted": 1,
                    "succeeded": 1,
                    "synthetic_terminals": 1,
                },
                "manifests": [
                    {
                        "manifest_path": str(result.manifest_path),
                        "action_recovery_source": "category_fallback",
                        "action_recovery_total": 1,
                        "terminal_recovery_source": "category_fallback",
                        "terminal_recovery_synthetic_terminals": 1,
                    }
                ],
            },
        )
        self.assertEqual(
            item["last_act1_boss_gate_data_quality"],
            {
                "total_manifests": 1,
                "coverage_manifests": 1,
                "missing_coverage_manifests": 0,
                "shadow_feature_rows": 2,
                "feature_gap_manifests": 1,
                "feature_zero_manifests": 0,
                "feature_gaps": {"route_risk:deck_": 1},
                "feature_zero": {},
                "shadow_label_rows": 3,
                "shadow_label_trainable": 2,
                "shadow_label_excluded": 1,
                "label_exclusion_manifests": 1,
                "label_exclusion_reasons": {"missed_direct_kill": 1},
                "direct_kill_available_labels": 1,
                "missed_single_card_search_labels": 1,
            },
        )
        self.assertEqual(item["last_act1_boss_gate_next_action"], "promote_to_next_validation_batch")
        self.assertTrue(gate["gate"]["passed"])
        self.assertEqual(gate["execution_recovery"], item["last_act1_boss_gate_execution_recovery"])
        self.assertEqual(item["last_act1_boss_gate_data_quality"]["shadow_label_excluded"], 1)
        self.assertEqual(gate["gate"]["deficits"]["pristine_act1_boss_cleared"], 0)
        self.assertEqual(saved["targets"]["IRONCLAD:A0"]["last_shadow_advice"], str(result.shadow_advice_path))
        self.assertEqual(saved["targets"]["IRONCLAD:A0"]["last_validation_grade"], "usable_with_recoveries")
        self.assertTrue(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_reached"])
        self.assertFalse(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_prefix_pristine_clear"])
        self.assertTrue(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_passed"])
        self.assertEqual(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_deficits"]["act1_boss_cleared"], 0)
        self.assertEqual(
            saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_progress"]["act1_boss_reached"]["current"],
            1,
        )
        self.assertEqual(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_remaining_progress"], [])
        self.assertIsNone(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_primary_remaining_progress"])
        self.assertEqual(
            saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_next_probe_goal"]["action"],
            "promote_to_next_validation_batch",
        )
        self.assertEqual(saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_blocking_reasons"], [])
        self.assertEqual(
            saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_focus"]["top_uncleared_boss"]["enemy"],
            "TheGuardian",
        )
        self.assertEqual(
            saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_execution_recovery"],
            item["last_act1_boss_gate_execution_recovery"],
        )
        self.assertEqual(
            saved["targets"]["IRONCLAD:A0"]["last_act1_boss_gate_data_quality"],
            item["last_act1_boss_gate_data_quality"],
        )
        return_to_menu.assert_called_once_with(client)

    def test_campaign_gate_progress_exposes_next_probe_acceptance_criteria(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "categories": {
                            "clean_trainable": [
                                {
                                    "path": "runs/probe/run.jsonl",
                                    "character": "IRONCLAD",
                                    "ascension": 0,
                                    "floor": 21,
                                    "victory": False,
                                    "recovered_actions": 1,
                                    "failed_actions": 0,
                                    "failure_attribution": "route_risk",
                                    "validation_grade": "usable_with_recoveries",
                                    "validation_flags": ["recovered_action_race"],
                                    "validation_evidence": {
                                        "act1_boss": {
                                            "reached": True,
                                            "cleared": True,
                                            "enemy_ids": ["TheGuardian"],
                                            "prefix_pristine_clear": False,
                                            "prefix_blockers": ["recovered_action_race"],
                                        }
                                    },
                                }
                            ],
                            "diagnostic_excluded": [],
                            "infra_blocked": [],
                        }
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                act1_boss_gate_output=None,
                act1_boss_gate_min_reached=1,
                act1_boss_gate_min_cleared=1,
                act1_boss_gate_min_pristine_cleared=1,
            )

            fields = _act1_boss_gate_progress_fields(
                args,
                Target("IRONCLAD", 0),
                {"manifest_history": [str(manifest)]},
            )

        criteria = fields["last_act1_boss_gate_next_probe_acceptance_criteria"]
        self.assertFalse(fields["last_act1_boss_gate_passed"])
        self.assertEqual(fields["last_act1_boss_gate_next_action"], "collect_pristine_act1_boss_clears")
        self.assertEqual(
            fields["last_act1_boss_gate_next_probe_goal"]["acceptance_criteria"],
            criteria,
        )
        self.assertEqual(criteria["counts_toward_metric"], "pristine_act1_boss_cleared")
        self.assertEqual(criteria["act1_boss"], {
            "reached": True,
            "cleared": True,
            "prefix_pristine_clear": True,
            "prefix_blockers": [],
        })
        self.assertIn("recovered_action_race", criteria["disallowed_prefix_blockers"])
        self.assertEqual(fields["last_act1_boss_gate_deficits"]["pristine_act1_boss_cleared"], 1)
        latest = fields["last_act1_boss_gate_latest_run_acceptance"]
        self.assertFalse(latest["accepted"])
        self.assertEqual(latest["metric"], "pristine_act1_boss_cleared")
        self.assertEqual(latest["failure_attribution"], "route_risk")
        self.assertIn("prefix_pristine_clear_false", latest["blockers"])
        self.assertIn("prefix:recovered_action_race", latest["blockers"])


if __name__ == "__main__":
    unittest.main()
