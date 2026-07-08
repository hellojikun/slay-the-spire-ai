import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.act1_boss_gate import build_report, main, status_line


def write_manifest(path: Path, categories: dict[str, list[dict]], *, summary: dict | None = None) -> None:
    payload = {
        "version": 1,
        "summary": summary or {},
        "categories": {
            "clean_trainable": categories.get("clean_trainable", []),
            "diagnostic_excluded": categories.get("diagnostic_excluded", []),
            "infra_blocked": categories.get("infra_blocked", []),
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def item(
    *,
    category_reason: str = "completed_clean",
    grade: str = "pristine",
    reached: bool = True,
    cleared: bool = True,
    attribution: str = "combat_planning",
    victory: bool | None = False,
    floor: int = 18,
    recovered: int = 0,
    flags: list[str] | None = None,
    enemy_ids: list[str] | None = None,
    prefix_pristine_clear: bool | None = None,
    prefix_blockers: list[str] | None = None,
) -> dict:
    boss = {
        "reached": reached,
        "cleared": cleared,
        "floor": 16,
        "enemy_ids": enemy_ids if enemy_ids is not None else ["TheGuardian"],
        "entry_step": 200,
        "entry_hp": 72,
        "entry_potion_count": 2,
        "clear_step": 221 if cleared else None,
        "last_turn": 8,
        "last_hp": 0 if cleared else 18,
        "potion_use_steps": [221],
    }
    if prefix_pristine_clear is not None:
        boss["prefix_pristine_clear"] = prefix_pristine_clear
    if prefix_blockers is not None:
        boss["prefix_blockers"] = prefix_blockers
    return {
        "path": "runs/probe/run.jsonl",
        "category": "clean_trainable",
        "reason": category_reason,
        "character": "IRONCLAD",
        "ascension": 0,
        "floor": floor,
        "victory": victory,
        "steps": 250,
        "recovered_actions": recovered,
        "failed_actions": 0,
        "failure_attribution": attribution,
        "failure_tags": [attribution] if attribution else [],
        "validation_grade": grade,
        "validation_flags": flags if flags is not None else (["recovered_action_race"] if recovered else []),
        "validation_evidence": {"act1_boss": boss},
    }


class Act1BossGateTests(unittest.TestCase):
    def test_report_passes_with_multiple_pristine_clears_and_known_attribution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [
                        item(attribution="route_risk"),
                        item(attribution="potion_planning"),
                        item(grade="usable_with_recoveries", attribution="combat_planning", recovered=1),
                    ]
                },
            )

            report = build_report([manifest], min_reached=3, min_cleared=3, min_pristine_cleared=2)

        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["counts"]["act1_boss_reached"], 3)
        self.assertEqual(report["counts"]["act1_boss_cleared"], 3)
        self.assertEqual(report["counts"]["pristine_act1_boss_cleared"], 2)
        self.assertEqual(report["counts"]["prefix_pristine_act1_boss_cleared"], 0)
        self.assertEqual(report["counts"]["legacy_pristine_act1_boss_cleared"], 2)
        self.assertEqual(report["counts"]["prefix_pristine_act1_boss_cleared_by_enemy"], {})
        self.assertEqual(report["counts"]["legacy_pristine_act1_boss_cleared_by_enemy"], {"TheGuardian": 2})
        self.assertEqual(report["counts"]["explicit_prefix_evidence_runs"], 0)
        self.assertEqual(report["counts"]["usable_act1_boss_cleared"], 1)
        self.assertEqual(report["gate"]["blocking_reasons"], [])
        self.assertIsNone(report["gate"]["primary_blocking_reason"])
        self.assertEqual(report["gate"]["deficits"]["pristine_act1_boss_cleared"], 0)
        self.assertEqual(report["gate"]["progress"], {
            "act1_boss_reached": {"current": 3, "required": 3, "deficit": 0, "ok": True},
            "act1_boss_cleared": {"current": 3, "required": 3, "deficit": 0, "ok": True},
            "pristine_act1_boss_cleared": {
                "current": 2,
                "required": 2,
                "deficit": 0,
                "ok": True,
            },
        })
        self.assertEqual(report["gate"]["remaining_progress"], [])
        self.assertIsNone(report["gate"]["primary_remaining_progress"])
        goal = report["gate"]["next_probe_goal"]
        self.assertEqual(goal["action"], "promote_to_next_validation_batch")
        self.assertIsNone(goal["primary_blocking_reason"])
        self.assertIsNone(goal["metric"])
        self.assertEqual(goal["deficit"], 0)
        self.assertEqual(goal["summary"], "gate passed; promote to the next validation batch")
        self.assertEqual(goal["acceptance_criteria"], {
            "scope": "gate_already_passed",
            "counts_toward_metric": None,
            "no_next_validation_required": True,
        })
        self.assertEqual(report["gate"]["next_action"], "promote_to_next_validation_batch")
        self.assertIn("act1_boss_gate PASS", status_line(report))
        self.assertIn("legacy_pristine=2", status_line(report))
        self.assertNotIn("need_pristine", status_line(report))

    def test_report_requires_pristine_clears_even_when_diagnostic_clear_exists(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            diagnostic = item(grade="diagnostic", attribution="diagnostic_incomplete", victory=None, recovered=1)
            diagnostic["reason"] = "no_terminal_outcome"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [item(attribution="deck_quality")],
                    "diagnostic_excluded": [diagnostic],
                },
            )

            report = build_report([manifest], min_reached=2, min_cleared=2, min_pristine_cleared=2)

        self.assertFalse(report["gate"]["passed"])
        self.assertEqual(report["counts"]["act1_boss_cleared"], 2)
        self.assertEqual(report["counts"]["diagnostic_act1_boss_cleared"], 1)
        self.assertEqual(report["counts"]["pristine_act1_boss_cleared"], 1)
        self.assertEqual(report["counts"]["non_pristine_clear_blockers"]["grade:diagnostic"], 1)
        self.assertEqual(report["counts"]["non_pristine_clear_blockers"]["flag:recovered_action_race"], 1)
        self.assertEqual(report["counts"]["non_pristine_clear_blockers"]["recovered_actions"], 1)
        self.assertEqual(report["gate"]["blocking_reasons"], ["insufficient_pristine_act1_boss_cleared"])
        self.assertEqual(report["gate"]["primary_blocking_reason"], "insufficient_pristine_act1_boss_cleared")
        self.assertEqual(report["gate"]["focus"]["top_non_pristine_clear_blocker"], {
            "blocker": "recovered_actions",
            "count": 1,
        })
        self.assertEqual(report["runs"][1]["non_pristine_clear_blockers"], [
            "flag:recovered_action_race",
            "grade:diagnostic",
            "recovered_actions",
        ])
        self.assertEqual(report["gate"]["deficits"]["pristine_act1_boss_cleared"], 1)
        self.assertEqual(report["gate"]["progress"]["pristine_act1_boss_cleared"], {
            "current": 1,
            "required": 2,
            "deficit": 1,
            "ok": False,
        })
        self.assertEqual(report["gate"]["remaining_progress"], [
            {
                "metric": "pristine_act1_boss_cleared",
                "current": 1,
                "required": 2,
                "deficit": 1,
                "ok": False,
            }
        ])
        self.assertEqual(report["gate"]["primary_remaining_progress"], {
            "metric": "pristine_act1_boss_cleared",
            "current": 1,
            "required": 2,
            "deficit": 1,
            "ok": False,
        })
        goal = report["gate"]["next_probe_goal"]
        self.assertEqual(goal["action"], "collect_pristine_act1_boss_clears")
        self.assertEqual(goal["primary_blocking_reason"], "insufficient_pristine_act1_boss_cleared")
        self.assertEqual(goal["metric"], "pristine_act1_boss_cleared")
        self.assertEqual(goal["deficit"], 1)
        self.assertEqual(goal["summary"], "collect 1 more pristine Act 1 boss clear")
        self.assertEqual(goal["acceptance_criteria"]["counts_toward_metric"], "pristine_act1_boss_cleared")
        self.assertEqual(goal["acceptance_criteria"]["act1_boss"], {
            "reached": True,
            "cleared": True,
            "prefix_pristine_clear": True,
            "prefix_blockers": [],
        })
        self.assertIn("recovered_action_race", goal["acceptance_criteria"]["disallowed_prefix_blockers"])
        self.assertIn("synthetic_after_mcp_null", goal["acceptance_criteria"]["disallowed_prefix_blockers"])
        self.assertEqual(
            goal["acceptance_criteria"]["accepted_manifest_categories"],
            ["clean_trainable", "diagnostic_excluded"],
        )
        self.assertEqual(goal["acceptance_criteria"]["rejected_manifest_categories"], ["infra_blocked"])
        self.assertEqual(report["gate"]["next_action"], "collect_pristine_act1_boss_clears")
        self.assertIn("top_non_pristine=", status_line(report))
        self.assertIn("need_pristine=1", status_line(report))

    def test_prefix_pristine_clear_counts_even_when_later_run_has_recoveries(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [
                        item(
                            grade="usable_with_recoveries",
                            attribution="route_risk",
                            recovered=3,
                            prefix_pristine_clear=True,
                        )
                    ]
                },
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["counts"]["act1_boss_cleared"], 1)
        self.assertEqual(report["counts"]["pristine_act1_boss_cleared"], 1)
        self.assertEqual(report["counts"]["prefix_pristine_act1_boss_cleared"], 1)
        self.assertEqual(report["counts"]["legacy_pristine_act1_boss_cleared"], 0)
        self.assertEqual(report["counts"]["prefix_pristine_act1_boss_cleared_by_enemy"], {"TheGuardian": 1})
        self.assertEqual(report["counts"]["legacy_pristine_act1_boss_cleared_by_enemy"], {})
        self.assertEqual(report["counts"]["explicit_prefix_evidence_runs"], 1)
        self.assertEqual(report["counts"]["non_pristine_clear_blockers"], {})
        self.assertEqual(report["gate"]["deficits"]["pristine_act1_boss_cleared"], 0)
        self.assertTrue(report["runs"][0]["act1_boss_prefix_pristine_clear"])
        self.assertTrue(report["runs"][0]["act1_boss"]["prefix_pristine_clear"])
        self.assertEqual(report["runs"][0]["act1_boss"]["entry_step"], 200)
        self.assertEqual(report["runs"][0]["act1_boss"]["clear_step"], 221)
        self.assertIn("prefix_pristine=1", status_line(report))
        self.assertIn("legacy_pristine=0", status_line(report))

    def test_latest_run_acceptance_counts_prefix_pristine_clear_for_remaining_deficit(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [
                        item(
                            grade="usable_with_recoveries",
                            attribution="route_risk",
                            recovered=3,
                            prefix_pristine_clear=True,
                        )
                    ]
                },
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=2)

        latest = report["gate"]["latest_run_acceptance"]
        self.assertFalse(report["gate"]["passed"])
        self.assertEqual(report["gate"]["next_action"], "collect_pristine_act1_boss_clears")
        self.assertTrue(latest["accepted"])
        self.assertEqual(latest["metric"], "pristine_act1_boss_cleared")
        self.assertEqual(latest["blockers"], [])
        self.assertTrue(latest["act1_boss"]["prefix_pristine_clear"])
        self.assertIn("latest_accept=accepted", status_line(report))

    def test_latest_run_acceptance_explains_failed_pristine_clear_candidate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [
                        item(prefix_pristine_clear=True, attribution="route_risk"),
                        item(
                            reached=True,
                            cleared=False,
                            attribution="combat_planning",
                            flags=["synthetic_terminal", "synthetic_after_mcp_null", "synthetic_terminal_state"],
                            prefix_pristine_clear=False,
                            prefix_blockers=[
                                "synthetic_terminal_state",
                                "synthetic_terminal",
                                "synthetic_after_mcp_null",
                            ],
                        ),
                    ]
                },
            )

            report = build_report([manifest], min_reached=2, min_cleared=1, min_pristine_cleared=2)

        latest = report["gate"]["latest_run_acceptance"]
        self.assertFalse(latest["accepted"])
        self.assertEqual(latest["metric"], "pristine_act1_boss_cleared")
        self.assertEqual(latest["failure_attribution"], "combat_planning")
        self.assertIn("act1_boss_not_cleared", latest["blockers"])
        self.assertIn("prefix:synthetic_terminal", latest["blockers"])
        self.assertIn("prefix:synthetic_after_mcp_null", latest["blockers"])
        self.assertIn("failure_attribution:combat_planning", latest["blockers"])
        self.assertFalse(latest["act1_boss"]["cleared"])
        line = status_line(report)
        self.assertIn("latest_reject=act1_boss_not_cleared", line)
        self.assertIn("failure_attribution:combat_planning", line)
        self.assertIn(",+2", line)

    def test_report_counts_act1_boss_results_by_enemy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [
                        item(enemy_ids=["TheGuardian"], prefix_pristine_clear=True),
                        item(enemy_ids=["Hexaghost"], grade="usable_with_recoveries", recovered=1),
                        item(enemy_ids=["SlimeBoss"], reached=True, cleared=False, attribution="combat_planning"),
                        item(enemy_ids=["TheGuardian"], reached=True, cleared=False, attribution="deck_quality"),
                        item(enemy_ids=["TheGuardian"], reached=True, cleared=False, attribution="combat_planning"),
                        item(enemy_ids=["TheGuardian"], reached=True, cleared=False, attribution="combat_planning"),
                    ]
                },
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(report["counts"]["act1_boss_reached_by_enemy"], {
            "Hexaghost": 1,
            "SlimeBoss": 1,
            "TheGuardian": 4,
        })
        self.assertEqual(report["counts"]["act1_boss_cleared_by_enemy"], {
            "Hexaghost": 1,
            "TheGuardian": 1,
        })
        self.assertEqual(report["counts"]["pristine_act1_boss_cleared_by_enemy"], {"TheGuardian": 1})
        self.assertEqual(report["counts"]["prefix_pristine_act1_boss_cleared_by_enemy"], {"TheGuardian": 1})
        self.assertEqual(report["counts"]["legacy_pristine_act1_boss_cleared_by_enemy"], {})
        self.assertEqual(report["counts"]["act1_boss_non_pristine_cleared_by_enemy"], {"Hexaghost": 1})
        self.assertEqual(report["counts"]["act1_boss_non_pristine_clear_blockers_by_enemy"], {
            "Hexaghost": {
                "flag:recovered_action_race": 1,
                "grade:usable_with_recoveries": 1,
                "recovered_actions": 1,
            }
        })
        self.assertEqual(report["counts"]["act1_boss_uncleared_by_enemy"], {
            "SlimeBoss": 1,
            "TheGuardian": 3,
        })
        self.assertEqual(report["counts"]["act1_boss_uncleared_failure_attributions_by_enemy"], {
            "SlimeBoss": {"combat_planning": 1},
            "TheGuardian": {"combat_planning": 2, "deck_quality": 1},
        })
        self.assertEqual(report["counts"]["act1_boss_outcomes_by_enemy"], {
            "Hexaghost": {
                "reached": 1,
                "cleared": 1,
                "pristine_cleared": 0,
                "prefix_pristine_cleared": 0,
                "legacy_pristine_cleared": 0,
                "non_pristine_cleared": 1,
                "uncleared": 0,
                "clear_rate": 1.0,
                "pristine_clear_rate": 0.0,
            },
            "SlimeBoss": {
                "reached": 1,
                "cleared": 0,
                "pristine_cleared": 0,
                "prefix_pristine_cleared": 0,
                "legacy_pristine_cleared": 0,
                "non_pristine_cleared": 0,
                "uncleared": 1,
                "clear_rate": 0.0,
                "pristine_clear_rate": 0.0,
            },
            "TheGuardian": {
                "reached": 4,
                "cleared": 1,
                "pristine_cleared": 1,
                "prefix_pristine_cleared": 1,
                "legacy_pristine_cleared": 0,
                "non_pristine_cleared": 0,
                "uncleared": 3,
                "clear_rate": 0.25,
                "pristine_clear_rate": 0.25,
            },
        })
        self.assertEqual(report["gate"]["focus"]["top_failure_attribution"], {
            "attribution": "combat_planning",
            "count": 5,
        })
        self.assertEqual(report["gate"]["focus"]["top_non_pristine_clear_boss"], {
            "enemy": "Hexaghost",
            "total": 1,
            "top_blocker": "recovered_actions",
            "top_blocker_count": 1,
        })
        self.assertEqual(report["gate"]["focus"]["top_uncleared_boss"], {
            "enemy": "TheGuardian",
            "total": 3,
            "top_failure_attribution": "combat_planning",
            "top_failure_attribution_count": 2,
        })
        self.assertIn("top_uncleared_boss=TheGuardian:3/combat_planning:2", status_line(report))
        self.assertIn("top_non_pristine_boss=Hexaghost:1/recovered_actions:1", status_line(report))

    def test_report_surfaces_manifest_shadow_feature_quality(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
                summary={
                    "shadow_feature_coverage": {
                        "total_rows": 4,
                        "categories": {
                            "route_risk": {
                                "rows": 2,
                                "source_quality": {"pristine": 2},
                                "unknown_static_features": {
                                    "deck_unknown_cards": {
                                        "present": 2,
                                        "nonzero": 1,
                                        "total": 2,
                                    }
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
                    },
                    "shadow_label_quality": {
                        "combat_search_labels": {
                            "total": 7,
                            "trainable": 4,
                            "excluded_from_training": 3,
                            "direct_kill_available": 2,
                            "missed_single_card_search": 1,
                            "exclusion_reasons": {
                                "missed_direct_kill": 2,
                                "missed_single_card_search": 1,
                            },
                        }
                    },
                },
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(report["data_quality"]["shadow_feature_rows"], 4)
        self.assertEqual(report["data_quality"]["feature_gap_manifests"], 1)
        self.assertEqual(report["data_quality"]["feature_zero_manifests"], 1)
        self.assertEqual(report["data_quality"]["feature_gaps"], {"route_risk:deck_": 1})
        self.assertEqual(report["data_quality"]["feature_zero"], {"potion_tempo:enemy_": 1})
        self.assertEqual(report["data_quality"]["feature_issue_categories"], {"potion_tempo": 1, "route_risk": 1})
        self.assertEqual(report["data_quality"]["unknown_static_manifests"], 1)
        self.assertEqual(report["data_quality"]["unknown_static_total"], 2)
        self.assertEqual(report["data_quality"]["unknown_static_features"], {"route_risk:deck_unknown_cards": 2})
        self.assertEqual(report["data_quality"]["shadow_label_rows"], 7)
        self.assertEqual(report["data_quality"]["shadow_label_trainable"], 4)
        self.assertEqual(report["data_quality"]["shadow_label_excluded"], 3)
        self.assertEqual(report["data_quality"]["label_exclusion_manifests"], 1)
        self.assertEqual(report["data_quality"]["direct_kill_available_labels"], 2)
        self.assertEqual(report["data_quality"]["missed_single_card_search_labels"], 1)
        self.assertEqual(
            report["data_quality"]["label_exclusion_reasons"],
            {"missed_direct_kill": 2, "missed_single_card_search": 1},
        )
        self.assertEqual(report["counts"]["shadow_feature_rows"], 4)
        self.assertEqual(report["counts"]["shadow_feature_gap_manifests"], 1)
        self.assertEqual(report["counts"]["shadow_unknown_static_total"], 2)
        self.assertEqual(report["counts"]["shadow_label_rows"], 7)
        self.assertEqual(report["counts"]["shadow_label_excluded"], 3)
        self.assertEqual(report["counts"]["shadow_label_exclusion_manifests"], 1)
        self.assertIn("feature_rows=4", status_line(report))
        self.assertIn("feature_gaps=route_risk:deck_:1", status_line(report))
        self.assertIn("feature_zero=potion_tempo:enemy_:1", status_line(report))
        self.assertIn("feature_unknown=route_risk:deck_unknown_cards:2", status_line(report))
        self.assertIn("labels=7", status_line(report))
        self.assertIn("label_exclusions=3/missed_direct_kill:2", status_line(report))

    def test_report_surfaces_stale_manifest_schema_quality(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale_manifest = root / "stale_manifest.json"
            current_manifest = root / "current_manifest.json"
            write_manifest(
                stale_manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
                summary={"total_logs": 1},
            )
            write_manifest(
                current_manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
                summary={
                    "input_count": 1,
                    "resolved_log_count": 1,
                    "warnings": [],
                    "total_logs": 1,
                    "clean_trainable": 1,
                    "diagnostic_excluded": 0,
                    "infra_blocked": 0,
                    "shadow_examples": {},
                    "shadow_feature_coverage": {},
                    "shadow_label_quality": {},
                    "classification_reasons": {},
                    "failure_attributions": {},
                    "failure_evidence": {},
                    "action_recovery": {},
                    "terminal_recovery": {},
                    "validation": {},
                },
            )

            report = build_report(
                [stale_manifest, current_manifest],
                min_reached=1,
                min_cleared=1,
                min_pristine_cleared=1,
            )

        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["data_quality"]["schema_complete_manifests"], 1)
        self.assertEqual(report["data_quality"]["schema_missing_manifests"], 1)
        self.assertGreater(report["data_quality"]["schema_missing_key_total"], 0)
        self.assertEqual(report["data_quality"]["schema_missing_summary_keys"]["action_recovery"], 1)
        self.assertEqual(report["data_quality"]["manifests"][0]["schema_complete"], False)
        self.assertIn("action_recovery", report["data_quality"]["manifests"][0]["missing_summary_keys"])
        self.assertEqual(report["counts"]["manifest_schema_missing_manifests"], 1)
        self.assertGreater(report["counts"]["manifest_schema_missing_key_total"], 0)
        self.assertIn("schema_missing=1", status_line(report))

    def test_report_surfaces_manifest_execution_recovery_summary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
                summary={
                    "action_recovery": {
                        "total": 2,
                        "recovered": 1,
                        "unrecovered": 1,
                        "runs_with_action_recovery": 2,
                        "runs_with_recovered_action": 1,
                        "runs_with_unrecovered_action": 1,
                        "by_status": {"recovered": 1, "failed": 1},
                        "by_kind": {"stale_target_index": 2},
                        "examples": [
                            {
                                "source_log": "run.jsonl",
                                "source_category": "clean_trainable",
                                "step": 218,
                                "action_status": "preflight_mismatch",
                                "recovered": True,
                                "kind": "unavailable_action",
                                "actions": [{"action": "choose", "choice_index": 1}],
                                "available_commands": ["proceed"],
                                "last_error": "Preflight unavailable action(s): ['choose']; available commands: ['proceed']",
                                "last_state": {
                                    "screen_type": "MAP",
                                    "room_phase": "COMPLETE",
                                    "floor": 15,
                                },
                            }
                        ],
                    },
                    "terminal_recovery": {
                        "synthetic_terminals": 2,
                        "attempted": 2,
                        "succeeded": 1,
                        "failed": 1,
                        "unhealthy": 1,
                    },
                    "failure_evidence": {
                        "runs_with_evidence": 2,
                        "by_type": {
                            "action_error": 1,
                            "mcp_read": 2,
                            "screen_stall": 1,
                            "synthetic_terminal": 1,
                            "terminal_outcome_source": 1,
                        },
                        "screen_stalls": {
                            "by_screen": {"CHEST": 1},
                            "by_reason": {"chest_screen_stall": 1},
                        },
                        "action_errors": {
                            "by_kind": {"chest_choose_to_proceed": 1},
                            "by_status": {"preflight_mismatch": 1},
                        },
                        "mcp_reads": {
                            "by_event": {"error": 2},
                            "by_diagnostics_status": {"read_failed": 2},
                        },
                        "synthetic_terminals": {
                            "by_source": {"main_menu_after_in_game": 1},
                            "terminal_outcome_sources": {"synthetic_after_mcp_null": 1},
                        },
                    },
                },
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(
            report["execution_recovery"]["action_recovery"],
            {
                "total": 2,
                "recovered": 1,
                "unrecovered": 1,
                "runs_with_action_recovery": 2,
                "runs_with_recovered_action": 1,
                "runs_with_unrecovered_action": 1,
                "by_status": {"failed": 1, "recovered": 1},
                "by_kind": {"stale_target_index": 2},
                "examples": [
                    {
                        "source_log": "run.jsonl",
                        "source_category": "clean_trainable",
                        "step": 218,
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "kind": "unavailable_action",
                        "actions": [{"action": "choose", "choice_index": 1}],
                        "available_commands": ["proceed"],
                        "last_error": "Preflight unavailable action(s): ['choose']; available commands: ['proceed']",
                        "last_state": {
                            "screen_type": "MAP",
                            "room_phase": "COMPLETE",
                            "floor": 15,
                        },
                    }
                ],
            },
        )
        self.assertEqual(
            report["execution_recovery"]["terminal_recovery"],
            {
                "attempted": 2,
                "failed": 1,
                "succeeded": 1,
                "synthetic_terminals": 2,
                "unhealthy": 1,
            },
        )
        self.assertEqual(
            report["execution_recovery"]["manifests"][0]["action_recovery_source"],
            "manifest_summary",
        )
        self.assertEqual(
            report["execution_recovery"]["manifests"][0]["terminal_recovery_source"],
            "manifest_summary",
        )
        self.assertEqual(report["counts"]["action_recovery_total"], 2)
        self.assertEqual(report["counts"]["action_recovery_unrecovered"], 1)
        self.assertEqual(report["counts"]["terminal_recovery_synthetic_terminals"], 2)
        self.assertEqual(report["counts"]["terminal_recovery_failed"], 1)
        self.assertEqual(report["counts"]["failure_evidence_runs"], 2)
        self.assertEqual(report["counts"]["failure_evidence_screen_stalls"], 1)
        self.assertEqual(report["counts"]["failure_evidence_mcp_reads"], 2)
        self.assertEqual(report["counts"]["failure_evidence_action_errors"], 1)
        self.assertEqual(
            report["failure_evidence"]["screen_stalls"]["by_screen"],
            {"CHEST": 1},
        )
        self.assertEqual(
            report["failure_evidence"]["mcp_reads"]["by_diagnostics_status"],
            {"read_failed": 2},
        )
        self.assertEqual(
            report["failure_evidence"]["synthetic_terminals"]["terminal_outcome_sources"],
            {"synthetic_after_mcp_null": 1},
        )
        self.assertEqual(
            report["failure_evidence"]["manifests"][0]["failure_evidence_source"],
            "manifest_summary",
        )
        line = status_line(report)
        self.assertIn("evidence=mcp_read:2", line)
        self.assertIn("screen_stalls=CHEST:1", line)
        self.assertIn("action_errors=chest_choose_to_proceed:1", line)
        self.assertIn("mcp_reads=read_failed:2", line)
        self.assertIn("terminal_sources=synthetic_after_mcp_null:1", line)
        self.assertIn("action_recovery=stale_target_index:2/2", line)
        self.assertIn("unrecovered_actions=1", line)
        self.assertIn(
            "terminal_recovery=synthetic:2,attempted:2,succeeded:1,failed:1,unhealthy:1",
            line,
        )

    def test_report_falls_back_to_run_execution_recovery_when_summary_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            recovered_run = item(
                grade="usable_with_recoveries",
                recovered=1,
                attribution="combat_planning",
                flags=["recovered_action_race", "synthetic_terminal"],
            )
            recovered_run["action_recovery_summary"] = {
                "total": 1,
                "recovered": 1,
                "unrecovered": 0,
                "by_status": {"recovered": 1},
                "by_kind": {"stale_potion_slot": 1},
            }
            recovered_run["failure_evidence"] = {
                "synthetic_terminal": {
                    "terminal_recovery_attempted": True,
                    "terminal_recovery_succeeded": False,
                    "post_recovery_status": "missing_state",
                }
            }
            write_manifest(manifest, {"clean_trainable": [recovered_run]})

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(report["execution_recovery"]["action_recovery"]["total"], 1)
        self.assertEqual(
            report["execution_recovery"]["action_recovery"]["by_kind"],
            {"stale_potion_slot": 1},
        )
        self.assertEqual(
            report["execution_recovery"]["terminal_recovery"],
            {"attempted": 1, "failed": 1, "synthetic_terminals": 1, "unhealthy": 1},
        )
        self.assertEqual(
            report["execution_recovery"]["manifests"][0]["action_recovery_source"],
            "category_fallback",
        )
        self.assertEqual(
            report["execution_recovery"]["manifests"][0]["terminal_recovery_source"],
            "category_fallback",
        )
        self.assertEqual(report["failure_evidence"]["runs_with_evidence"], 1)
        self.assertEqual(report["failure_evidence"]["by_type"], {"synthetic_terminal": 1})
        self.assertEqual(
            report["failure_evidence"]["synthetic_terminals"]["by_source"],
            {"unknown_source": 1},
        )
        self.assertEqual(
            report["failure_evidence"]["manifests"][0]["failure_evidence_source"],
            "category_fallback",
        )
        line = status_line(report)
        self.assertIn("evidence=synthetic_terminal:1", line)
        self.assertIn("action_recovery=stale_potion_slot:1/1", line)
        self.assertIn("terminal_recovery=synthetic:1,attempted:1,failed:1,unhealthy:1", line)

    def test_report_falls_back_to_adjacent_shadow_rows_for_feature_quality(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe777_a0.json"
            write_manifest(
                manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
            )
            write_jsonl(
                root / "shadow_probe777_a0" / "route_risk.jsonl",
                [
                    {
                        "source_validation_grade": "pristine",
                        "floor": 3,
                        "deck_total_current_damage": 38,
                    }
                ],
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(report["data_quality"]["shadow_feature_rows"], 1)
        self.assertEqual(report["data_quality"]["coverage_manifests"], 1)
        self.assertEqual(report["data_quality"]["missing_coverage_manifests"], 0)
        self.assertEqual(report["data_quality"]["manifests"][0]["coverage_source"], "shadow_dir_fallback")
        self.assertIn("feature_rows=1", status_line(report))
        self.assertNotIn("feature_missing", status_line(report))

    def test_report_falls_back_to_suffixed_shadow_rows_for_feature_quality(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe779_a0.json"
            write_manifest(
                manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
            )
            write_jsonl(
                root / "shadow_probe779_a0_regen" / "route_risk.jsonl",
                [
                    {
                        "source_validation_grade": "pristine",
                        "floor": 3,
                        "deck_total_current_damage": 38,
                    }
                ],
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(report["data_quality"]["shadow_feature_rows"], 1)
        self.assertEqual(report["data_quality"]["manifests"][0]["coverage_source"], "shadow_dir_fallback")
        self.assertIn("feature_rows=1", status_line(report))

    def test_report_marks_adjacent_empty_shadow_rows_as_empty(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe778_a0.json"
            write_manifest(
                manifest,
                {"clean_trainable": [item(attribution="combat_planning")]},
            )
            write_jsonl(root / "shadow_probe778_a0" / "route_risk.jsonl", [])

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertEqual(report["data_quality"]["shadow_feature_rows"], 0)
        self.assertEqual(report["data_quality"]["missing_coverage_manifests"], 1)
        self.assertEqual(report["data_quality"]["manifests"][0]["coverage_source"], "shadow_dir_empty")
        self.assertEqual(report["data_quality"]["manifests"][0]["reason"], "shadow_dir_empty")
        self.assertIn("feature_missing=1", status_line(report))

    def test_report_fails_unknown_failure_attribution_before_strategy_promotion(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            write_manifest(
                manifest,
                {
                    "clean_trainable": [
                        item(attribution="unknown_clean_failure", reached=False, cleared=False, floor=5),
                        item(attribution="combat_planning"),
                    ]
                },
            )

            report = build_report([manifest], min_reached=1, min_cleared=1, min_pristine_cleared=1)

        self.assertFalse(report["gate"]["passed"])
        self.assertEqual(report["counts"]["unknown_attribution_runs"], 1)
        self.assertEqual(report["gate"]["blocking_reasons"], ["unknown_failure_attribution"])
        self.assertEqual(report["gate"]["primary_blocking_reason"], "unknown_failure_attribution")
        self.assertEqual(report["gate"]["next_action"], "fix_failure_attribution")
        self.assertIn("unknown_attr=1", status_line(report))

    def test_cli_writes_report_without_failing_by_default(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            output = root / "gate.json"
            write_manifest(manifest, {"clean_trainable": [item(attribution="combat_planning")]})

            code = main([str(manifest), "--min-reached", "2", "--output", str(output)])
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertFalse(payload["gate"]["passed"])
        self.assertEqual(payload["gate"]["blocking_reasons"], [
            "insufficient_act1_boss_reached",
            "insufficient_act1_boss_cleared",
            "insufficient_pristine_act1_boss_cleared",
        ])
        self.assertEqual(payload["gate"]["deficits"]["act1_boss_reached"], 1)
        self.assertEqual(payload["gate"]["progress"]["act1_boss_reached"], {
            "current": 1,
            "required": 2,
            "deficit": 1,
            "ok": False,
        })
        self.assertEqual(
            [entry["metric"] for entry in payload["gate"]["remaining_progress"]],
            ["act1_boss_reached", "act1_boss_cleared", "pristine_act1_boss_cleared"],
        )
        self.assertEqual(payload["gate"]["primary_remaining_progress"]["metric"], "act1_boss_reached")
        self.assertEqual(payload["gate"]["next_probe_goal"]["summary"], "collect 1 more Act 1 boss reach")
        self.assertEqual(payload["gate"]["next_action"], "improve_route_and_early_act1_survival")


if __name__ == "__main__":
    unittest.main()
