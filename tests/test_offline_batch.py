import json
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.offline_batch import (
    _agent_assignment_prompt,
    _compact_gate_data_quality,
    _compact_static_knowledge_gaps,
    _gate_data_quality_queue_item,
    _manifest_schema_queue_item,
    _write_next_handoff_prompt,
    build_batch_triage,
    main,
    run_offline_batch,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class OfflineBatchTests(unittest.TestCase):
    def test_compact_gate_data_quality_keeps_unknown_static_facts(self):
        compact = _compact_gate_data_quality(
            {
                "total_manifests": 1,
                "coverage_manifests": 1,
                "shadow_feature_rows": 3,
                "unknown_static_manifests": 1,
                "unknown_static_total": 2,
                "feature_issue_categories": {"potion_tempo": 1},
                "unknown_static_features": {"potion_tempo:enemy_unknown_count": 2},
                "schema_missing_manifests": 1,
                "schema_missing_key_total": 3,
                "schema_missing_summary_keys": {"action_recovery": 1, "terminal_recovery": 1},
                "manifests": [
                    {
                        "manifest_path": "data/training_manifest_probe104_a0.json",
                        "schema_complete": False,
                        "missing_summary_keys": ["action_recovery", "terminal_recovery"],
                        "coverage_source": "shadow_dir_fallback",
                    }
                ],
            }
        )

        self.assertEqual(
            compact,
            {
                "total_manifests": 1,
                "coverage_manifests": 1,
                "shadow_feature_rows": 3,
                "unknown_static_manifests": 1,
                "unknown_static_total": 2,
                "feature_issue_categories": {"potion_tempo": 1},
                "unknown_static_features": {"potion_tempo:enemy_unknown_count": 2},
                "schema_missing_manifests": 1,
                "schema_missing_key_total": 3,
                "schema_missing_summary_keys": {"action_recovery": 1, "terminal_recovery": 1},
                "stale_manifest_targets": [
                    {
                        "manifest_path": "data/training_manifest_probe104_a0.json",
                        "missing_key_count": 2,
                        "missing_summary_keys": ["action_recovery", "terminal_recovery"],
                        "coverage_source": "shadow_dir_fallback",
                    }
                ],
            },
        )

    def test_compact_gate_data_quality_truncates_stale_manifest_targets(self):
        compact = _compact_gate_data_quality(
            {
                "schema_missing_manifests": 10,
                "schema_missing_key_total": 10,
                "manifests": [
                    {
                        "manifest_path": f"data/training_manifest_probe{idx}_a0.json",
                        "schema_complete": False,
                        "missing_summary_keys": ["action_recovery"],
                    }
                    for idx in range(100, 110)
                ],
            }
        )

        self.assertEqual(len(compact["stale_manifest_targets"]), 8)
        self.assertEqual(compact["stale_manifest_targets"][0]["manifest_path"], "data/training_manifest_probe100_a0.json")
        self.assertTrue(compact["stale_manifest_targets_truncated"])
        self.assertEqual(compact["stale_manifest_targets_remaining"], 2)

    def test_batch_triage_routes_diagnostic_collection_to_runner_agent(self):
        manifest = {
            "summary": {
                "diagnostic_excluded": 1,
                "classification_reasons": {
                    "diagnostic_excluded": {"no_terminal_outcome": 1},
                },
            },
            "categories": {
                "clean_trainable": [],
                "diagnostic_excluded": [
                    {
                        "path": "diagnostic_boss.jsonl",
                        "reason": "no_terminal_outcome",
                        "character": "IRONCLAD",
                        "ascension": 0,
                        "floor": 16,
                        "failure_attribution": "diagnostic_incomplete",
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
            },
        }

        triage = build_batch_triage(manifest)

        self.assertEqual(
            [(run["path"], run["owner"], run["action"]) for run in triage["runs"]],
            [("diagnostic_boss.jsonl", "runner_agent", "exclude_and_collect_terminal_boss_evidence")],
        )
        runner_item = next(
            item
            for item in triage["agent_queues"]["runner_agent"]
            if item["action"] == "exclude_and_collect_terminal_boss_evidence"
        )
        self.assertEqual(runner_item["reason"], "per_run_diagnosis")
        self.assertEqual(runner_item["paths"], ["diagnostic_boss.jsonl"])
        self.assertEqual(runner_item["execution_contract"]["mode"], "live_mcp_gated_collection")
        self.assertTrue(runner_item["execution_contract"]["requires_live_mcp_ownership"])
        self.assertIn(
            "control_live_mcp_without_explicit_ownership",
            runner_item["execution_contract"]["forbidden_operations"],
        )
        self.assertEqual(
            triage["agent_queue_actions"]["runner_agent"][0]["execution_mode"],
            "live_mcp_gated_collection",
        )
        self.assertTrue(triage["agent_queue_actions"]["runner_agent"][0]["requires_live_mcp_ownership"])
        self.assertEqual(triage["next_handoff"]["primary_worker"]["owner"], "runner_agent")

    def test_unknown_static_facts_queue_gate_data_quality_review(self):
        item = _gate_data_quality_queue_item(
            {
                "shadow_feature_rows": 3,
                "unknown_static_manifests": 1,
                "unknown_static_total": 2,
                "unknown_static_features": {"potion_tempo:enemy_unknown_count": 2},
            }
        )

        self.assertIsNotNone(item)
        self.assertEqual(item["action"], "review_gate_data_quality")
        self.assertEqual(item["issues"], {"unknown_static_manifests": 1})
        self.assertEqual(item["gate_data_quality"]["unknown_static_total"], 2)

    def test_stale_manifest_schema_queues_engineering_schema_refresh(self):
        item = _manifest_schema_queue_item(
            {
                "schema_missing_manifests": 2,
                "schema_missing_key_total": 5,
                "schema_missing_summary_keys": {"action_recovery": 2},
                "stale_manifest_targets": [
                    {
                        "manifest_path": "data/training_manifest_probe104_a0.json",
                        "missing_key_count": 1,
                        "missing_summary_keys": ["action_recovery"],
                    }
                ],
                "stale_manifest_targets_truncated": True,
                "stale_manifest_targets_remaining": 3,
            }
        )

        self.assertIsNotNone(item)
        self.assertEqual(item["action"], "refresh_stale_manifest_schema")
        self.assertEqual(item["issues"], {"schema_missing_manifests": 2})
        self.assertEqual(item["count"], 2)
        self.assertEqual(item["reason"], "manifest_schema_missing_summary_keys")
        self.assertEqual(item["schema_missing_key_total"], 5)
        self.assertEqual(item["schema_missing_summary_keys"], {"action_recovery": 2})
        self.assertEqual(
            item["stale_manifest_targets"],
            [
                {
                    "manifest_path": "data/training_manifest_probe104_a0.json",
                    "missing_key_count": 1,
                    "missing_summary_keys": ["action_recovery"],
                }
            ],
        )
        self.assertEqual(
            item["refresh_manifest_schema_commands"],
            [
                {
                    "kind": "training_manifest_schema_refresh",
                    "manifest_path": "data/training_manifest_probe104_a0.json",
                    "argv": [
                        "python",
                        "-m",
                        "slay_ai.training_manifest",
                        "--refresh-from",
                        "data/training_manifest_probe104_a0.json",
                        "--output",
                        "data/training_manifest_probe104_a0.json",
                        "--knowledge-dir",
                        str(Path("data") / "static_knowledge"),
                    ],
                    "touches_live_mcp": False,
                    "trains_models": False,
                    "writes_models": False,
                }
            ],
        )
        self.assertTrue(item["stale_manifest_targets_truncated"])
        self.assertEqual(item["stale_manifest_targets_remaining"], 3)
        self.assertEqual(item["acceptance_criteria"]["schema_missing_manifests"], 0)
        self.assertFalse(item["acceptance_criteria"]["touches_live_mcp"])
        self.assertEqual(item["gate_data_quality"]["schema_missing_summary_keys"], {"action_recovery": 2})

        self.assertIsNone(
            _gate_data_quality_queue_item(
                {
                    "schema_missing_manifests": 2,
                    "schema_missing_key_total": 5,
                    "schema_missing_summary_keys": {"action_recovery": 2},
                }
            )
        )

    def test_batch_triage_routes_stale_manifest_schema_to_engineering(self):
        manifest = {
            "summary": {
                "clean_trainable": 1,
                "classification_reasons": {"clean_trainable": {"completed_clean": 1}},
            },
            "categories": {
                "clean_trainable": [
                    {
                        "path": "clean.jsonl",
                        "reason": "completed_clean",
                        "character": "IRONCLAD",
                        "ascension": 0,
                        "failure_attribution": "route_risk",
                        "validation_grade": "pristine",
                        "validation_evidence": {"act1_boss": {"reached": True, "cleared": True}},
                    }
                ],
                "diagnostic_excluded": [],
                "infra_blocked": [],
            },
        }
        triage = build_batch_triage(
            manifest,
            gate_report={
                "gate": {
                    "passed": True,
                    "next_action": "promote_to_next_validation_batch",
                    "next_probe_goal": {
                        "action": "promote_to_next_validation_batch",
                        "acceptance_criteria": {"scope": "gate_already_passed"},
                    },
                    "latest_run_acceptance": {"accepted": True},
                },
                "data_quality": {
                    "schema_missing_manifests": 1,
                    "schema_missing_key_total": 4,
                    "schema_missing_summary_keys": {"action_recovery": 1},
                    "manifests": [
                        {
                            "manifest_path": "data/training_manifest_probe106_a0.json",
                            "schema_complete": False,
                            "missing_summary_keys": ["action_recovery"],
                            "coverage_source": "manifest_summary",
                        }
                    ],
                },
            },
        )

        self.assertEqual(
            [item["action"] for item in triage["agent_queues"]["engineering_agent"]],
            ["refresh_stale_manifest_schema"],
        )
        engineering_item = triage["agent_queues"]["engineering_agent"][0]
        self.assertEqual(engineering_item["issues"], {"schema_missing_manifests": 1})
        self.assertEqual(
            engineering_item["stale_manifest_targets"],
            [
                {
                    "manifest_path": "data/training_manifest_probe106_a0.json",
                    "missing_key_count": 1,
                    "missing_summary_keys": ["action_recovery"],
                    "coverage_source": "manifest_summary",
                }
            ],
        )
        self.assertEqual(engineering_item["execution_contract"]["mode"], "offline_code_or_data_infra")
        self.assertFalse(engineering_item["execution_contract"]["requires_live_mcp_ownership"])
        self.assertIn("refresh_manifest_schema", engineering_item["execution_contract"]["allowed_operations"])
        self.assertEqual(engineering_item["acceptance_criteria"]["schema_missing_manifests"], 0)
        self.assertNotIn(
            "review_gate_data_quality",
            [item["action"] for item in triage["agent_queues"]["ai_agent"]],
        )
        self.assertEqual(triage["next_handoff"]["primary_worker"]["owner"], "engineering_agent")
        self.assertEqual(
            triage["next_handoff"]["primary_worker"]["item"]["action"],
            "refresh_stale_manifest_schema",
        )

        prompt = _agent_assignment_prompt(
            {
                "owner": "engineering_agent",
                "ownership_contract": {
                    "role": "execution and data infrastructure",
                    "live_mcp_policy": "do not control live MCP",
                },
                "default_execution_contract": engineering_item["execution_contract"],
                "first_item": engineering_item,
                "queue": triage["agent_queues"]["engineering_agent"],
                "queue_count": len(triage["agent_queues"]["engineering_agent"]),
            }
        )
        self.assertIn("Task issues:", prompt)
        self.assertIn('"schema_missing_manifests":1', prompt)
        self.assertIn("Schema missing summary keys:", prompt)
        self.assertIn('"action_recovery":1', prompt)
        self.assertIn("Schema missing key total: 4", prompt)
        self.assertIn("Stale manifest targets:", prompt)
        self.assertIn("data/training_manifest_probe106_a0.json", prompt)
        self.assertIn("Refresh manifest schema commands:", prompt)
        self.assertIn("--refresh-from", prompt)
        self.assertIn("--knowledge-dir", prompt)
        if engineering_item.get("stale_manifest_targets_truncated"):
            self.assertIn("Stale manifest targets truncated:", prompt)
        self.assertIn("Acceptance criteria:", prompt)
        self.assertIn('"schema_missing_manifests":0', prompt)
        self.assertIn("refresh_manifest_schema", prompt)

    def test_replay_resolved_label_quality_does_not_queue_gate_data_quality_review(self):
        item = _gate_data_quality_queue_item(
            {
                "shadow_label_excluded": 3,
                "label_exclusion_manifests": 1,
                "missed_single_card_search_labels": 2,
            },
            combat_label_replay_audit={
                "excluded_rows": 3,
                "replayed_rows": 3,
                "current_policy_matches_label": 3,
            },
        )

        self.assertIsNone(item)

    def test_replay_resolved_label_quality_keeps_unrelated_gate_data_quality_issues(self):
        item = _gate_data_quality_queue_item(
            {
                "feature_gap_manifests": 1,
                "shadow_label_excluded": 3,
                "label_exclusion_manifests": 1,
            },
            combat_label_replay_audit={
                "excluded_rows": 3,
                "replayed_rows": 3,
                "current_policy_matches_label": 3,
            },
        )

        self.assertIsNotNone(item)
        self.assertEqual(item["count"], 1)
        self.assertEqual(item["reason"], "gate_data_quality_issues_with_replay_resolved_labels")
        self.assertEqual(item["issues"], {"feature_gap_manifests": 1})
        self.assertEqual(
            item["resolved_issues"],
            {"label_exclusion_manifests": 1, "shadow_label_excluded": 3},
        )
        self.assertTrue(item["combat_label_replay_resolution"]["current_policy_resolved"])

    def test_static_knowledge_gaps_compact_summary_keeps_top_entities(self):
        compact = _compact_static_knowledge_gaps(
            {
                "status_line": "static_knowledge_gaps logs=1 missing=3 cards=2/1 relics=1/1",
                "resolved_log_count": 1,
                "total_missing_occurrences": 3,
                "warnings": [],
                "totals": {
                    "cards": {"occurrences": 2, "unique": 1},
                    "potions": {"occurrences": 0, "unique": 0},
                    "relics": {"occurrences": 1, "unique": 1},
                },
                "entities": {
                    "cards": {
                        "items": [
                            {
                                "entity": "Mystery Card",
                                "count": 2,
                                "sources": [
                                    {"path": "run.jsonl", "step": 1, "field": "deck"},
                                    {"path": "run.jsonl", "step": 2, "field": "combat.hand_cards"},
                                    {"path": "run.jsonl", "step": 3, "field": "draw_pile"},
                                ],
                            }
                        ]
                    },
                    "relics": {
                        "items": [
                            {
                                "entity": "Mystery Relic",
                                "count": 1,
                                "sources": [{"path": "run.jsonl", "step": 1, "field": "relics"}],
                            }
                        ]
                    },
                },
            }
        )

        self.assertEqual(compact["total_missing_occurrences"], 3)
        self.assertEqual(compact["resolved_log_count"], 1)
        self.assertEqual(compact["totals"]["cards"], {"occurrences": 2, "unique": 1})
        self.assertEqual(compact["entities"]["cards"][0]["entity"], "Mystery Card")
        self.assertEqual(compact["entities"]["cards"][0]["count"], 2)
        self.assertEqual(len(compact["entities"]["cards"][0]["sources"]), 2)

    def test_gate_data_quality_item_includes_static_knowledge_gap_context(self):
        item = _gate_data_quality_queue_item(
            {
                "shadow_feature_rows": 3,
                "unknown_static_manifests": 1,
                "unknown_static_total": 2,
                "unknown_static_features": {"route_risk:card_unknown_count": 2},
            },
            static_knowledge_gaps={
                "status_line": "static_knowledge_gaps logs=1 missing=2 cards=2/1",
                "total_missing_occurrences": 2,
                "entities": {"cards": [{"entity": "Mystery Card", "count": 2}]},
            },
        )

        self.assertIsNotNone(item)
        self.assertEqual(item["static_knowledge_gaps"]["total_missing_occurrences"], 2)
        self.assertEqual(item["static_knowledge_gaps"]["entities"]["cards"][0]["entity"], "Mystery Card")

    def test_batch_triage_attaches_combat_label_audit_to_excluded_label_review(self):
        manifest = {
            "summary": {
                "shadow_label_quality": {
                    "combat_search_labels": {
                        "total": 2,
                        "trainable": 1,
                        "excluded_from_training": 1,
                        "exclusion_reasons": {"missed_direct_kill": 1},
                    }
                }
            },
            "categories": {
                "clean_trainable": [],
                "diagnostic_excluded": [],
                "infra_blocked": [],
            },
        }

        triage = build_batch_triage(
            manifest,
            combat_label_audit_report={
                "status_line": "combat_label_audit files=1 rows=2 excluded=1 missed_direct_kill=1",
                "resolved_file_count": 1,
                "rows": 2,
                "accepted_rows": 1,
                "excluded_rows": 1,
                "exclusion_reasons": {"missed_direct_kill": 1},
                "examples": [{"source_log": "run.jsonl", "step": 42, "label_first_card_key": "Defend_R"}],
            },
            combat_label_replay_audit_report={
                "status_line": "combat_label_replay_audit files=1 excluded=1 replayed=1 matches_label=1 matches_actual=0 other=0",
                "resolved_file_count": 1,
                "excluded_rows": 1,
                "replayed_rows": 1,
                "current_policy_matches_label": 1,
                "current_policy_matches_actual": 0,
                "current_policy_other": 0,
                "replay_outcomes": {"current_policy_matches_label": 1},
                "examples": [{"source_log": "run.jsonl", "step": 42, "outcome": "current_policy_matches_label"}],
            },
        )

        item = next(
            item
            for item in triage["agent_queues"]["ai_agent"]
            if item["action"] == "review_excluded_combat_search_labels"
        )
        self.assertEqual(triage["combat_label_audit"]["excluded_rows"], 1)
        self.assertEqual(triage["combat_label_replay_audit"]["current_policy_matches_label"], 1)
        self.assertEqual(item["combat_label_audit"], triage["combat_label_audit"])
        self.assertEqual(item["combat_label_replay_audit"], triage["combat_label_replay_audit"])
        self.assertEqual(item["combat_label_audit"]["examples"][0]["step"], 42)
        self.assertEqual(item["combat_label_replay_audit"]["examples"][0]["outcome"], "current_policy_matches_label")

    def test_agent_assignment_prompt_includes_terminal_recovery_summary(self):
        prompt = _agent_assignment_prompt(
            {
                "owner": "engineering_agent",
                "ownership_contract": {
                    "role": "execution and data infrastructure",
                    "live_mcp_policy": "do not control live MCP",
                },
                "default_execution_contract": {"mode": "offline_code_or_data_infra"},
                "first_item": {
                    "action": "fix_execution_layer",
                    "reason": "per_run_diagnosis",
                    "execution_contract": {
                        "mode": "offline_code_or_data_infra",
                        "requires_live_mcp_ownership": False,
                    },
                    "failure_evidence": {
                        "screen_stalls": [
                            {
                                "screen_type": "CHEST",
                                "floor": 9,
                                "repeat_count": 3,
                                "last_reward_count": 0,
                                "chest_open": False,
                            }
                        ],
                        "mcp_reads": [
                            {
                                "step": 47,
                                "last_state": {"screen_type": "CHEST", "floor": 9},
                            }
                        ],
                        "action_errors": [{"kind": "chest_choose_to_proceed"}],
                    },
                },
                "action_recovery": {
                    "total": 1,
                    "recovered": 0,
                    "unrecovered": 1,
                    "by_kind": {"stale_target_index": 1},
                },
                "terminal_recovery": {
                    "synthetic_terminals": 1,
                    "attempted": 1,
                    "failed": 1,
                    "unhealthy": 1,
                },
                "gate_execution_recovery": {
                    "action_recovery": {
                        "total": 1,
                        "recovered": 1,
                        "unrecovered": 0,
                        "by_kind": {"stale_target_index": 1},
                    },
                    "terminal_recovery": {
                        "synthetic_terminals": 1,
                        "attempted": 1,
                        "succeeded": 1,
                    },
                },
                "gate_data_quality": {
                    "shadow_feature_rows": 2,
                    "feature_gap_manifests": 1,
                    "feature_gaps": {"route_risk:deck_": 1},
                    "shadow_label_rows": 3,
                    "shadow_label_excluded": 1,
                    "label_exclusion_reasons": {"missed_direct_kill": 1},
                },
                "static_knowledge_gaps": {
                    "status_line": "static_knowledge_gaps logs=1 missing=2 cards=2/1",
                    "total_missing_occurrences": 2,
                    "entities": {"cards": [{"entity": "Mystery Card", "count": 2}]},
                },
                "combat_label_audit": {
                    "status_line": "combat_label_audit files=1 rows=2 excluded=1 missed_direct_kill=1",
                    "excluded_rows": 1,
                    "examples": [{"source_log": "run.jsonl", "step": 42}],
                },
                "combat_label_replay_audit": {
                    "status_line": "combat_label_replay_audit files=1 excluded=1 replayed=1 matches_label=1 matches_actual=0 other=0",
                    "current_policy_matches_label": 1,
                    "examples": [{"source_log": "run.jsonl", "step": 42, "outcome": "current_policy_matches_label"}],
                },
                "gate_data_quality_replay_resolved_issues": {
                    "reason": "current_policy_replay_resolved",
                    "issues": {"shadow_label_excluded": 1},
                },
                "gate_failure_evidence": {
                    "runs_with_evidence": 2,
                    "by_type": {"mcp_read": 2, "screen_stall": 1},
                    "screen_stalls": {"by_screen": {"CHEST": 1}},
                    "mcp_reads": {"by_diagnostics_status": {"read_failed": 2}},
                },
            }
        )

        self.assertIn("Action recovery:", prompt)
        self.assertIn("Failure evidence:", prompt)
        self.assertIn('"screen_type":"CHEST"', prompt)
        self.assertIn('"chest_open":false', prompt)
        self.assertIn('"kind":"chest_choose_to_proceed"', prompt)
        self.assertIn('"stale_target_index":1', prompt)
        self.assertIn("Terminal recovery:", prompt)
        self.assertIn('"failed":1', prompt)
        self.assertIn('"unhealthy":1', prompt)
        self.assertIn("Gate execution recovery:", prompt)
        self.assertIn('"succeeded":1', prompt)
        self.assertIn("Gate data quality:", prompt)
        self.assertIn('"feature_gaps":{"route_risk:deck_":1}', prompt)
        self.assertIn('"label_exclusion_reasons":{"missed_direct_kill":1}', prompt)
        self.assertIn("Static knowledge gaps:", prompt)
        self.assertIn('"entity":"Mystery Card"', prompt)
        self.assertIn("Combat label audit:", prompt)
        self.assertIn('"source_log":"run.jsonl"', prompt)
        self.assertIn("Combat label replay audit:", prompt)
        self.assertIn('"outcome":"current_policy_matches_label"', prompt)
        self.assertIn("Gate data quality replay-resolved issues:", prompt)
        self.assertIn('"shadow_label_excluded":1', prompt)
        self.assertIn("Gate failure evidence:", prompt)
        self.assertIn('"mcp_read":2', prompt)
        self.assertIn('"read_failed":2', prompt)

    def test_next_handoff_summary_includes_primary_failure_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt_path = root / "worker.txt"
            prompt_path.write_text("worker prompt\n", encoding="utf-8")
            next_path = root / "next_handoff.txt"

            _write_next_handoff_prompt(
                next_path,
                str(prompt_path),
                None,
                next_handoff={
                    "primary_worker": {
                        "owner": "engineering_agent",
                        "item": {
                            "action": "fix_execution_layer",
                            "reason": "per_run_diagnosis",
                            "failure_evidence": {
                                "screen_stalls": [
                                    {
                                        "screen_type": "CHEST",
                                        "floor": 9,
                                        "chest_open": False,
                                    }
                                ],
                                "action_errors": [{"kind": "chest_choose_to_proceed"}],
                            },
                        },
                    },
                    "coordinator": {"owner": "main_agent", "item": {"action": "merge_evidence"}},
                },
                gate_status_line="act1_boss_gate NEEDS_WORK: runs=2 reached=1/3",
                gate_primary_remaining_progress={
                    "metric": "pristine_act1_boss_cleared",
                    "current": 1,
                    "required": 2,
                    "deficit": 1,
                    "ok": False,
                },
                gate_remaining_progress=[
                    {
                        "metric": "pristine_act1_boss_cleared",
                        "current": 1,
                        "required": 2,
                        "deficit": 1,
                        "ok": False,
                    }
                ],
                latest_run_acceptance={
                    "available": True,
                    "accepted": False,
                    "blockers": ["act1_boss_not_cleared"],
                },
                next_probe_goal={
                    "action": "collect_pristine_act1_boss_clears",
                    "summary": "collect one clean Act 1 boss clear",
                    "acceptance_criteria": {"scope": "act1_boss_gate"},
                },
                gate_failure_evidence={
                    "runs_with_evidence": 2,
                    "by_type": {"mcp_read": 2, "screen_stall": 1},
                    "screen_stalls": {"by_screen": {"CHEST": 1}},
                },
                gate_data_quality_replay_resolved_issues={
                    "reason": "current_policy_replay_resolved",
                    "issues": {"shadow_label_excluded": 2},
                },
            )

            text = next_path.read_text(encoding="utf-8")

        self.assertIn("Primary worker: engineering_agent:fix_execution_layer", text)
        self.assertIn("Gate status: act1_boss_gate NEEDS_WORK: runs=2 reached=1/3", text)
        self.assertIn("Gate primary remaining progress:", text)
        self.assertIn('"metric":"pristine_act1_boss_cleared"', text)
        self.assertIn('"deficit":1', text)
        self.assertIn("Gate remaining progress:", text)
        self.assertIn("Latest run acceptance:", text)
        self.assertIn('"accepted":false', text)
        self.assertIn('"blockers":["act1_boss_not_cleared"]', text)
        self.assertIn("Next probe goal:", text)
        self.assertIn('"action":"collect_pristine_act1_boss_clears"', text)
        self.assertIn('"scope":"act1_boss_gate"', text)
        self.assertIn("Gate failure evidence:", text)
        self.assertIn("Gate data quality replay-resolved issues:", text)
        self.assertIn('"shadow_label_excluded":2', text)
        self.assertIn('"mcp_read":2', text)
        self.assertIn('"screen_stall":1', text)
        self.assertIn("Primary worker failure evidence:", text)
        self.assertIn('"screen_type":"CHEST"', text)
        self.assertIn('"chest_open":false', text)
        self.assertIn('"kind":"chest_choose_to_proceed"', text)

    def test_build_batch_triage_uses_manifest_action_recovery_summary(self):
        manifest = {
            "summary": {
                "clean_trainable": 0,
                "diagnostic_excluded": 0,
                "infra_blocked": 0,
                "action_recovery": {
                    "total": 2,
                    "recovered": 1,
                    "unrecovered": 1,
                    "runs_with_action_recovery": 2,
                    "runs_with_recovered_action": 1,
                    "runs_with_unrecovered_action": 1,
                    "by_status": {"preflight_mismatch": 2},
                    "by_kind": {"stale_target_index": 1, "stale_potion_slot": 1},
                },
            },
            "categories": {
                "clean_trainable": [],
                "diagnostic_excluded": [],
                "infra_blocked": [],
            },
        }

        triage = build_batch_triage(manifest)

        self.assertEqual(
            triage["action_recovery"],
            {
                "total": 2,
                "recovered": 1,
                "unrecovered": 1,
                "runs_with_action_recovery": 2,
                "runs_with_recovered_action": 1,
                "runs_with_unrecovered_action": 1,
                "by_status": {"preflight_mismatch": 2},
                "by_kind": {"stale_potion_slot": 1, "stale_target_index": 1},
            },
        )

    def test_build_batch_triage_uses_manifest_terminal_recovery_summary(self):
        manifest = {
            "summary": {
                "clean_trainable": 0,
                "diagnostic_excluded": 0,
                "infra_blocked": 0,
                "terminal_recovery": {
                    "synthetic_terminals": 2,
                    "attempted": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "unhealthy": 1,
                },
            },
            "categories": {
                "clean_trainable": [],
                "diagnostic_excluded": [],
                "infra_blocked": [],
            },
        }

        triage = build_batch_triage(manifest)

        self.assertEqual(
            triage["terminal_recovery"],
            {
                "attempted": 2,
                "synthetic_terminals": 2,
                "failed": 1,
                "succeeded": 1,
                "unhealthy": 1,
            },
        )

    def test_run_offline_batch_writes_manifest_advice_diagnosis_and_gate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(log, _boss_loss_rows())
            output = root / "out"

            summary = run_offline_batch(
                [log],
                output_dir=output,
                name="probe_test",
                knowledge_dir=None,
                min_reached=1,
                min_cleared=0,
                min_pristine_cleared=0,
            )

            manifest_path = Path(summary["manifest_path"])
            shadow_dir = Path(summary["shadow_dir"])
            advice_dir = Path(summary["advice_dir"])
            diagnosis_path = Path(summary["diagnosis_path"])
            gate_path = Path(summary["gate_path"])
            handoff_dir = Path(summary["handoff_dir"])
            handoff_index_path = Path(summary["handoff_index_path"])
            next_handoff_prompt_path = Path(summary["next_handoff_prompt_path"])
            artifact_manifest_path = Path(summary["artifact_manifest_path"])
            combat_label_audit_path = Path(summary["combat_label_audit_path"])
            combat_label_replay_audit_path = Path(summary["combat_label_replay_audit_path"])
            self.assertTrue(manifest_path.exists())
            self.assertTrue((shadow_dir / "route_risk.jsonl").exists())
            self.assertTrue((advice_dir / "summary.json").exists())
            self.assertTrue(diagnosis_path.exists())
            self.assertTrue(gate_path.exists())
            self.assertTrue(handoff_index_path.exists())
            self.assertTrue(next_handoff_prompt_path.exists())
            self.assertTrue((handoff_dir / "ai_agent.json").exists())
            self.assertTrue((handoff_dir / "main_agent.json").exists())
            self.assertTrue((handoff_dir / "ai_agent.txt").exists())
            self.assertTrue((handoff_dir / "runner_agent.txt").exists())
            self.assertTrue(Path(summary["summary_path"]).exists())
            self.assertTrue(artifact_manifest_path.exists())
            self.assertTrue(combat_label_audit_path.exists())
            self.assertTrue(combat_label_replay_audit_path.exists())
            saved_summary = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved_summary, summary)
            self.assertEqual(summary["resolved_logs"], [str(log)])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["clean_trainable"], 1)
            self.assertEqual(manifest["shadow_advice"]["path"], str(advice_dir))
            self.assertEqual(summary["artifact_paths"]["manifest_path"], str(manifest_path))
            self.assertEqual(summary["artifact_paths"]["gate_path"], str(gate_path))
            self.assertEqual(summary["artifact_paths"]["handoff_dir"], str(handoff_dir))
            self.assertEqual(summary["artifact_paths"]["handoff_index_path"], str(handoff_index_path))
            self.assertEqual(summary["artifact_paths"]["next_handoff_prompt_path"], str(next_handoff_prompt_path))
            self.assertEqual(summary["artifact_paths"]["artifact_manifest_path"], str(artifact_manifest_path))
            self.assertEqual(summary["artifact_paths"]["combat_label_audit_path"], str(combat_label_audit_path))
            self.assertEqual(
                summary["artifact_paths"]["combat_label_replay_audit_path"],
                str(combat_label_replay_audit_path),
            )
            self.assertEqual(summary["artifact_manifest"]["path"], str(artifact_manifest_path))
            self.assertEqual(summary["artifact_manifest"]["health_source"], "artifact_manifest_path")
            self.assertTrue(summary["artifact_manifest"]["self_checksum_excluded"])
            artifact_manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(artifact_manifest["ok"])
            self.assertEqual(artifact_manifest["missing"], [])
            self.assertEqual(artifact_manifest["errors"], [])
            self.assertEqual(artifact_manifest["warnings"], [])
            self.assertEqual(artifact_manifest["artifact_manifest_path"], str(artifact_manifest_path))
            self.assertEqual(artifact_manifest["inputs"], [str(log)])
            self.assertEqual(artifact_manifest["input_count"], 1)
            self.assertEqual(artifact_manifest["input_health"][0]["path"], str(log))
            self.assertEqual(artifact_manifest["input_health"][0]["kind"], "file")
            self.assertTrue(artifact_manifest["input_health"][0]["exists"])
            self.assertGreater(artifact_manifest["input_health"][0]["bytes"], 0)
            self.assertEqual(len(artifact_manifest["input_health"][0]["sha256"]), 64)
            self.assertEqual(artifact_manifest["resolved_logs"], [str(log)])
            self.assertEqual(artifact_manifest["resolved_log_count"], 1)
            self.assertEqual(artifact_manifest["resolved_log_health"][0]["path"], str(log))
            self.assertEqual(artifact_manifest["resolved_log_health"][0]["kind"], "file")
            self.assertEqual(len(artifact_manifest["resolved_log_health"][0]["sha256"]), 64)
            self.assertEqual(artifact_manifest["artifact_paths"]["summary_path"], summary["summary_path"])
            self.assertEqual(artifact_manifest["files"]["manifest_path"]["kind"], "file")
            self.assertTrue(artifact_manifest["files"]["manifest_path"]["exists"])
            self.assertGreater(artifact_manifest["files"]["manifest_path"]["bytes"], 0)
            self.assertEqual(len(artifact_manifest["files"]["manifest_path"]["sha256"]), 64)
            self.assertEqual(artifact_manifest["files"]["summary_path"]["kind"], "file")
            self.assertEqual(len(artifact_manifest["files"]["summary_path"]["sha256"]), 64)
            self.assertEqual(artifact_manifest["files"]["shadow_dir"]["kind"], "directory")
            self.assertGreater(artifact_manifest["files"]["shadow_dir"]["file_count"], 0)
            self.assertEqual(artifact_manifest["files"]["handoff_index_path"]["path"], str(handoff_index_path))
            self.assertEqual(artifact_manifest["files"]["combat_label_audit_path"]["path"], str(combat_label_audit_path))
            self.assertEqual(
                artifact_manifest["files"]["combat_label_replay_audit_path"]["path"],
                str(combat_label_replay_audit_path),
            )
            self.assertEqual(artifact_manifest["files"]["ai_agent_prompt"]["path"], str(handoff_dir / "ai_agent.txt"))
            self.assertEqual(artifact_manifest["files"]["runner_agent_handoff"]["path"], str(handoff_dir / "runner_agent.json"))
            self.assertEqual(summary["agent_handoff_paths"]["ai_agent"], str(handoff_dir / "ai_agent.json"))
            self.assertEqual(summary["agent_prompt_paths"]["ai_agent"], str(handoff_dir / "ai_agent.txt"))
            self.assertEqual(summary["handoff_index"]["primary_worker_prompt_path"], str(handoff_dir / "ai_agent.txt"))
            self.assertEqual(summary["handoff_index"]["coordinator_prompt_path"], str(handoff_dir / "main_agent.txt"))
            self.assertEqual(summary["offline_refresh_command"]["kind"], "offline_batch_replay")
            self.assertIn("--no-knowledge", summary["offline_refresh_command"]["argv"])
            self.assertIn(str(log), summary["offline_refresh_command"]["argv"])
            self.assertIn(str(log), summary["offline_refresh_command"]["resolved_argv"])
            self.assertIn(str(output), summary["offline_refresh_command"]["argv"])
            self.assertIn(str(output), summary["offline_refresh_command"]["resolved_argv"])
            self.assertTrue(summary["offline_refresh_command"]["resolved_argv_available"])
            self.assertEqual(summary["offline_refresh_command"]["resolved_log_count"], 1)
            self.assertFalse(summary["offline_refresh_command"]["touches_live_mcp"])
            self.assertFalse(summary["offline_refresh_command"]["trains_models"])
            self.assertEqual(summary["manifest_summary"]["validation"]["act1_boss_reached"], 1)
            self.assertTrue(summary["gate_passed"])
            self.assertIn("act1_boss_gate PASS", summary["gate_status_line"])
            self.assertEqual(summary["diagnosis_count"], 1)
            self.assertEqual(len(summary["diagnosis_status_lines"]), 1)
            triage = summary["batch_triage"]
            self.assertEqual(summary["gate_next_action"], triage["gate_next_action"])
            self.assertEqual(summary["gate_progress"], triage["gate_progress"])
            self.assertEqual(summary["gate_remaining_progress"], triage["gate_remaining_progress"])
            self.assertEqual(summary["gate_primary_remaining_progress"], triage["gate_primary_remaining_progress"])
            self.assertEqual(summary["next_probe_goal"], triage["next_probe_goal"])
            self.assertEqual(summary["latest_run_acceptance"], triage["latest_run_acceptance"])
            self.assertEqual(summary["recommended_next_action"], triage["recommended_next_action"])
            self.assertIn("offline_batch_next:", summary["offline_batch_next_line"])
            self.assertIn("recommended=promote_to_next_validation_batch", summary["offline_batch_next_line"])
            self.assertIn("primary=ai_agent:inspect_potion_planning", summary["offline_batch_next_line"])
            self.assertIn("coordinator=main_agent:promote_to_next_validation_batch", summary["offline_batch_next_line"])
            self.assertIn("primary_live_mcp_required=false", summary["offline_batch_next_line"])
            self.assertIn("any_live_mcp_required=true", summary["offline_batch_next_line"])
            summary_file = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
            self.assertEqual(summary_file["gate_next_action"], triage["gate_next_action"])
            self.assertEqual(summary_file["gate_progress"], triage["gate_progress"])
            self.assertEqual(summary_file["gate_remaining_progress"], triage["gate_remaining_progress"])
            self.assertEqual(summary_file["gate_primary_remaining_progress"], triage["gate_primary_remaining_progress"])
            self.assertEqual(summary_file["next_probe_goal"], triage["next_probe_goal"])
            self.assertEqual(summary_file["latest_run_acceptance"], triage["latest_run_acceptance"])
            self.assertEqual(summary_file["recommended_next_action"], triage["recommended_next_action"])
            self.assertEqual(summary_file["offline_batch_next_line"], summary["offline_batch_next_line"])
            self.assertEqual(triage["classification_counts"]["clean_trainable"], 1)
            self.assertEqual(triage["classification_reasons"]["clean_trainable"], {"completed_clean": 1})
            self.assertEqual(triage["validation"]["by_grade"], {"pristine": 1})
            self.assertEqual(triage["validation"]["by_flag"], {})
            self.assertEqual(triage["artifact_paths"]["manifest_path"], str(manifest_path))
            self.assertEqual(triage["artifact_paths"]["diagnosis_path"], str(diagnosis_path))
            self.assertEqual(triage["offline_refresh_command"]["argv"], summary["offline_refresh_command"]["argv"])
            self.assertEqual(triage["handoff_index_path"], str(handoff_index_path))
            self.assertEqual(triage["next_handoff_prompt_path"], str(next_handoff_prompt_path))
            self.assertEqual(triage["agent_handoff_paths"]["main_agent"], str(handoff_dir / "main_agent.json"))
            self.assertEqual(triage["agent_prompt_paths"]["main_agent"], str(handoff_dir / "main_agent.txt"))
            self.assertEqual(triage["agent_queue_counts"], {
                "runner_agent": 1,
                "engineering_agent": 0,
                "ai_agent": 2,
                "main_agent": 1,
            })
            self.assertEqual(
                [item["action"] for item in triage["agent_queue_actions"]["ai_agent"]],
                ["inspect_potion_planning", "review_gate_data_quality"],
            )
            self.assertEqual(
                triage["agent_queue_actions"]["ai_agent"][1],
                {
                    "action": "review_gate_data_quality",
                    "count": 1,
                    "reason": "gate_data_quality_issues",
                    "priority": 1,
                    "execution_mode": "offline_strategy_or_shadow_model",
                    "requires_live_mcp_ownership": False,
                },
            )
            self.assertEqual(triage["next_handoff"]["primary_worker"]["owner"], "ai_agent")
            self.assertEqual(triage["next_handoff"]["primary_worker"]["item"]["action"], "inspect_potion_planning")
            self.assertEqual(triage["next_handoff"]["coordinator"]["item"]["action"], "promote_to_next_validation_batch")
            self.assertEqual(triage["failure_attributions"], {"potion_planning": 1})
            self.assertEqual(triage["recommended_next_action"], "promote_to_next_validation_batch")
            self.assertEqual(triage["gate_status_line"], summary["gate_status_line"])
            self.assertTrue(triage["shadow_advice_provenance"]["available"])
            self.assertEqual(triage["shadow_advice_provenance"]["mode"], "shadow_rows")
            self.assertEqual(triage["shadow_advice_provenance"]["resolved_file_count"], 4)
            self.assertEqual(
                triage["shadow_advice_provenance"]["categories"]["route_risk"]["shadow_input_file_count"],
                1,
            )
            self.assertEqual(len(triage["runs"]), 1)
            self.assertEqual(triage["runs"][0]["owner"], "ai_agent")
            self.assertEqual(triage["runs"][0]["action"], "inspect_potion_planning")
            self.assertTrue(triage["runs"][0]["shadow_advice"]["available"])
            self.assertEqual(triage["runs"][0]["shadow_advice"]["shadow_inputs"]["resolved_file_count"], 4)
            self.assertTrue(triage["runs"][0]["act1_boss"]["reached"])
            self.assertFalse(triage["runs"][0]["act1_boss"]["cleared"])
            self.assertEqual(triage["agent_queues"]["main_agent"][0]["action"], "promote_to_next_validation_batch")
            self.assertEqual(triage["agent_queues"]["main_agent"][0]["artifact_paths"]["gate_path"], str(gate_path))
            self.assertEqual(triage["agent_queues"]["main_agent"][0]["acceptance_criteria"]["scope"], "gate_already_passed")
            self.assertTrue(triage["agent_queues"]["main_agent"][0]["latest_run_acceptance"]["accepted"])
            self.assertEqual(triage["agent_queues"]["runner_agent"][0]["action"], "collect_next_validation_batch")
            self.assertEqual(triage["agent_queues"]["runner_agent"][0]["artifact_paths"]["summary_path"], summary["summary_path"])
            self.assertEqual(triage["agent_queues"]["runner_agent"][0]["acceptance_criteria"]["scope"], "gate_already_passed")
            self.assertTrue(triage["agent_queues"]["runner_agent"][0]["latest_run_acceptance"]["accepted"])
            self.assertEqual(
                triage["agent_queues"]["runner_agent"][0]["execution_contract"]["mode"],
                "live_mcp_gated_collection",
            )
            self.assertTrue(
                triage["agent_queues"]["runner_agent"][0]["execution_contract"]["requires_live_mcp_ownership"]
            )
            self.assertEqual(
                [item["action"] for item in triage["agent_queues"]["ai_agent"]],
                ["inspect_potion_planning", "review_gate_data_quality"],
            )
            self.assertEqual(triage["agent_queues"]["ai_agent"][0]["reason"], "per_run_diagnosis")
            self.assertEqual(
                triage["agent_queues"]["ai_agent"][0]["execution_contract"]["mode"],
                "offline_strategy_or_shadow_model",
            )
            self.assertFalse(
                triage["agent_queues"]["ai_agent"][0]["execution_contract"]["requires_live_mcp_ownership"]
            )
            self.assertEqual(
                triage["agent_queues"]["ai_agent"][1]["issues"],
                {"feature_gap_manifests": 1},
            )
            self.assertFalse(
                triage["agent_queues"]["ai_agent"][1]["execution_contract"]["requires_live_mcp_ownership"]
            )
            ai_handoff = json.loads((handoff_dir / "ai_agent.json").read_text(encoding="utf-8"))
            self.assertEqual(ai_handoff["owner"], "ai_agent")
            self.assertEqual(ai_handoff["handoff_path"], str(handoff_dir / "ai_agent.json"))
            self.assertEqual(ai_handoff["assignment_prompt_path"], str(handoff_dir / "ai_agent.txt"))
            self.assertEqual(ai_handoff["agent_handoff_paths"]["runner_agent"], str(handoff_dir / "runner_agent.json"))
            self.assertEqual(ai_handoff["agent_handoff_paths"]["main_agent"], str(handoff_dir / "main_agent.json"))
            self.assertEqual(ai_handoff["agent_prompt_paths"]["runner_agent"], str(handoff_dir / "runner_agent.txt"))
            self.assertEqual(ai_handoff["ownership_contract"]["role"], "strategy analysis and shadow models")
            self.assertEqual(ai_handoff["ownership_contract"]["live_mcp_policy"], "do not control live MCP")
            self.assertEqual(ai_handoff["default_execution_contract"]["mode"], "offline_strategy_or_shadow_model")
            self.assertEqual(ai_handoff["classification_reasons"], triage["classification_reasons"])
            self.assertEqual(ai_handoff["validation"], triage["validation"])
            self.assertEqual(ai_handoff["action_recovery"], triage["action_recovery"])
            self.assertEqual(ai_handoff["terminal_recovery"], triage["terminal_recovery"])
            self.assertEqual(ai_handoff["gate_data_quality"], triage["gate_data_quality"])
            self.assertEqual(ai_handoff["gate_status_line"], triage["gate_status_line"])
            self.assertEqual(ai_handoff["gate_progress"], triage["gate_progress"])
            self.assertEqual(ai_handoff["gate_remaining_progress"], triage["gate_remaining_progress"])
            self.assertEqual(
                ai_handoff["gate_primary_remaining_progress"],
                triage["gate_primary_remaining_progress"],
            )
            self.assertEqual(ai_handoff["next_probe_goal"], triage["next_probe_goal"])
            self.assertEqual(ai_handoff["latest_run_acceptance"], triage["latest_run_acceptance"])
            self.assertEqual(ai_handoff["offline_refresh_command"]["argv"], summary["offline_refresh_command"]["argv"])
            self.assertEqual(ai_handoff["shadow_advice_provenance"]["resolved_file_count"], 4)
            self.assertEqual(ai_handoff["shadow_label_quality"], triage["shadow_label_quality"])
            self.assertEqual(ai_handoff["combat_label_replay_audit"], triage["combat_label_replay_audit"])
            self.assertEqual(ai_handoff["agent_queue_actions"], triage["agent_queue_actions"])
            self.assertFalse(ai_handoff["offline_refresh_command"]["touches_live_mcp"])
            self.assertFalse(ai_handoff["first_item"]["execution_contract"]["requires_live_mcp_ownership"])
            self.assertTrue(ai_handoff["first_item"]["shadow_advice"]["available"])
            self.assertIn("Agent: ai_agent", ai_handoff["assignment_prompt"])
            self.assertIn("First task: inspect_potion_planning", ai_handoff["assignment_prompt"])
            self.assertIn("Task count: 1", ai_handoff["assignment_prompt"])
            self.assertIn("Queue length: 2", ai_handoff["assignment_prompt"])
            self.assertIn("review_gate_data_quality[count=1][reason=gate_data_quality_issues]", ai_handoff["assignment_prompt"])
            self.assertIn(f"Paths: {log}", ai_handoff["assignment_prompt"])
            self.assertIn("Execution mode: offline_strategy_or_shadow_model", ai_handoff["assignment_prompt"])
            self.assertIn("Requires live MCP ownership: False", ai_handoff["assignment_prompt"])
            self.assertIn("control_live_mcp", ai_handoff["assignment_prompt"])
            self.assertIn("offline_refresh_command.resolved_argv", ai_handoff["assignment_prompt"])
            expected_resolved_argv_text = json.dumps(
                summary["offline_refresh_command"]["resolved_argv"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.assertIn("Offline refresh command:", ai_handoff["assignment_prompt"])
            self.assertIn('"touches_live_mcp":false', ai_handoff["assignment_prompt"])
            self.assertIn(
                f"offline_refresh_command.resolved_argv: {expected_resolved_argv_text}",
                ai_handoff["assignment_prompt"],
            )
            self.assertIn("Classification reasons:", ai_handoff["assignment_prompt"])
            self.assertIn("clean_trainable=completed_clean:1", ai_handoff["assignment_prompt"])
            self.assertIn("Validation summary:", ai_handoff["assignment_prompt"])
            self.assertIn("grades=pristine:1", ai_handoff["assignment_prompt"])
            self.assertIn("Gate status: act1_boss_gate PASS", ai_handoff["assignment_prompt"])
            self.assertIn("Latest run acceptance:", ai_handoff["assignment_prompt"])
            self.assertIn('"accepted":true', ai_handoff["assignment_prompt"])
            self.assertIn("Next probe goal:", ai_handoff["assignment_prompt"])
            self.assertIn('"scope":"gate_already_passed"', ai_handoff["assignment_prompt"])
            self.assertIn("Shadow advice provenance:", ai_handoff["assignment_prompt"])
            self.assertIn("Shadow label quality:", ai_handoff["assignment_prompt"])
            self.assertIn("Combat label replay audit:", ai_handoff["assignment_prompt"])
            if triage["gate_data_quality"]:
                self.assertIn("Gate data quality:", ai_handoff["assignment_prompt"])
            self.assertIn(f"combat_label_replay_audit_path: {combat_label_replay_audit_path}", ai_handoff["assignment_prompt"])
            self.assertIn(f"artifact_manifest_path: {artifact_manifest_path}", ai_handoff["assignment_prompt"])
            self.assertEqual((handoff_dir / "ai_agent.txt").read_text(encoding="utf-8").strip(), ai_handoff["assignment_prompt"])
            self.assertEqual(ai_handoff["queue_count"], 2)
            self.assertEqual(ai_handoff["first_item"]["action"], "inspect_potion_planning")
            self.assertEqual(ai_handoff["next_handoff"]["primary_worker"]["owner"], "ai_agent")
            self.assertEqual(ai_handoff["artifact_paths"]["manifest_path"], str(manifest_path))
            runner_handoff = json.loads((handoff_dir / "runner_agent.json").read_text(encoding="utf-8"))
            self.assertIn("Agent: runner_agent", runner_handoff["assignment_prompt"])
            self.assertIn("First task: collect_next_validation_batch", runner_handoff["assignment_prompt"])
            self.assertIn('"scope":"gate_already_passed"', runner_handoff["assignment_prompt"])
            self.assertIn('"accepted":true', runner_handoff["assignment_prompt"])
            self.assertIn("Requires live MCP ownership: True", runner_handoff["assignment_prompt"])
            self.assertIn("Offline refresh command:", runner_handoff["assignment_prompt"])
            self.assertIn("offline_refresh_command.resolved_argv:", runner_handoff["assignment_prompt"])
            self.assertEqual(
                (handoff_dir / "runner_agent.txt").read_text(encoding="utf-8").strip(),
                runner_handoff["assignment_prompt"],
            )
            handoff_index = json.loads(handoff_index_path.read_text(encoding="utf-8"))
            self.assertEqual(handoff_index["primary_worker"]["owner"], "ai_agent")
            self.assertEqual(handoff_index["primary_worker_prompt_path"], str(handoff_dir / "ai_agent.txt"))
            self.assertEqual(handoff_index["coordinator"]["owner"], "main_agent")
            self.assertEqual(handoff_index["coordinator_prompt_path"], str(handoff_dir / "main_agent.txt"))
            self.assertEqual(handoff_index["classification_reasons"], triage["classification_reasons"])
            self.assertEqual(handoff_index["validation"], triage["validation"])
            self.assertEqual(handoff_index["action_recovery"], triage["action_recovery"])
            self.assertEqual(handoff_index["terminal_recovery"], triage["terminal_recovery"])
            self.assertEqual(handoff_index["gate_data_quality"], triage["gate_data_quality"])
            self.assertEqual(handoff_index["gate_status_line"], triage["gate_status_line"])
            self.assertEqual(handoff_index["gate_progress"], triage["gate_progress"])
            self.assertEqual(handoff_index["gate_remaining_progress"], triage["gate_remaining_progress"])
            self.assertEqual(
                handoff_index["gate_primary_remaining_progress"],
                triage["gate_primary_remaining_progress"],
            )
            self.assertEqual(handoff_index["next_probe_goal"], triage["next_probe_goal"])
            self.assertEqual(handoff_index["latest_run_acceptance"], triage["latest_run_acceptance"])
            self.assertEqual(handoff_index["agent_queue_actions"], triage["agent_queue_actions"])
            self.assertEqual(handoff_index["shadow_advice_provenance"]["resolved_file_count"], 4)
            self.assertEqual(handoff_index["shadow_label_quality"], triage["shadow_label_quality"])
            self.assertEqual(handoff_index["combat_label_replay_audit"], triage["combat_label_replay_audit"])
            next_prompt = next_handoff_prompt_path.read_text(encoding="utf-8")
            self.assertIn("Handoff summary", next_prompt)
            self.assertIn("Primary worker: ai_agent:inspect_potion_planning", next_prompt)
            self.assertIn("Coordinator: main_agent:promote_to_next_validation_batch", next_prompt)
            self.assertIn("Gate status: act1_boss_gate PASS", next_prompt)
            self.assertIn("Latest run acceptance:", next_prompt)
            self.assertIn("Next probe goal:", next_prompt)
            self.assertIn("Agent queue counts:", next_prompt)
            self.assertIn("Agent queue actions:", next_prompt)
            self.assertIn("Classification reasons: clean_trainable=completed_clean:1", next_prompt)
            self.assertIn("Gate failure evidence:", next_prompt)
            self.assertIn("Combat label replay audit:", next_prompt)
            self.assertIn('"route":1', next_prompt)
            self.assertIn("review_gate_data_quality", next_prompt)
            self.assertIn('"requires_live_mcp_ownership":false', next_prompt)
            self.assertIn("Primary worker prompt", next_prompt)
            self.assertIn("Agent: ai_agent", next_prompt)
            self.assertIn("Coordinator prompt", next_prompt)
            self.assertIn("Agent: main_agent", next_prompt)
            diagnosis = json.loads(diagnosis_path.read_text(encoding="utf-8"))["diagnoses"][0]
            self.assertEqual(diagnosis["next_action"], "inspect_potion_planning")

    def test_run_offline_batch_writes_static_knowledge_gap_report_and_handoff_context(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "unknown_static.jsonl"
            rows = _boss_loss_rows()
            rows[0]["state"]["deck"].append({"name": "Mystery Card", "id": "Mystery_Card"})
            write_jsonl(log, rows)

            summary = run_offline_batch(
                [log],
                output_dir=root / "out",
                name="gap_case",
                knowledge_dir=Path("data") / "static_knowledge",
                min_reached=1,
                min_cleared=0,
                min_pristine_cleared=0,
            )

            gap_path = Path(summary["static_knowledge_gap_report_path"])
            self.assertTrue(gap_path.exists())
            self.assertEqual(summary["artifact_paths"]["static_knowledge_gap_report_path"], str(gap_path))
            gap_report_text = gap_path.read_text(encoding="utf-8")
            gap_report = json.loads(gap_report_text)
            self.assertGreater(gap_report["total_missing_occurrences"], 0)
            self.assertGreater(gap_report["totals"]["cards"]["occurrences"], 0)
            self.assertEqual(gap_report["entities"]["cards"]["items"][0]["entity"], "Mystery_Card")

            triage = summary["batch_triage"]
            self.assertEqual(summary["static_knowledge_gaps"], triage["static_knowledge_gaps"])
            self.assertEqual(triage["static_knowledge_gaps"]["total_missing_occurrences"], gap_report["total_missing_occurrences"])
            self.assertEqual(triage["static_knowledge_gaps"]["entities"]["cards"][0]["entity"], "Mystery_Card")
            gate_quality_item = next(
                item for item in triage["agent_queues"]["ai_agent"] if item["action"] == "review_gate_data_quality"
            )
            self.assertEqual(gate_quality_item["static_knowledge_gaps"], triage["static_knowledge_gaps"])
            self.assertEqual(
                gate_quality_item["artifact_paths"]["static_knowledge_gap_report_path"],
                str(gap_path),
            )

            ai_handoff = json.loads(Path(summary["agent_handoff_paths"]["ai_agent"]).read_text(encoding="utf-8"))
            self.assertEqual(ai_handoff["static_knowledge_gaps"], triage["static_knowledge_gaps"])
            self.assertIn("Static knowledge gaps:", ai_handoff["assignment_prompt"])
            self.assertIn('"entity":"Mystery_Card"', ai_handoff["assignment_prompt"])
            self.assertIn(f"static_knowledge_gap_report_path: {gap_path}", ai_handoff["assignment_prompt"])

            handoff_index = json.loads(Path(summary["handoff_index_path"]).read_text(encoding="utf-8"))
            self.assertEqual(handoff_index["static_knowledge_gaps"], triage["static_knowledge_gaps"])
            next_prompt = Path(summary["next_handoff_prompt_path"]).read_text(encoding="utf-8")
            self.assertIn("Static knowledge gaps:", next_prompt)
            self.assertIn('"entity":"Mystery_Card"', next_prompt)

            artifact_manifest = json.loads(Path(summary["artifact_manifest_path"]).read_text(encoding="utf-8"))
            self.assertTrue(artifact_manifest["files"]["static_knowledge_gap_report_path"]["exists"])
            self.assertEqual(
                artifact_manifest["files"]["static_knowledge_gap_report_path"]["path"],
                str(gap_path),
            )

    def test_cli_writes_offline_batch_outputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(log, _boss_loss_rows())
            output = root / "out"

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        str(log),
                        "--output-dir",
                        str(output),
                        "--name",
                        "cli-test",
                        "--knowledge-dir",
                        "missing",
                        "--no-knowledge",
                        "--min-reached",
                        "1",
                        "--min-cleared",
                        "0",
                        "--min-pristine-cleared",
                        "0",
                    ]
                )
            self.assertEqual(code, 0)
            output_text = stdout.getvalue()
            self.assertIn("offline_batch_next:", output_text)
            self.assertIn("recommended=promote_to_next_validation_batch", output_text)
            self.assertIn("primary=ai_agent:inspect_potion_planning", output_text)
            self.assertIn("coordinator=main_agent:promote_to_next_validation_batch", output_text)
            self.assertIn("primary_live_mcp_required=false", output_text)
            self.assertIn("any_live_mcp_required=true", output_text)
            summary_path = output / "offline_batch_cli-test.json"
            self.assertTrue(summary_path.exists())
            self.assertTrue((output / "training_manifest_cli-test.json").exists())
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertIn("offline_batch_next:", summary["offline_batch_next_line"])
            self.assertEqual(summary["batch_triage"]["run_count"], 1)
            self.assertEqual(summary["diagnosis_count"], 1)
            self.assertTrue(Path(summary["artifact_manifest_path"]).exists())
            self.assertEqual(summary["artifact_manifest"]["health_source"], "artifact_manifest_path")
            self.assertEqual(summary["artifact_manifest"]["warnings_source"], "artifact_manifest_path")

    def test_run_offline_batch_records_resolved_log_health_for_directory_inputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            first = logs_dir / "a_run.jsonl"
            second = logs_dir / "b_run.jsonl"
            write_jsonl(first, _boss_loss_rows())
            write_jsonl(second, _boss_loss_rows())

            summary = run_offline_batch(
                [logs_dir],
                output_dir=root / "out",
                name="dir_input",
                knowledge_dir=None,
                min_reached=1,
                min_cleared=0,
                min_pristine_cleared=0,
            )

            self.assertEqual(summary["inputs"], [str(logs_dir)])
            self.assertEqual(summary["resolved_logs"], [str(first), str(second)])
            self.assertIn(str(logs_dir), summary["offline_refresh_command"]["argv"])
            self.assertNotIn(str(logs_dir), summary["offline_refresh_command"]["resolved_argv"])
            self.assertIn(str(first), summary["offline_refresh_command"]["resolved_argv"])
            self.assertIn(str(second), summary["offline_refresh_command"]["resolved_argv"])
            self.assertTrue(summary["offline_refresh_command"]["resolved_argv_available"])
            self.assertEqual(summary["offline_refresh_command"]["resolved_log_count"], 2)
            artifact_manifest = json.loads(Path(summary["artifact_manifest_path"]).read_text(encoding="utf-8"))
            self.assertTrue(artifact_manifest["ok"])
            self.assertEqual(artifact_manifest["warnings"], [])
            self.assertEqual(artifact_manifest["inputs"], [str(logs_dir)])
            self.assertEqual(artifact_manifest["input_count"], 1)
            self.assertEqual(artifact_manifest["input_health"][0]["kind"], "directory")
            self.assertEqual(artifact_manifest["input_health"][0]["file_count"], 2)
            self.assertEqual(artifact_manifest["resolved_logs"], [str(first), str(second)])
            self.assertEqual(artifact_manifest["resolved_log_count"], 2)
            self.assertEqual([item["kind"] for item in artifact_manifest["resolved_log_health"]], ["file", "file"])
            self.assertEqual([len(item["sha256"]) for item in artifact_manifest["resolved_log_health"]], [64, 64])
            self.assertEqual(summary["manifest_summary"]["total_logs"], 2)

    def test_run_offline_batch_warns_when_directory_input_resolves_no_logs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "empty_logs"
            logs_dir.mkdir()

            summary = run_offline_batch(
                [logs_dir],
                output_dir=root / "out",
                name="empty_dir",
                knowledge_dir=None,
                min_reached=1,
                min_cleared=0,
                min_pristine_cleared=0,
            )

            artifact_manifest = json.loads(Path(summary["artifact_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["resolved_logs"], [])
            self.assertEqual(summary["manifest_summary"]["total_logs"], 0)
            self.assertFalse(summary["offline_refresh_command"]["resolved_argv_available"])
            self.assertEqual(summary["offline_refresh_command"]["resolved_argv"], [])
            self.assertEqual(summary["offline_refresh_command"]["resolved_log_count"], 0)
            self.assertTrue(artifact_manifest["ok"])
            self.assertEqual(artifact_manifest["warnings"], ["no_resolved_logs"])
            self.assertEqual(artifact_manifest["input_health"][0]["kind"], "directory")
            self.assertEqual(artifact_manifest["input_health"][0]["file_count"], 0)
            self.assertEqual(artifact_manifest["resolved_log_count"], 0)
            self.assertEqual(artifact_manifest["resolved_log_health"], [])
            self.assertEqual(summary["batch_triage"]["recommended_next_action"], "collect_a0_manifest_batch")

    def test_run_offline_batch_writes_diagnosis_for_each_manifest_item(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_log = root / "clean.jsonl"
            empty_log = root / "empty.jsonl"
            write_jsonl(clean_log, _boss_loss_rows())
            empty_log.write_text("", encoding="utf-8")

            summary = run_offline_batch(
                [clean_log, empty_log],
                output_dir=root / "out",
                name="multi",
                knowledge_dir=None,
                min_reached=1,
                min_cleared=0,
                min_pristine_cleared=0,
            )

            diagnoses = json.loads(Path(summary["diagnosis_path"]).read_text(encoding="utf-8"))["diagnoses"]
            self.assertEqual(summary["diagnosis_count"], 2)
            self.assertEqual(len(summary["diagnosis_status_lines"]), 2)
            self.assertEqual([diagnosis["category"] for diagnosis in diagnoses], ["diagnostic_excluded", "clean_trainable"])
            self.assertEqual([diagnosis["path"] for diagnosis in diagnoses], [str(empty_log), str(clean_log)])

    def test_build_batch_triage_keeps_per_run_handoff_actions(self):
        manifest = {
            "summary": {
                "clean_trainable": 1,
                "diagnostic_excluded": 1,
                "infra_blocked": 1,
                "failure_attributions": {
                    "combat_planning": 1,
                    "diagnostic_incomplete": 1,
                    "mcp_execution": 1,
                },
                "shadow_label_quality": {
                    "combat_search_labels": {
                        "total": 2,
                        "trainable": 1,
                        "excluded_from_training": 1,
                        "exclusion_reasons": {"missed_direct_kill": 1},
                    }
                },
                "failure_evidence": {
                    "runs_with_evidence": 3,
                    "by_type": {
                        "action_error": 1,
                        "boss_combat": 1,
                        "mcp_read": 1,
                        "pre_boss": 1,
                        "reason_only": 1,
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
                        "by_event": {"error": 1},
                        "by_diagnostics_status": {"read_failed": 1},
                    },
                    "synthetic_terminals": {
                        "by_source": {"unknown_source": 1},
                        "terminal_outcome_sources": {"synthetic_after_mcp_null": 1},
                    },
                },
            },
            "categories": {
                "clean_trainable": [
                    {
                        "path": "b_clean.jsonl",
                        "victory": False,
                        "failure_attribution": "combat_planning",
                        "validation_grade": "pristine",
                        "validation_evidence": {
                            "act1_boss": {
                                "reached": True,
                                "cleared": False,
                                "enemy_ids": ["Hexaghost"],
                                "last_hp": 4,
                            }
                        },
                    }
                ],
                "diagnostic_excluded": [
                    {
                        "path": "c_diag.jsonl",
                        "reason": "no_terminal_outcome",
                        "failure_attribution": "diagnostic_incomplete",
                        "validation_grade": "diagnostic",
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
                        "path": "a_infra.jsonl",
                        "reason": "chest_screen_stall",
                        "failure_attribution": "mcp_execution",
                        "failure_evidence": {
                            "reason": "chest_screen_stall",
                            "screen_stall": {
                                "screen_type": "CHEST",
                                "floor": 9,
                                "repeat_count": 3,
                                "first_step": 44,
                                "last_step": 46,
                                "last_reward_count": 0,
                                "chest_open": False,
                                "last_actions": [{"action": "choose", "choice_index": 1}],
                            },
                            "mcp_read": {
                                "step": 47,
                                "event": "error",
                                "diagnostics_status": "read_failed",
                                "last_state": {
                                    "screen_type": "CHEST",
                                    "room_phase": "COMPLETE",
                                    "floor": 9,
                                },
                            },
                            "action_error": {
                                "step": 46,
                                "action_status": "preflight_mismatch",
                                "recovered": False,
                                "kind": "chest_choose_to_proceed",
                            },
                            "synthetic_terminal": {
                                "step": 48,
                                "terminal_recovery_attempted": True,
                                "terminal_recovery_succeeded": True,
                                "post_recovery_status": "healthy",
                            },
                            "terminal_outcome_source": "synthetic_after_mcp_null",
                        },
                        "failed_actions": 1,
                        "action_recovery_summary": {
                            "total": 1,
                            "recovered": 0,
                            "unrecovered": 1,
                            "by_status": {"preflight_mismatch": 1},
                            "by_kind": {"chest_choose_to_proceed": 1},
                        },
                    }
                ],
            },
        }

        triage = build_batch_triage(
            manifest,
            diagnosis={"next_action": "preserve_boss_validation_evidence"},
            diagnoses=[
                {
                    "path": "b_clean.jsonl",
                    "next_action": "fix_combat_lethal_priority",
                    "shadow_advice": {
                        "available": True,
                        "path": "shadow_advice",
                        "summary_path": "shadow_advice/summary.json",
                        "shadow_inputs": {
                            "mode": "shadow_rows",
                            "resolved_file_count": 4,
                            "warnings": [],
                        },
                        "categories": {
                            "combat_search": {
                                "rows": 3,
                                "high_risk_count": 1,
                                "watch_count": 0,
                                "model_available": True,
                                "model_load_quality": {
                                    "files": 1,
                                    "rows": 2,
                                    "accepted": 1,
                                    "skipped": 1,
                                    "skip_reasons": {"missed_direct_kill": 1},
                                },
                                "shadow_source_files": ["shadow/combat_search_labels.jsonl"],
                                "shadow_input": {"resolved_file_count": 1},
                                "model_training_source": {
                                    "resolved_file_count": 1,
                                    "source_quality_policy": "usable",
                                },
                                "model_training_source_quality": "usable",
                                "top_signal": {
                                    "score": 1.7,
                                    "advice": "review_missed_lethal",
                                    "floor": 16,
                                    "step": 80,
                                    "source_log": "b_clean.jsonl",
                                    "label_first_card_key": "bash",
                                    "model_top_card_key": "twinstrike",
                                    "loss_delta": 12,
                                    "attacks_removed": 0,
                                    "retaliation_damage": 24,
                                    "label_missed_direct_kill": True,
                                },
                            }
                        },
                    },
                },
                {"path": "c_diag.jsonl", "next_action": "preserve_boss_validation_evidence"},
            ],
            gate_report={
                "data_quality": {
                    "total_manifests": 1,
                    "coverage_manifests": 1,
                    "missing_coverage_manifests": 0,
                    "shadow_feature_rows": 4,
                    "feature_gap_manifests": 1,
                    "feature_zero_manifests": 0,
                    "feature_gaps": {"route_risk:deck_": 1},
                    "feature_zero": {},
                    "shadow_label_rows": 5,
                    "shadow_label_trainable": 3,
                    "shadow_label_excluded": 2,
                    "label_exclusion_manifests": 1,
                    "label_exclusion_reasons": {"missed_direct_kill": 2},
                    "direct_kill_available_labels": 2,
                    "missed_single_card_search_labels": 1,
                    "manifests": [{"manifest_path": "manifest.json", "total_rows": 4}],
                },
                "execution_recovery": {
                    "action_recovery": {
                        "total": 1,
                        "recovered": 0,
                        "unrecovered": 1,
                        "runs_with_action_recovery": 1,
                        "runs_with_recovered_action": 0,
                        "runs_with_unrecovered_action": 1,
                        "by_status": {"preflight_mismatch": 1},
                        "by_kind": {"chest_choose_to_proceed": 1},
                    },
                    "terminal_recovery": {
                        "synthetic_terminals": 1,
                        "attempted": 1,
                        "succeeded": 1,
                    },
                },
                "failure_evidence": {
                    "runs_with_evidence": 2,
                    "by_type": {
                        "action_error": 1,
                        "mcp_read": 2,
                        "screen_stall": 1,
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
                        "terminal_outcome_sources": {"synthetic_after_mcp_null": 1},
                    },
                },
                "gate": {
                    "passed": False,
                    "next_action": "promote_to_next_validation_batch",
                    "next_probe_goal": {
                        "action": "promote_to_next_validation_batch",
                        "summary": "gate passed; promote to the next validation batch",
                        "acceptance_criteria": {
                            "scope": "gate_already_passed",
                            "counts_toward_metric": None,
                            "no_next_validation_required": True,
                        },
                    },
                    "latest_run_acceptance": {
                        "available": True,
                        "accepted": True,
                        "not_required": True,
                        "summary": "gate already passed; latest run acceptance is not required",
                    },
                }
            },
            artifact_paths={
                "manifest_path": "manifest.json",
                "diagnosis_path": "diagnosis.json",
                "gate_path": "gate.json",
                "skipped_path": None,
            },
        )

        self.assertEqual(triage["recommended_next_action"], "fix_execution_layer")
        self.assertEqual(triage["artifact_paths"], {
            "manifest_path": "manifest.json",
            "diagnosis_path": "diagnosis.json",
            "gate_path": "gate.json",
        })
        self.assertEqual(triage["classification_reasons"]["infra_blocked"], {"chest_screen_stall": 1})
        self.assertEqual(
            triage["failure_evidence"],
            {
                "runs_with_evidence": 3,
                "by_type": {
                    "action_error": 1,
                    "boss_combat": 1,
                    "mcp_read": 1,
                    "pre_boss": 1,
                    "reason_only": 1,
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
                    "by_event": {"error": 1},
                    "by_diagnostics_status": {"read_failed": 1},
                },
                "synthetic_terminals": {
                    "by_source": {"unknown_source": 1},
                    "terminal_outcome_sources": {"synthetic_after_mcp_null": 1},
                },
            },
        )
        self.assertEqual(
            triage["terminal_recovery"],
            {"synthetic_terminals": 1, "attempted": 1, "succeeded": 1},
        )
        self.assertEqual(
            triage["action_recovery"],
            {
                "total": 1,
                "recovered": 0,
                "unrecovered": 1,
                "runs_with_action_recovery": 1,
                "runs_with_recovered_action": 0,
                "runs_with_unrecovered_action": 1,
                "by_status": {"preflight_mismatch": 1},
                "by_kind": {"chest_choose_to_proceed": 1},
            },
        )
        self.assertEqual(
            triage["gate_execution_recovery"],
            {
                "action_recovery": {
                    "total": 1,
                    "recovered": 0,
                    "unrecovered": 1,
                    "runs_with_action_recovery": 1,
                    "runs_with_recovered_action": 0,
                    "runs_with_unrecovered_action": 1,
                    "by_status": {"preflight_mismatch": 1},
                    "by_kind": {"chest_choose_to_proceed": 1},
                },
                "terminal_recovery": {
                    "attempted": 1,
                    "succeeded": 1,
                    "synthetic_terminals": 1,
                },
            },
        )
        self.assertEqual(
            triage["gate_data_quality"],
            {
                "total_manifests": 1,
                "coverage_manifests": 1,
                "shadow_feature_rows": 4,
                "feature_gap_manifests": 1,
                "feature_gaps": {"route_risk:deck_": 1},
                "shadow_label_rows": 5,
                "shadow_label_trainable": 3,
                "shadow_label_excluded": 2,
                "label_exclusion_manifests": 1,
                "label_exclusion_reasons": {"missed_direct_kill": 2},
                "direct_kill_available_labels": 2,
                "missed_single_card_search_labels": 1,
            },
        )
        self.assertEqual(
            triage["gate_failure_evidence"],
            {
                "runs_with_evidence": 2,
                "by_type": {
                    "action_error": 1,
                    "mcp_read": 2,
                    "screen_stall": 1,
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
                    "terminal_outcome_sources": {"synthetic_after_mcp_null": 1},
                },
            },
        )
        self.assertEqual(triage["agent_queues"]["main_agent"][0]["artifact_paths"]["gate_path"], "gate.json")
        self.assertEqual(triage["agent_queue_counts"], {
            "runner_agent": 1,
            "engineering_agent": 2,
            "ai_agent": 3,
            "main_agent": 1,
        })
        self.assertEqual(triage["next_handoff"]["primary_worker"]["owner"], "engineering_agent")
        self.assertEqual(triage["next_handoff"]["primary_worker"]["item"]["action"], "fix_execution_layer")
        self.assertEqual(triage["next_handoff"]["by_agent"]["ai_agent"]["item"]["action"], "fix_combat_lethal_priority")
        self.assertEqual(triage["diagnosis_next_action"], "fix_combat_lethal_priority")
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["diagnosis_next_action"],
            "fix_combat_lethal_priority",
        )
        self.assertEqual(triage["next_probe_goal"]["acceptance_criteria"]["scope"], "gate_already_passed")
        self.assertTrue(triage["latest_run_acceptance"]["accepted"])
        self.assertEqual(
            triage["agent_queues"]["runner_agent"][0]["acceptance_criteria"]["scope"],
            "gate_already_passed",
        )
        self.assertEqual(
            triage["agent_queues"]["runner_agent"][0]["gate_execution_recovery"],
            triage["gate_execution_recovery"],
        )
        self.assertEqual(
            triage["agent_queues"]["runner_agent"][0]["gate_data_quality"],
            triage["gate_data_quality"],
        )
        self.assertEqual(
            triage["agent_queues"]["runner_agent"][0]["gate_failure_evidence"],
            triage["gate_failure_evidence"],
        )
        self.assertTrue(triage["agent_queues"]["runner_agent"][0]["latest_run_acceptance"]["not_required"])
        self.assertEqual(triage["agent_queues"]["main_agent"][0]["next_probe_goal"]["action"], "promote_to_next_validation_batch")
        self.assertEqual(triage["agent_queues"]["main_agent"][0]["acceptance_criteria"]["scope"], "gate_already_passed")
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["gate_execution_recovery"],
            triage["gate_execution_recovery"],
        )
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["gate_data_quality"],
            triage["gate_data_quality"],
        )
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["gate_failure_evidence"],
            triage["gate_failure_evidence"],
        )
        self.assertTrue(triage["agent_queues"]["main_agent"][0]["latest_run_acceptance"]["not_required"])
        self.assertEqual(triage["shadow_label_quality"]["combat_search_labels"]["excluded_from_training"], 1)
        self.assertEqual(
            triage["shadow_advice_provenance"]["categories"]["combat_search"]["model_load_quality"]["skip_reasons"],
            {"missed_direct_kill": 1},
        )
        self.assertEqual(
            triage["shadow_advice_provenance"]["categories"]["combat_search"]["model_load_quality"]["skipped"],
            1,
        )
        self.assertEqual(
            triage["shadow_advice_provenance"]["categories"]["combat_search"]["top_signal"]["retaliation_damage"],
            24,
        )
        self.assertEqual(
            triage["shadow_advice_provenance"]["categories"]["combat_search"]["top_signal"]["label_first_card_key"],
            "bash",
        )
        self.assertEqual(triage["run_action_counts"]["fix_combat_lethal_priority"], 1)
        ai_item = next(item for item in triage["agent_queues"]["ai_agent"] if item["action"] == "fix_combat_lethal_priority")
        self.assertEqual(ai_item["count"], 1)
        self.assertEqual(ai_item["reason"], "per_run_diagnosis")
        self.assertEqual(ai_item["paths"], ["b_clean.jsonl"])
        self.assertEqual(
            ai_item["shadow_advice"]["categories"]["combat_search"]["model_load_quality"]["skip_reasons"],
            {"missed_direct_kill": 1},
        )
        self.assertEqual(
            ai_item["shadow_advice"]["categories"]["combat_search"]["top_signals"][0]["retaliation_damage"],
            24,
        )
        self.assertEqual(ai_item["priority"], 0)
        self.assertEqual(ai_item["artifact_paths"]["diagnosis_path"], "diagnosis.json")
        self.assertEqual(ai_item["execution_contract"]["mode"], "offline_strategy_or_shadow_model")
        self.assertIn("replace_live_policy_with_model", ai_item["execution_contract"]["forbidden_operations"])
        gate_quality_item = next(
            item for item in triage["agent_queues"]["ai_agent"] if item["action"] == "review_gate_data_quality"
        )
        self.assertEqual(gate_quality_item["count"], 5)
        self.assertEqual(gate_quality_item["reason"], "gate_data_quality_issues")
        self.assertEqual(
            gate_quality_item["issues"],
            {
                "feature_gap_manifests": 1,
                "label_exclusion_manifests": 1,
                "shadow_label_excluded": 2,
                "missed_single_card_search_labels": 1,
            },
        )
        self.assertEqual(gate_quality_item["gate_data_quality"], triage["gate_data_quality"])
        self.assertEqual(gate_quality_item["artifact_paths"]["manifest_path"], "manifest.json")
        self.assertEqual(gate_quality_item["execution_contract"]["mode"], "offline_strategy_or_shadow_model")
        self.assertFalse(gate_quality_item["execution_contract"]["requires_live_mcp_ownership"])
        engineering_item = next(
            item for item in triage["agent_queues"]["engineering_agent"] if item["action"] == "fix_execution_layer"
        )
        self.assertEqual(engineering_item["count"], 1)
        self.assertEqual(engineering_item["reason"], "per_run_diagnosis")
        self.assertEqual(engineering_item["paths"], ["a_infra.jsonl"])
        self.assertEqual(
            engineering_item["failure_evidence"]["screen_stalls"][0]["screen_type"],
            "CHEST",
        )
        self.assertEqual(
            engineering_item["failure_evidence"]["screen_stalls"][0]["last_reward_count"],
            0,
        )
        self.assertEqual(engineering_item["failure_evidence"]["mcp_reads"][0]["step"], 47)
        self.assertEqual(
            engineering_item["failure_evidence"]["mcp_reads"][0]["last_state"]["screen_type"],
            "CHEST",
        )
        self.assertEqual(engineering_item["failure_evidence"]["action_errors"][0]["kind"], "chest_choose_to_proceed")
        self.assertEqual(engineering_item["failure_evidence"]["synthetic_terminals"][0]["step"], 48)
        self.assertEqual(
            engineering_item["failure_evidence"]["synthetic_terminals"][0]["terminal_outcome_source"],
            "synthetic_after_mcp_null",
        )
        self.assertEqual(engineering_item["priority"], 0)
        self.assertEqual(engineering_item["artifact_paths"]["manifest_path"], "manifest.json")
        self.assertEqual(engineering_item["execution_contract"]["mode"], "offline_code_or_data_infra")
        self.assertIn(
            "control_live_mcp_without_explicit_ownership",
            engineering_item["execution_contract"]["forbidden_operations"],
        )
        self.assertEqual(
            [(run["path"], run["owner"], run["action"]) for run in triage["runs"]],
            [
                ("a_infra.jsonl", "engineering_agent", "fix_execution_layer"),
                ("b_clean.jsonl", "ai_agent", "fix_combat_lethal_priority"),
                ("c_diag.jsonl", "main_agent", "preserve_boss_validation_evidence"),
            ],
        )
        self.assertEqual(triage["runs"][0]["failure_evidence"]["screen_stall"]["screen_type"], "CHEST")
        self.assertEqual(triage["runs"][0]["failure_evidence"]["screen_stall"]["last_reward_count"], 0)
        self.assertEqual(triage["runs"][0]["failure_evidence"]["mcp_read"]["last_state"]["floor"], 9)
        self.assertEqual(triage["runs"][0]["failure_evidence"]["action_error"]["kind"], "chest_choose_to_proceed")
        self.assertTrue(
            triage["runs"][0]["failure_evidence"]["synthetic_terminal"]["terminal_recovery_succeeded"]
        )
        self.assertEqual(triage["runs"][0]["failure_evidence"]["terminal_outcome_source"], "synthetic_after_mcp_null")
        self.assertEqual(triage["runs"][1]["diagnosis_next_action"], "fix_combat_lethal_priority")
        self.assertTrue(triage["runs"][2]["act1_boss"]["prefix_pristine_clear"])

    def test_build_batch_triage_uses_strongest_per_run_diagnosis_for_batch_action(self):
        manifest = {
            "summary": {
                "clean_trainable": 2,
                "diagnostic_excluded": 0,
                "infra_blocked": 0,
                "failure_attributions": {
                    "potion_planning": 1,
                    "combat_planning": 1,
                },
            },
            "categories": {
                "clean_trainable": [
                    {
                        "path": "a_potion.jsonl",
                        "victory": False,
                        "failure_attribution": "potion_planning",
                    },
                    {
                        "path": "b_combat.jsonl",
                        "victory": False,
                        "failure_attribution": "combat_planning",
                    },
                ]
            },
        }

        triage = build_batch_triage(
            manifest,
            diagnosis={"path": "a_potion.jsonl", "next_action": "inspect_potion_planning"},
            diagnoses=[
                {"path": "a_potion.jsonl", "next_action": "inspect_potion_planning"},
                {"path": "b_combat.jsonl", "next_action": "fix_combat_lethal_priority"},
            ],
        )

        self.assertEqual(triage["diagnosis_next_action"], "fix_combat_lethal_priority")
        self.assertEqual(triage["recommended_next_action"], "fix_combat_lethal_priority")
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["diagnosis_next_action"],
            "fix_combat_lethal_priority",
        )
        self.assertEqual(triage["next_handoff"]["primary_worker"]["item"]["action"], "fix_combat_lethal_priority")

    def test_build_batch_triage_demotes_combat_label_fix_when_replay_resolved(self):
        manifest = {
            "summary": {
                "clean_trainable": 1,
                "diagnostic_excluded": 0,
                "infra_blocked": 0,
                "failure_attributions": {"combat_planning": 1},
                "shadow_label_quality": {
                    "combat_search_labels": {
                        "total": 2,
                        "trainable": 1,
                        "excluded_from_training": 1,
                        "exclusion_reasons": {"missed_direct_kill": 1},
                    }
                },
            },
            "categories": {
                "clean_trainable": [
                    {
                        "path": "boss_missed_lethal.jsonl",
                        "victory": False,
                        "failure_attribution": "combat_planning",
                    }
                ]
            },
        }

        triage = build_batch_triage(
            manifest,
            diagnoses=[
                {
                    "path": "boss_missed_lethal.jsonl",
                    "next_action": "fix_combat_lethal_priority",
                }
            ],
            gate_report={
                "data_quality": {
                    "shadow_label_excluded": 1,
                    "label_exclusion_manifests": 1,
                    "missed_single_card_search_labels": 0,
                }
            },
            combat_label_replay_audit_report={
                "status_line": "combat_label_replay_audit files=1 excluded=1 replayed=1 matches_label=1 matches_actual=0 other=0",
                "resolved_file_count": 1,
                "excluded_rows": 1,
                "replayed_rows": 1,
                "current_policy_matches_label": 1,
                "current_policy_matches_actual": 0,
                "current_policy_other": 0,
                "missing_source_log": 0,
                "missing_source_step": 0,
                "policy_errors": 0,
                "replay_outcomes": {"current_policy_matches_label": 1},
            },
        )

        self.assertEqual(triage["diagnosis_next_action"], "verify_current_policy_replay_for_combat_labels")
        self.assertEqual(triage["recommended_next_action"], "verify_current_policy_replay_for_combat_labels")
        self.assertEqual(
            triage["gate_data_quality_replay_resolved_issues"]["issues"],
            {"label_exclusion_manifests": 1, "shadow_label_excluded": 1},
        )
        ai_actions = triage["agent_queue_actions"]["ai_agent"]
        self.assertEqual(ai_actions[0]["action"], "verify_current_policy_replay_for_combat_labels")
        self.assertEqual(ai_actions[0]["original_action"], "fix_combat_lethal_priority")
        self.assertTrue(ai_actions[0]["current_policy_replay_resolved"])
        self.assertEqual(ai_actions[1]["action"], "review_excluded_combat_search_labels")
        self.assertEqual(ai_actions[1]["reason"], "current_policy_replay_resolved")
        self.assertEqual(ai_actions[1]["priority"], 3)
        self.assertTrue(ai_actions[1]["current_policy_replay_resolved"])
        verify_item = next(
            item
            for item in triage["agent_queues"]["ai_agent"]
            if item["action"] == "verify_current_policy_replay_for_combat_labels"
        )
        self.assertEqual(verify_item["original_action"], "fix_combat_lethal_priority")
        self.assertEqual(verify_item["reason"], "per_run_diagnosis_replay_resolved")
        self.assertEqual(verify_item["priority"], 2)
        self.assertTrue(verify_item["combat_label_replay_resolution"]["current_policy_resolved"])

    def test_build_batch_triage_queues_unmet_gate_collection_goal_for_runner(self):
        manifest = {
            "summary": {
                "clean_trainable": 1,
                "diagnostic_excluded": 0,
                "infra_blocked": 0,
                "failure_attributions": {"combat_planning": 1},
            },
            "categories": {
                "clean_trainable": [
                    {
                        "path": "boss_loss.jsonl",
                        "victory": False,
                        "failure_attribution": "combat_planning",
                        "validation_grade": "pristine",
                        "validation_evidence": {
                            "act1_boss": {
                                "reached": True,
                                "cleared": False,
                                "enemy_ids": ["TheGuardian"],
                                "last_hp": 18,
                            }
                        },
                    }
                ]
            },
        }

        triage = build_batch_triage(
            manifest,
            gate_report={
                "gate": {
                    "passed": False,
                    "next_action": "collect_pristine_act1_boss_clears",
                    "progress": {
                        "act1_boss_reached": {"current": 1, "required": 1, "deficit": 0, "ok": True},
                        "act1_boss_cleared": {"current": 0, "required": 1, "deficit": 1, "ok": False},
                        "pristine_act1_boss_cleared": {
                            "current": 0,
                            "required": 1,
                            "deficit": 1,
                            "ok": False,
                        },
                    },
                    "remaining_progress": [
                        {"metric": "act1_boss_cleared", "current": 0, "required": 1, "deficit": 1, "ok": False},
                        {
                            "metric": "pristine_act1_boss_cleared",
                            "current": 0,
                            "required": 1,
                            "deficit": 1,
                            "ok": False,
                        },
                    ],
                    "primary_remaining_progress": {
                        "metric": "act1_boss_cleared",
                        "current": 0,
                        "required": 1,
                        "deficit": 1,
                        "ok": False,
                    },
                    "next_probe_goal": {
                        "action": "collect_pristine_act1_boss_clears",
                        "metric": "pristine_act1_boss_cleared",
                        "deficit": 1,
                        "summary": "collect 1 more pristine Act 1 boss clear",
                        "acceptance_criteria": {
                            "scope": "next_matching_run",
                            "counts_toward_metric": "pristine_act1_boss_cleared",
                            "accepted_manifest_categories": ["clean_trainable", "diagnostic_excluded"],
                            "rejected_manifest_categories": ["infra_blocked"],
                            "act1_boss": {
                                "reached": True,
                                "cleared": True,
                                "prefix_pristine_clear": True,
                                "prefix_blockers": [],
                            },
                        },
                    },
                    "latest_run_acceptance": {
                        "available": True,
                        "accepted": False,
                        "metric": "pristine_act1_boss_cleared",
                        "run_path": "boss_loss.jsonl",
                        "blockers": ["act1_boss_not_cleared"],
                    },
                }
            },
        )

        runner_item = triage["agent_queues"]["runner_agent"][0]
        self.assertEqual(triage["recommended_next_action"], "collect_pristine_act1_boss_clears")
        self.assertEqual(
            triage["gate_progress"]["act1_boss_cleared"],
            {"current": 0, "required": 1, "deficit": 1, "ok": False},
        )
        self.assertEqual(triage["gate_remaining_progress"][0]["metric"], "act1_boss_cleared")
        self.assertEqual(triage["gate_primary_remaining_progress"]["metric"], "act1_boss_cleared")
        self.assertEqual(
            triage["agent_queue_actions"]["runner_agent"][0],
            {
                "action": "collect_pristine_act1_boss_clears",
                "reason": "act1_boss_gate_needs_pristine_clear",
                "priority": 0,
                "execution_mode": "live_mcp_gated_collection",
                "requires_live_mcp_ownership": True,
                "gate_metric": "pristine_act1_boss_cleared",
                "gate_deficit": 1,
                "acceptance_scope": "next_matching_run",
                "counts_toward_metric": "pristine_act1_boss_cleared",
                "latest_run_accepted": False,
                "latest_run_blockers": ["act1_boss_not_cleared"],
            },
        )
        self.assertEqual(runner_item["action"], "collect_pristine_act1_boss_clears")
        self.assertEqual(runner_item["reason"], "act1_boss_gate_needs_pristine_clear")
        self.assertEqual(runner_item["acceptance_criteria"]["counts_toward_metric"], "pristine_act1_boss_cleared")
        self.assertEqual(runner_item["acceptance_criteria"]["act1_boss"]["prefix_blockers"], [])
        self.assertFalse(runner_item["latest_run_acceptance"]["accepted"])
        self.assertEqual(runner_item["latest_run_acceptance"]["blockers"], ["act1_boss_not_cleared"])
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["acceptance_criteria"]["counts_toward_metric"],
            "pristine_act1_boss_cleared",
        )

    def test_pristine_gate_execution_recovery_queues_engineering_before_collection(self):
        manifest = {
            "summary": {"clean_trainable": 0, "diagnostic_excluded": 0, "infra_blocked": 0},
            "categories": {"clean_trainable": [], "diagnostic_excluded": [], "infra_blocked": []},
        }

        triage = build_batch_triage(
            manifest,
            gate_report={
                "execution_recovery": {
                    "action_recovery": {
                        "total": 1,
                        "recovered": 1,
                        "unrecovered": 0,
                        "by_kind": {"unavailable_action": 1},
                        "examples": [
                            {
                                "source_log": "probe104.jsonl",
                                "source_category": "clean_trainable",
                                "step": 218,
                                "action_status": "preflight_mismatch",
                                "recovered": True,
                                "kind": "unavailable_action",
                                "actions": [{"action": "choose", "choice_index": 1}],
                                "available_commands": ["proceed"],
                            }
                        ],
                    }
                },
                "failure_evidence": {
                    "runs_with_evidence": 1,
                    "by_type": {"action_error": 1},
                    "action_errors": {"by_kind": {"unavailable_action": 1}},
                },
                "gate": {
                    "passed": False,
                    "next_action": "collect_pristine_act1_boss_clears",
                    "next_probe_goal": {
                        "action": "collect_pristine_act1_boss_clears",
                        "metric": "pristine_act1_boss_cleared",
                        "deficit": 1,
                        "acceptance_criteria": {
                            "scope": "next_matching_run",
                            "counts_toward_metric": "pristine_act1_boss_cleared",
                        },
                    },
                    "latest_run_acceptance": {
                        "available": True,
                        "accepted": False,
                        "blockers": ["prefix_blocker:recovered_action_race"],
                    },
                },
            },
        )

        engineering_item = triage["agent_queues"]["engineering_agent"][0]
        self.assertEqual(engineering_item["action"], "stabilize_pristine_gate_execution")
        self.assertEqual(engineering_item["reason"], "pristine_gate_execution_recovery")
        self.assertEqual(engineering_item["priority"], 0)
        self.assertEqual(engineering_item["gate_execution_recovery"], triage["gate_execution_recovery"])
        self.assertEqual(
            engineering_item["gate_execution_recovery"]["action_recovery"]["examples"][0]["source_log"],
            "probe104.jsonl",
        )
        self.assertEqual(engineering_item["gate_failure_evidence"], triage["gate_failure_evidence"])
        self.assertEqual(triage["next_handoff"]["primary_worker"]["owner"], "engineering_agent")
        self.assertEqual(
            triage["next_handoff"]["primary_worker"]["item"]["action"],
            "stabilize_pristine_gate_execution",
        )
        self.assertEqual(triage["agent_queues"]["runner_agent"][0]["action"], "collect_pristine_act1_boss_clears")

    def test_pristine_gate_execution_recovery_demotes_when_current_runner_resolves_examples(self):
        manifest = {
            "summary": {"clean_trainable": 0, "diagnostic_excluded": 0, "infra_blocked": 0},
            "categories": {"clean_trainable": [], "diagnostic_excluded": [], "infra_blocked": []},
        }

        triage = build_batch_triage(
            manifest,
            gate_report={
                "execution_recovery": {
                    "action_recovery": {
                        "total": 2,
                        "recovered": 2,
                        "unrecovered": 0,
                        "by_kind": {"invalid_command": 1, "unavailable_action": 1},
                        "examples": [
                            {
                                "source_log": "probe104.jsonl",
                                "step": 218,
                                "action_status": "preflight_mismatch",
                                "recovered": True,
                                "kind": "unavailable_action",
                                "actions": [{"action": "choose", "choice_index": 1}],
                                "available_commands": ["proceed"],
                                "last_state": {"screen_type": "MAP", "room_phase": "COMPLETE", "floor": 15},
                            },
                            {
                                "source_log": "probe106.jsonl",
                                "step": 257,
                                "action_status": "recoverable_error",
                                "recovered": True,
                                "kind": "invalid_command",
                                "actions": [{"action": "proceed"}],
                                "last_error": "Invalid command: proceed. Possible commands: [choose, key, click, wait, save, state]",
                                "last_state": {"screen_type": "CHEST", "room_phase": "COMPLETE", "floor": 17},
                            },
                        ],
                    }
                },
                "gate": {
                    "passed": False,
                    "next_action": "collect_pristine_act1_boss_clears",
                    "next_probe_goal": {
                        "action": "collect_pristine_act1_boss_clears",
                        "metric": "pristine_act1_boss_cleared",
                        "deficit": 1,
                        "acceptance_criteria": {
                            "scope": "next_matching_run",
                            "counts_toward_metric": "pristine_act1_boss_cleared",
                        },
                    },
                    "latest_run_acceptance": {
                        "available": True,
                        "accepted": False,
                        "blockers": ["prefix_blocker:recovered_action_race"],
                    },
                },
            },
        )

        engineering_item = triage["agent_queues"]["engineering_agent"][0]
        self.assertEqual(engineering_item["action"], "verify_current_runner_for_gate_execution_recovery")
        self.assertEqual(engineering_item["reason"], "current_runner_rewrite_resolved_gate_execution_recovery")
        self.assertEqual(engineering_item["priority"], 2)
        resolution = engineering_item["gate_execution_recovery_resolution"]
        self.assertTrue(resolution["current_runner_resolved"])
        self.assertEqual(resolution["action_recovery_total"], 2)
        self.assertEqual(
            resolution["by_resolution"],
            {
                "current_runner_rewrites_chest_proceed_to_choose": 1,
                "current_runner_waits_stale_map_choose_proceed": 1,
            },
        )
        self.assertEqual(triage["agent_queue_actions"]["engineering_agent"][0]["current_runner_recovery_resolved"], True)
        self.assertEqual(triage["next_handoff"]["primary_worker"]["owner"], "runner_agent")
        self.assertEqual(triage["next_handoff"]["primary_worker"]["item"]["action"], "collect_pristine_act1_boss_clears")

    def test_build_batch_triage_queues_gate_improvement_goal_for_ai_agent(self):
        manifest = {
            "summary": {
                "clean_trainable": 1,
                "diagnostic_excluded": 0,
                "infra_blocked": 0,
                "failure_attributions": {"combat_planning": 1},
            },
            "categories": {
                "clean_trainable": [
                    {
                        "path": "guardian_loss.jsonl",
                        "victory": False,
                        "failure_attribution": "combat_planning",
                        "validation_grade": "pristine",
                        "validation_evidence": {
                            "act1_boss": {
                                "reached": True,
                                "cleared": False,
                                "enemy_ids": ["TheGuardian"],
                                "last_hp": 11,
                            }
                        },
                    }
                ]
            },
        }

        triage = build_batch_triage(
            manifest,
            gate_report={
                "gate": {
                    "passed": False,
                    "next_action": "improve_act1_boss_combat",
                    "next_probe_goal": {
                        "action": "improve_act1_boss_combat",
                        "metric": "act1_boss_cleared",
                        "deficit": 1,
                        "summary": "collect 1 more Act 1 boss clear",
                        "acceptance_criteria": {
                            "scope": "next_matching_run",
                            "counts_toward_metric": "act1_boss_cleared",
                            "accepted_manifest_categories": ["clean_trainable", "diagnostic_excluded"],
                            "rejected_manifest_categories": ["infra_blocked"],
                            "act1_boss": {"reached": True, "cleared": True},
                        },
                    },
                    "latest_run_acceptance": {
                        "available": True,
                        "accepted": False,
                        "metric": "act1_boss_cleared",
                        "run_path": "guardian_loss.jsonl",
                        "blockers": ["act1_boss_not_cleared"],
                    },
                }
            },
        )

        ai_gate_item = next(
            item for item in triage["agent_queues"]["ai_agent"] if item["action"] == "improve_act1_boss_combat"
        )
        self.assertEqual(triage["recommended_next_action"], "improve_act1_boss_combat")
        self.assertEqual(ai_gate_item["reason"], "act1_boss_gate_needs_clear")
        self.assertEqual(ai_gate_item["priority"], 0)
        self.assertEqual(ai_gate_item["acceptance_criteria"]["counts_toward_metric"], "act1_boss_cleared")
        self.assertEqual(ai_gate_item["acceptance_criteria"]["act1_boss"], {"reached": True, "cleared": True})
        self.assertFalse(ai_gate_item["latest_run_acceptance"]["accepted"])
        self.assertEqual(ai_gate_item["latest_run_acceptance"]["blockers"], ["act1_boss_not_cleared"])
        self.assertEqual(
            triage["agent_queues"]["main_agent"][0]["acceptance_criteria"]["counts_toward_metric"],
            "act1_boss_cleared",
        )


def _boss_loss_rows() -> list[dict]:
    return [
        {
            "step": 1,
            "state": {
                "screen_type": "MAP",
                "room_phase": "COMPLETE",
                "floor": 15,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 66,
                "max_hp": 80,
                "deck": ["Strike_R", "Defend_R", "Bash"],
                "potions": [],
                "relics": ["Burning Blood"],
                "boss_available": True,
                "route_evaluation": {
                    "options": [
                        {
                            "choice_index": 1,
                            "symbol": "B",
                            "score": 100,
                            "lookahead": {
                                "forced_elite_within_3": False,
                                "forced_combat_within_2": False,
                                "nearest_rest": 0,
                                "nearest_shop": None,
                                "readiness_penalty": 0,
                                "readiness_flags": [],
                                "act2_route_flags": [],
                            },
                        }
                    ]
                },
            },
            "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
        },
        {
            "step": 2,
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
                            "name": "Defend",
                            "id": "Defend_R",
                            "type": "SKILL",
                            "cost": 1,
                            "block": 5,
                            "is_playable": True,
                        }
                    ],
                    "monsters": [{"id": "Hexaghost", "hp": 20, "max_hp": 250}],
                },
            },
            "decision": {"actions": [{"action": "play_card", "card_index": 1}]},
        },
        {
            "step": 3,
            "state": {
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False, "score": 42},
            },
        },
    ]


if __name__ == "__main__":
    unittest.main()
