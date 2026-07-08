import json
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.run_status import build_status_payload, main, summarize_log
from slay_ai.static_knowledge import StaticKnowledge


def write_lines(path: Path, rows: list[dict], *, partial_tail: str | None = None) -> None:
    text = "\n".join(json.dumps(row) for row in rows) + "\n"
    if partial_tail is not None:
        text += partial_tail
    path.write_text(text, encoding="utf-8")


def combat_state(step_floor: int = 16) -> dict:
    return {
        "in_game": True,
        "screen_type": "NONE",
        "room_phase": "COMBAT",
        "floor": step_floor,
        "act": 1,
        "class": "IRONCLAD",
        "ascension_level": 0,
        "current_hp": 62,
        "max_hp": 80,
        "gold": 62,
        "combat": {
            "turn": 2,
            "incoming_damage": 32,
            "player": {"current_energy": 3},
            "hand_cards": [{"id": "Bash", "is_playable": True}],
            "monsters": [
                {
                    "id": "TheGuardian",
                    "name": "The Guardian",
                    "hp": 200,
                    "max_hp": 240,
                    "intent": "ATTACK",
                }
            ],
        },
    }


class RunStatusTests(unittest.TestCase):
    def test_summarizes_live_log_and_ignores_incomplete_tail_line(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            write_lines(
                path,
                [
                    {
                        "step": 1,
                        "state": {
                            "in_game": True,
                            "screen_type": "MAP",
                            "room_phase": "COMPLETE",
                            "floor": 8,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 70,
                            "max_hp": 80,
                        },
                    },
                    {"step": 1, "event": "action_result", "action_status": "ok"},
                ],
                partial_tail='{"step": 2, "state": ',
            )

            status = summarize_log(path)

        self.assertEqual(status["state"], "running")
        self.assertEqual(status["records"], 2)
        self.assertEqual(status["skipped_malformed_tail_lines"], 1)
        self.assertEqual(status["latest"]["floor"], 8)
        self.assertIn("category=diagnostic_excluded/no_terminal_outcome", status["status_line"])
        self.assertIn("skipped_tail=1", status["status_line"])

    def test_summarizes_boss_evidence_and_recovered_action_race(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            write_lines(
                path,
                [
                    {"step": 18, "state": combat_state()},
                    {
                        "step": 18,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight unavailable action(s): ['play_card']",
                    },
                ],
            )

            status = summarize_log(path)

        self.assertEqual(status["classification"]["recovered_actions"], 1)
        self.assertEqual(status["classification"]["validation_grade"], "diagnostic")
        self.assertTrue(status["act1_boss"]["reached"])
        self.assertFalse(status["act1_boss"]["cleared"])
        self.assertEqual(status["latest"]["combat"]["enemies"][0]["id"], "TheGuardian")
        self.assertIn("recovered=1", status["status_line"])
        self.assertIn("recovery=unavailable_action:1", status["status_line"])
        self.assertIn("failure=diagnostic_incomplete", status["status_line"])
        self.assertIn("recovered_action_race", status["status_line"])
        self.assertIn("act1_boss=reached:TheGuardian", status["status_line"])
        self.assertIn("player_hp=62", status["status_line"])

    def test_summarizes_act1_boss_prefix_pristine_clear(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            post_boss_state = {
                "in_game": True,
                "screen_type": "MAP",
                "room_phase": "COMPLETE",
                "floor": 17,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 58,
                "max_hp": 80,
                "gold": 74,
            }
            write_lines(
                path,
                [
                    {"step": 20, "state": combat_state()},
                    {"step": 20, "event": "action_result", "action_status": "ok"},
                    {"step": 21, "state": post_boss_state},
                ],
            )

            status = summarize_log(path)

        self.assertTrue(status["act1_boss"]["cleared"])
        self.assertTrue(status["act1_boss"]["prefix_pristine_clear"])
        self.assertEqual(status["act1_boss"]["prefix_blockers"], [])
        self.assertEqual(status["act1_boss"]["clear_step"], 21)
        self.assertIn("act1_boss=cleared:TheGuardian", status["status_line"])
        self.assertIn("clear_step=21", status["status_line"])
        self.assertIn("prefix_pristine=true", status["status_line"])

    def test_status_line_surfaces_synthetic_terminal_evidence(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.jsonl"
            terminal_state = {
                "in_game": True,
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False, "source": "synthetic_after_mcp_null"},
            }
            write_lines(
                path,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 21,
                        "event": "synthetic_terminal_state",
                        "last_error": "read_state_failed: Internal error: null",
                        "previous_error": "Internal error: null",
                        "terminal_recovery_attempted": True,
                        "terminal_recovery_succeeded": True,
                        "post_recovery_diagnostics": {"status": "healthy"},
                    },
                    {"step": 21, "state": terminal_state},
                ],
            )

            status = summarize_log(path)

        self.assertEqual(status["state"], "terminal")
        self.assertIn("category=clean_trainable/completed_clean", status["status_line"])
        self.assertIn("synthetic_after_mcp_null", status["status_line"])
        self.assertIn(
            "evidence=synthetic_terminal@21[outcome=synthetic_after_mcp_null,recover=ok,post=healthy]",
            status["status_line"],
        )

    def test_status_line_surfaces_mcp_read_evidence(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "read_failed.jsonl"
            write_lines(
                path,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 21,
                        "event": "error",
                        "error": "read_state_failed: Cannot reach MCPTheSpire at http://127.0.0.1:8080/mcp",
                    },
                ],
            )

            status = summarize_log(path)

        self.assertEqual(status["classification"]["reason"], "mcp_unreachable")
        self.assertIn("category=infra_blocked/mcp_unreachable", status["status_line"])
        self.assertIn("failure=mcp_execution", status["status_line"])
        self.assertIn("evidence=mcp_read@21[NONE/F16/COMBAT]", status["status_line"])

    def test_status_line_surfaces_reward_screen_stall_details(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "boss_reward_stall.jsonl"
            boss_reward = {
                "in_game": True,
                "screen_type": "BOSS_REWARD",
                "room_phase": "COMPLETE",
                "floor": 17,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 58,
                "max_hp": 80,
                "screen_state": {
                    "relics": [
                        {"id": "Black Blood", "name": "Black Blood"},
                        {"id": "Coffee Dripper", "name": "Coffee Dripper"},
                        {"id": "Sozu", "name": "Sozu"},
                    ]
                },
            }
            write_lines(
                path,
                [
                    {
                        "step": 10,
                        "state": boss_reward,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 11,
                        "state": boss_reward,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 12,
                        "state": boss_reward,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                ],
            )

            status = summarize_log(path)
            payload = build_status_payload([path])

        self.assertEqual(status["classification"]["category"], "infra_blocked")
        self.assertEqual(status["classification"]["reason"], "boss_reward_screen_stall")
        self.assertIn("category=infra_blocked/boss_reward_screen_stall", status["status_line"])
        self.assertIn(
            "screen_stall=BOSS_REWARDx3[F17,steps=10->12,relics=3,action=choose:1]",
            status["status_line"],
        )
        self.assertEqual(payload["summary"]["failure_evidence_kinds"], {"screen_stall": 1})
        self.assertEqual(
            payload["summary"]["screen_stalls"],
            {
                "by_screen": {"BOSS_REWARD": 1},
                "by_reason": {"boss_reward_screen_stall": 1},
            },
        )
        source_evidence = payload["summary"]["recommended_source_runs"][0]["failure_evidence"]
        self.assertEqual(source_evidence["screen_stall"]["screen_type"], "BOSS_REWARD")
        self.assertEqual(source_evidence["screen_stall"]["last_relic_count"], 3)
        self.assertEqual(source_evidence["screen_stall"]["last_action"], "choose:1")
        self.assertIn('"screen_type":"BOSS_REWARD"', payload["summary"]["recommended_assignment"]["assignment_prompt"])
        self.assertIn("evidence=screen_stall:1", payload["summary"]["status_line"])
        self.assertIn("screen_stalls=BOSS_REWARD:1", payload["summary"]["status_line"])
        self.assertIn("screen_reasons=boss_reward_screen_stall:1", payload["summary"]["status_line"])

    def test_build_status_payload_rolls_up_classification_and_validation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            synthetic = root / "synthetic.jsonl"
            read_failed = root / "read_failed.jsonl"
            recovered_clear = root / "recovered_clear.jsonl"
            terminal_state = {
                "in_game": True,
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False, "source": "synthetic_after_mcp_null"},
            }
            post_boss_state = {
                "in_game": True,
                "screen_type": "MAP",
                "room_phase": "COMPLETE",
                "floor": 17,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 58,
                "max_hp": 80,
            }
            write_lines(
                synthetic,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 21,
                        "event": "synthetic_terminal_state",
                        "last_error": "read_state_failed: Internal error: null",
                        "previous_error": "Internal error: null",
                        "terminal_recovery_attempted": True,
                        "terminal_recovery_succeeded": True,
                        "post_recovery_diagnostics": {"status": "healthy"},
                    },
                    {"step": 21, "state": terminal_state},
                ],
            )
            write_lines(
                read_failed,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 21,
                        "event": "error",
                        "error": "read_state_failed: Cannot reach MCPTheSpire at http://127.0.0.1:8080/mcp",
                    },
                ],
            )
            write_lines(
                recovered_clear,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 20,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale target_index: target_index=2 outside current monster count 1.",
                    },
                    {"step": 21, "state": post_boss_state},
                ],
            )

            payload = build_status_payload([root])

        summary = payload["summary"]
        self.assertEqual(summary["input_count"], 1)
        self.assertEqual(summary["resolved_log_count"], 3)
        self.assertEqual(summary["state_counts"], {"running": 2, "terminal": 1})
        self.assertEqual(summary["classification_counts"]["clean_trainable"], 1)
        self.assertEqual(summary["classification_counts"]["diagnostic_excluded"], 1)
        self.assertEqual(summary["classification_counts"]["infra_blocked"], 1)
        self.assertEqual(summary["classification_reasons"]["clean_trainable"], {"completed_clean": 1})
        self.assertEqual(summary["classification_reasons"]["diagnostic_excluded"], {"no_terminal_outcome": 1})
        self.assertEqual(summary["classification_reasons"]["infra_blocked"], {"mcp_unreachable": 1})
        self.assertEqual(summary["action_recovery"]["total"], 1)
        self.assertEqual(summary["action_recovery"]["recovered"], 1)
        self.assertEqual(summary["action_recovery"]["unrecovered"], 0)
        self.assertEqual(summary["action_recovery"]["runs_with_action_recovery"], 1)
        self.assertEqual(summary["action_recovery"]["runs_with_recovered_action"], 1)
        self.assertEqual(summary["action_recovery"]["runs_with_unrecovered_action"], 0)
        self.assertEqual(summary["action_recovery"]["by_status"], {"preflight_mismatch": 1})
        self.assertEqual(summary["action_recovery"]["by_kind"], {"stale_target_index": 1})
        self.assertEqual(summary["failure_evidence_kinds"], {
            "mcp_read": 1,
            "synthetic_terminal": 1,
            "terminal_outcome_source": 1,
        })
        self.assertEqual(
            summary["terminal_recovery"],
            {"attempted": 1, "succeeded": 1, "synthetic_terminals": 1},
        )
        self.assertEqual(summary["validation"]["act1_boss_reached"], 3)
        self.assertEqual(summary["validation"]["act1_boss_cleared"], 1)
        self.assertEqual(summary["validation"]["pristine_act1_boss_cleared"], 0)
        self.assertEqual(summary["validation"]["act1_boss_prefix_blockers"], {"recovered_action_race": 1})
        self.assertEqual(summary["validation"]["act1_boss_clear_blockers"], {"prefix:recovered_action_race": 1})
        recovered_run = next(run for run in payload["runs"] if run["path"] == str(recovered_clear))
        self.assertIn("recovery=stale_target_index:1", recovered_run["status_line"])
        self.assertIn("run_status_summary:", summary["status_line"])
        self.assertIn("runs=3", summary["status_line"])
        self.assertIn("classes=clean_trainable:1,diagnostic_excluded:1,infra_blocked:1", summary["status_line"])
        self.assertIn(
            "class_reasons=clean_trainable/completed_clean:1,"
            "diagnostic_excluded/no_terminal_outcome:1,"
            "infra_blocked/mcp_unreachable:1",
            summary["status_line"],
        )
        self.assertIn("flags=", summary["status_line"])
        self.assertIn("recovered_action_race:1", summary["status_line"])
        self.assertIn("synthetic_terminal:1", summary["status_line"])
        self.assertIn("recovery=stale_target_index:1", summary["status_line"])
        self.assertIn("act1_boss=reached:3,cleared:1,pristine:0", summary["status_line"])
        self.assertIn(
            "evidence=mcp_read:1,synthetic_terminal:1,terminal_outcome_source:1",
            summary["status_line"],
        )
        self.assertIn("terminal_recovery=synthetic:1,attempted:1,succeeded:1", summary["status_line"])
        self.assertIn("clear_blockers=prefix:recovered_action_race:1", summary["status_line"])
        self.assertEqual(summary["recommended_next_action"], "fix_execution_layer")
        self.assertEqual(summary["recommended_owner"], "engineering_agent")
        self.assertEqual(summary["recommended_reason"], "infra_blocked_runs")
        self.assertFalse(summary["recommended_live_mcp_required"])
        self.assertEqual(summary["recommended_source_count"], 1)
        self.assertEqual(summary["recommended_source_paths"], [str(read_failed)])
        self.assertEqual(len(summary["recommended_source_runs"]), 1)
        self.assertEqual(summary["recommended_source_runs"][0]["path"], str(read_failed))
        self.assertEqual(summary["recommended_source_runs"][0]["category"], "infra_blocked")
        self.assertEqual(summary["recommended_source_runs"][0]["reason"], "mcp_unreachable")
        self.assertEqual(summary["recommended_source_runs"][0]["failure_attribution"], "mcp_execution")
        source_evidence = summary["recommended_source_runs"][0]["failure_evidence"]
        self.assertEqual(source_evidence["mcp_read"]["step"], 21)
        self.assertEqual(source_evidence["mcp_read"]["event"], "error")
        self.assertEqual(source_evidence["mcp_read"]["last_state"]["screen_type"], "NONE")
        self.assertEqual(source_evidence["mcp_read"]["last_state"]["floor"], 16)
        self.assertEqual(summary["recommended_source_runs"][0]["act1_boss"]["enemy_ids"], ["TheGuardian"])
        self.assertIn("category=infra_blocked/mcp_unreachable", summary["recommended_source_runs"][0]["status_line"])
        self.assertEqual(summary["recommended_assignment"]["owner"], "engineering_agent")
        self.assertEqual(summary["recommended_assignment"]["action"], "fix_execution_layer")
        self.assertEqual(summary["recommended_assignment"]["status_line"], summary["status_line"])
        self.assertEqual(summary["recommended_assignment"]["input_paths"], [str(root)])
        self.assertEqual(summary["recommended_assignment"]["resolved_log_count"], 3)
        self.assertEqual(len(summary["recommended_assignment"]["resolved_log_paths"]), 3)
        self.assertEqual(summary["recommended_assignment"]["warnings"], [])
        self.assertEqual(summary["recommended_assignment"]["source_paths"], [str(read_failed)])
        self.assertEqual(summary["recommended_assignment"]["source_runs"][0]["path"], str(read_failed))
        self.assertEqual(
            summary["recommended_assignment"]["execution_contract"]["mode"],
            "offline_code_or_data_infra",
        )
        self.assertFalse(summary["recommended_assignment"]["execution_contract"]["requires_live_mcp_ownership"])
        self.assertIn(
            "control_live_mcp_without_explicit_ownership",
            summary["recommended_assignment"]["execution_contract"]["forbidden_operations"],
        )
        prompt = summary["recommended_assignment"]["assignment_prompt"]
        self.assertIn("Run status assignment", prompt)
        self.assertIn("Agent: engineering_agent", prompt)
        self.assertIn("Action: fix_execution_layer", prompt)
        self.assertIn("Run status summary: run_status_summary:", prompt)
        self.assertIn(f"Input paths: {root}", prompt)
        self.assertIn("Resolved logs:", prompt)
        self.assertIn("synthetic.jsonl", prompt)
        self.assertIn("read_failed.jsonl", prompt)
        self.assertIn("Allowed operations: inspect_logs", prompt)
        self.assertIn("Execution mode: offline_code_or_data_infra", prompt)
        self.assertIn("Requires live MCP ownership: False", prompt)
        self.assertIn(str(read_failed), prompt)
        self.assertIn("category=infra_blocked/mcp_unreachable", prompt)
        self.assertIn(
            "evidence: category=infra_blocked reason=mcp_unreachable "
            "failure_attribution=mcp_execution validation_grade=infra_blocked",
            prompt,
        )
        self.assertIn('failure_evidence={"mcp_read"', prompt)
        self.assertIn('"screen_type":"NONE"', prompt)
        self.assertIn('"enemy_ids":["TheGuardian"]', prompt)
        self.assertIn("run_status_next:", summary["run_status_next_line"])
        self.assertIn("recommended=fix_execution_layer", summary["run_status_next_line"])
        self.assertIn("owner=engineering_agent", summary["run_status_next_line"])
        self.assertIn("reason=infra_blocked_runs", summary["run_status_next_line"])
        self.assertIn("mode=offline_code_or_data_infra", summary["run_status_next_line"])
        self.assertIn("live_mcp_required=false", summary["run_status_next_line"])
        self.assertIn("source_count=1", summary["run_status_next_line"])
        self.assertIn("first_source=read_failed.jsonl", summary["run_status_next_line"])

    def test_build_status_payload_rolls_up_failed_terminal_recovery(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "failed_terminal_recovery.jsonl"
            terminal_state = {
                "in_game": True,
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False, "source": "synthetic_after_mcp_null"},
            }
            write_lines(
                path,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 21,
                        "event": "synthetic_terminal_state",
                        "last_error": "read_state_failed: Internal error: null",
                        "terminal_recovery_attempted": True,
                        "terminal_recovery_succeeded": False,
                        "post_recovery_diagnostics": {"status": "state_broken"},
                    },
                    {"step": 21, "state": terminal_state},
                ],
            )

            payload = build_status_payload([path])

        self.assertEqual(
            payload["summary"]["terminal_recovery"],
            {
                "attempted": 1,
                "failed": 1,
                "synthetic_terminals": 1,
                "unhealthy": 1,
            },
        )
        self.assertIn("terminal_recovery_unhealthy:1", payload["summary"]["status_line"])
        self.assertIn(
            "terminal_recovery=synthetic:1,attempted:1,failed:1,unhealthy:1",
            payload["summary"]["status_line"],
        )

    def test_run_status_rolls_up_shadow_label_exclusions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "missed_kill.jsonl"
            kill_state = {
                "in_game": True,
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
                    "incoming_damage": 6,
                    "player": {"current_hp": 12, "max_hp": 80, "block": 0, "current_energy": 3},
                    "hand_cards": [
                        {
                            "id": "Strike_R",
                            "name": "Strike",
                            "type": "ATTACK",
                            "damage": 6,
                            "cost": 1,
                            "is_playable": True,
                        },
                        {
                            "id": "Defend_R",
                            "name": "Defend",
                            "type": "SKILL",
                            "block": 5,
                            "cost": 1,
                            "is_playable": True,
                        },
                    ],
                    "monsters": [
                        {
                            "id": "Hexaghost",
                            "name": "Hexaghost",
                            "hp": 4,
                            "max_hp": 264,
                            "block": 0,
                            "intent": "ATTACK",
                        }
                    ],
                },
            }
            terminal_state = {
                "in_game": True,
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False},
            }
            write_lines(
                path,
                [
                    {
                        "step": 50,
                        "state": kill_state,
                        "decision": {
                            "actions": [{"action": "play_card", "card_index": 2, "target_index": 1}],
                            "metadata": {
                                "search": {
                                    "type": "one_turn",
                                    "first_card_key": "Strike_R",
                                    "sequence_card_keys": ["Strike_R"],
                                    "score": 25,
                                    "initial_loss": 6,
                                    "projected_loss": 0,
                                    "kills": 1,
                                    "attacks_removed": 1,
                                }
                            },
                        },
                    },
                    {"step": 51, "state": terminal_state},
                ],
            )

            status = summarize_log(path)
            payload = build_status_payload([root])

        combat_quality = status["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(combat_quality["total"], 1)
        self.assertEqual(combat_quality["trainable"], 0)
        self.assertEqual(combat_quality["excluded_from_training"], 1)
        self.assertEqual(combat_quality["direct_kill_available"], 1)
        self.assertEqual(combat_quality["exclusion_reasons"], {"missed_direct_kill": 1})
        self.assertEqual(combat_quality["exclusion_examples"][0]["reasons"], ["missed_direct_kill"])
        self.assertEqual(combat_quality["exclusion_examples"][0]["source_log"], str(path))
        self.assertEqual(combat_quality["exclusion_examples"][0]["enemy_ids"], ["Hexaghost"])
        self.assertIn("label_exclusions=combat_search:1/missed_direct_kill:1", status["status_line"])

        summary_quality = payload["summary"]["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(summary_quality["total"], 1)
        self.assertEqual(summary_quality["trainable"], 0)
        self.assertEqual(summary_quality["excluded_from_training"], 1)
        self.assertEqual(summary_quality["direct_kill_available"], 1)
        self.assertEqual(summary_quality["exclusion_reasons"], {"missed_direct_kill": 1})
        self.assertEqual(summary_quality["exclusion_examples"][0]["source_log"], str(path))
        self.assertEqual(summary_quality["exclusion_examples"][0]["reasons"], ["missed_direct_kill"])
        self.assertIn(
            "label_exclusions=combat_search:1/missed_direct_kill:1",
            payload["summary"]["status_line"],
        )
        self.assertEqual(payload["summary"]["recommended_next_action"], "review_excluded_combat_search_labels")
        self.assertEqual(payload["summary"]["recommended_owner"], "ai_agent")
        self.assertEqual(payload["summary"]["recommended_reason"], "label_exclusions")
        self.assertEqual(payload["summary"]["recommended_source_count"], 1)
        self.assertEqual(payload["summary"]["recommended_source_paths"], [str(path)])
        self.assertEqual(payload["summary"]["recommended_source_runs"][0]["path"], str(path))
        self.assertEqual(
            payload["summary"]["recommended_source_runs"][0]["label_exclusions"],
            "combat_search:1/missed_direct_kill:1",
        )
        self.assertEqual(
            payload["summary"]["recommended_source_runs"][0]["label_exclusion_examples"][0]["enemy_ids"],
            ["Hexaghost"],
        )
        self.assertEqual(payload["summary"]["recommended_source_runs"][0]["failure_attribution"], "combat_planning")
        self.assertEqual(payload["summary"]["recommended_assignment"]["owner"], "ai_agent")
        self.assertEqual(
            payload["summary"]["recommended_assignment"]["execution_contract"]["mode"],
            "offline_strategy_or_shadow_model",
        )
        self.assertFalse(
            payload["summary"]["recommended_assignment"]["execution_contract"]["requires_live_mcp_ownership"]
        )
        self.assertIn(
            "replace_live_policy_with_model",
            payload["summary"]["recommended_assignment"]["execution_contract"]["forbidden_operations"],
        )
        ai_prompt = payload["summary"]["recommended_assignment"]["assignment_prompt"]
        self.assertIn("Agent: ai_agent", ai_prompt)
        self.assertIn("Action: review_excluded_combat_search_labels", ai_prompt)
        self.assertIn("Execution mode: offline_strategy_or_shadow_model", ai_prompt)
        self.assertIn("replace_live_policy_with_model", ai_prompt)
        self.assertIn("label_exclusions=combat_search:1/missed_direct_kill:1", ai_prompt)
        self.assertIn("label_exclusion_examples=", ai_prompt)
        self.assertIn("Hexaghost", ai_prompt)
        self.assertIn("recommended=review_excluded_combat_search_labels", payload["summary"]["run_status_next_line"])
        self.assertIn("owner=ai_agent", payload["summary"]["run_status_next_line"])

    def test_run_status_prioritizes_boss_label_examples_across_sources(self):
        def write_missed_kill_log(path: Path, *, floor: int, enemy_id: str) -> None:
            state = {
                "in_game": True,
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": floor,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 12,
                "max_hp": 80,
                "combat": {
                    "turn": 4,
                    "incoming_damage": 10,
                    "player": {"current_hp": 12, "max_hp": 80, "block": 0, "current_energy": 2},
                    "hand_cards": [
                        {
                            "id": "Strike_R",
                            "name": "Strike",
                            "type": "ATTACK",
                            "damage": 6,
                            "cost": 1,
                            "is_playable": True,
                        },
                        {
                            "id": "Defend_R",
                            "name": "Defend",
                            "type": "SKILL",
                            "block": 5,
                            "cost": 1,
                            "is_playable": True,
                        },
                    ],
                    "monsters": [{"id": enemy_id, "name": enemy_id, "hp": 4, "max_hp": 250, "intent": "ATTACK"}],
                },
            }
            terminal_state = {
                "in_game": True,
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": floor,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False},
            }
            write_lines(
                path,
                [
                    {
                        "step": 1,
                        "state": state,
                        "decision": {
                            "actions": [{"action": "play_card", "card_index": 2, "target_index": 1}],
                            "metadata": {
                                "search": {
                                    "type": "one_turn",
                                    "first_card_key": "Defend_R",
                                    "sequence_card_keys": ["Defend_R"],
                                    "initial_loss": 10,
                                    "projected_loss": 5,
                                    "kills": 0,
                                    "attacks_removed": 0,
                                }
                            },
                        },
                    },
                    {"step": 2, "state": terminal_state},
                ],
            )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            early_path = root / "a_early.jsonl"
            boss_path = root / "z_boss.jsonl"
            write_missed_kill_log(early_path, floor=2, enemy_id="JawWorm")
            write_missed_kill_log(boss_path, floor=16, enemy_id="Hexaghost")

            payload = build_status_payload([root])

        examples = payload["summary"]["shadow_label_quality"]["combat_search_labels"]["exclusion_examples"]
        self.assertEqual(examples[0]["source_log"], str(boss_path))
        self.assertEqual(examples[0]["enemy_ids"], ["Hexaghost"])
        self.assertEqual(payload["summary"]["recommended_source_paths"][0], str(boss_path))
        self.assertIn("Hexaghost", payload["summary"]["recommended_assignment"]["assignment_prompt"])
        self.assertIn("a_early.jsonl", payload["summary"]["recommended_assignment"]["assignment_prompt"])

    def test_run_status_rolls_up_unknown_static_feature_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "unknown_card.jsonl"
            map_state = {
                "in_game": True,
                "screen_type": "MAP",
                "room_phase": "COMPLETE",
                "floor": 15,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 55,
                "max_hp": 80,
                "deck": ["Strike_R", {"id": "MysteryBlade"}],
                "potions": [],
                "relics": [{"id": "Burning Blood"}],
                "boss_available": True,
                "route_evaluation": {
                    "options": [
                        {
                            "choice_index": 1,
                            "symbol": "B",
                            "score": 80,
                            "lookahead": {"nearest_rest": 0, "nearest_shop": None},
                        }
                    ]
                },
            }
            terminal_state = {
                "in_game": True,
                "screen_type": "GAME_OVER",
                "room_phase": "COMPLETE",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 0,
                "max_hp": 80,
                "outcome": {"victory": False},
            }
            write_lines(
                path,
                [
                    {
                        "step": 10,
                        "state": map_state,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {"step": 11, "state": terminal_state},
                ],
            )
            knowledge = StaticKnowledge.load()

            status = summarize_log(path, knowledge=knowledge)
            payload = build_status_payload([root], knowledge=knowledge)

        coverage = status["shadow_feature_coverage"]
        self.assertEqual(coverage["total_rows"], 2)
        self.assertEqual(
            coverage["categories"]["route_risk"]["unknown_static_features"]["deck_unknown_cards"]["total"],
            1,
        )
        self.assertEqual(
            coverage["categories"]["pre_boss_deck_quality"]["unknown_static_features"]["deck_unknown_cards"]["total"],
            1,
        )
        self.assertIn("feature_unknown=", status["status_line"])
        self.assertIn("route_risk:deck_unknown_cards:1", status["status_line"])
        self.assertIn("pre_boss_deck_quality:deck_unknown_cards:1", status["status_line"])

        summary_coverage = payload["summary"]["shadow_feature_coverage"]
        self.assertEqual(summary_coverage["total_rows"], 2)
        self.assertEqual(summary_coverage["unknown_static_runs"], 1)
        self.assertEqual(summary_coverage["unknown_static_total"], 2)
        self.assertEqual(
            summary_coverage["unknown_static_features"],
            {
                "pre_boss_deck_quality:deck_unknown_cards": 1,
                "route_risk:deck_unknown_cards": 1,
            },
        )
        self.assertIn("feature_unknown=", payload["summary"]["status_line"])
        self.assertIn("route_risk:deck_unknown_cards:1", payload["summary"]["status_line"])
        self.assertIn("pre_boss_deck_quality:deck_unknown_cards:1", payload["summary"]["status_line"])
        self.assertEqual(payload["summary"]["recommended_next_action"], "update_static_knowledge")
        self.assertEqual(payload["summary"]["recommended_owner"], "ai_agent")
        self.assertEqual(payload["summary"]["recommended_reason"], "unknown_static_features")
        self.assertEqual(payload["summary"]["recommended_source_count"], 1)
        self.assertEqual(payload["summary"]["recommended_source_paths"], [str(path)])
        self.assertEqual(payload["summary"]["recommended_source_runs"][0]["path"], str(path))
        self.assertIn(
            "pre_boss_deck_quality:deck_unknown_cards:1",
            payload["summary"]["recommended_source_runs"][0]["feature_unknown"],
        )
        self.assertEqual(payload["summary"]["recommended_source_runs"][0]["failure_attribution"], "potion_planning")
        gap_summary = payload["summary"]["static_knowledge_gaps"]
        self.assertEqual(gap_summary["total_missing_occurrences"], 1)
        self.assertEqual(gap_summary["totals"]["cards"], {"occurrences": 1, "unique": 1})
        self.assertEqual(gap_summary["entities"]["cards"][0]["entity"], "MysteryBlade")
        self.assertEqual(payload["summary"]["recommended_assignment"]["static_knowledge_gaps"], gap_summary)
        assignment_prompt = payload["summary"]["recommended_assignment"]["assignment_prompt"]
        self.assertIn("Static knowledge gaps:", assignment_prompt)
        self.assertIn('"entity":"MysteryBlade"', assignment_prompt)
        self.assertIn("recommended=update_static_knowledge", payload["summary"]["run_status_next_line"])

    def test_cli_writes_static_knowledge_gap_report_for_monitor_handoff(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "unknown_card.jsonl"
            output = root / "status.json"
            assignment_output = root / "assignment.txt"
            gaps_output = root / "gaps.json"
            write_lines(
                path,
                [
                    {
                        "step": 10,
                        "state": {
                            "in_game": True,
                            "screen_type": "MAP",
                            "room_phase": "COMPLETE",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 55,
                            "max_hp": 80,
                            "deck": ["Strike_R", {"id": "MysteryBlade"}],
                            "potions": [],
                            "relics": [{"id": "Burning Blood"}],
                            "boss_available": True,
                            "route_evaluation": {
                                "options": [
                                    {
                                        "choice_index": 1,
                                        "symbol": "B",
                                        "score": 80,
                                        "lookahead": {"nearest_rest": 0, "nearest_shop": None},
                                    }
                                ]
                            },
                        },
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 11,
                        "state": {
                            "in_game": True,
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

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        str(path),
                        "--output",
                        str(output),
                        "--assignment-output",
                        str(assignment_output),
                        "--static-knowledge-gaps-output",
                        str(gaps_output),
                    ]
                )
            saved = json.loads(output.read_text(encoding="utf-8"))
            assignment_text = assignment_output.read_text(encoding="utf-8")
            gap_report = json.loads(gaps_output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        output_text = stdout.getvalue()
        self.assertIn("Wrote static knowledge gap report:", output_text)
        self.assertIn("static_knowledge_gaps logs=1 missing=1 cards=1/1", output_text)
        self.assertEqual(saved["summary"]["recommended_next_action"], "update_static_knowledge")
        self.assertEqual(saved["summary"]["static_knowledge_gap_report_path"], str(gaps_output))
        self.assertEqual(saved["summary"]["recommended_assignment"]["static_knowledge_gap_report_path"], str(gaps_output))
        self.assertIn(f"Static knowledge gap report: {gaps_output}", assignment_text)
        self.assertIn("Static knowledge gaps:", assignment_text)
        self.assertIn('"entity":"MysteryBlade"', assignment_text)
        self.assertEqual(gap_report["entities"]["cards"]["items"][0]["entity"], "MysteryBlade")
        self.assertEqual(gap_report["total_missing_occurrences"], 1)

    def test_cli_prints_run_status_next_line(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "read_failed.jsonl"
            output = root / "status.json"
            assignment_output = root / "assignment.txt"
            write_lines(
                path,
                [
                    {"step": 20, "state": combat_state()},
                    {
                        "step": 21,
                        "event": "error",
                        "error": "read_state_failed: Cannot reach MCPTheSpire at http://127.0.0.1:8080/mcp",
                    },
                ],
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        str(path),
                        "--output",
                        str(output),
                        "--assignment-output",
                        str(assignment_output),
                    ]
                )
            saved = json.loads(output.read_text(encoding="utf-8"))
            assignment_text = assignment_output.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        output_text = stdout.getvalue()
        self.assertIn("Wrote run status assignment:", output_text)
        self.assertIn("run_status_next:", output_text)
        self.assertIn("recommended=fix_execution_layer", output_text)
        self.assertIn("owner=engineering_agent", output_text)
        self.assertIn("mode=offline_code_or_data_infra", output_text)
        self.assertIn("live_mcp_required=false", output_text)
        self.assertIn("source_count=1", output_text)
        self.assertIn("first_source=read_failed.jsonl", output_text)
        self.assertIn("run_status_next:", saved["summary"]["run_status_next_line"])
        self.assertEqual(saved["summary"]["recommended_source_paths"], [str(path)])
        self.assertEqual(saved["summary"]["recommended_source_runs"][0]["path"], str(path))
        self.assertEqual(saved["summary"]["recommended_assignment"]["source_paths"], [str(path)])
        self.assertEqual(saved["summary"]["recommended_assignment"]["input_paths"], [str(path)])
        self.assertEqual(saved["summary"]["recommended_assignment"]["resolved_log_paths"], [str(path)])
        self.assertEqual(
            saved["summary"]["recommended_assignment"]["execution_contract"]["mode"],
            "offline_code_or_data_infra",
        )
        self.assertEqual(
            assignment_text,
            saved["summary"]["recommended_assignment"]["assignment_prompt"].rstrip() + "\n",
        )
        self.assertIn("Agent: engineering_agent", assignment_text)
        self.assertIn("Run status summary: run_status_summary:", assignment_text)
        self.assertIn(f"Input paths: {path}", assignment_text)
        self.assertIn(f"Resolved logs: {path}", assignment_text)
        self.assertIn("Allowed operations: inspect_logs", assignment_text)
        self.assertIn("Execution mode: offline_code_or_data_infra", assignment_text)
        self.assertIn('failure_evidence={"mcp_read"', assignment_text)
        self.assertIn("mcp_unreachable", saved["summary"]["recommended_source_runs"][0]["status_line"])

    def test_empty_run_status_recommends_live_gated_runner_collection(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty_logs"
            empty.mkdir()

            payload = build_status_payload([empty])

        summary = payload["summary"]
        self.assertEqual(summary["warnings"], ["no_resolved_logs"])
        self.assertEqual(summary["recommended_next_action"], "collect_a0_manifest_batch")
        self.assertEqual(summary["recommended_owner"], "runner_agent")
        self.assertTrue(summary["recommended_live_mcp_required"])
        self.assertEqual(summary["recommended_source_count"], 0)
        self.assertEqual(summary["recommended_assignment"]["input_paths"], [str(empty)])
        self.assertEqual(summary["recommended_assignment"]["resolved_log_paths"], [])
        self.assertEqual(summary["recommended_assignment"]["warnings"], ["no_resolved_logs"])
        self.assertEqual(summary["recommended_assignment"]["source_paths"], [])
        self.assertEqual(
            summary["recommended_assignment"]["execution_contract"]["mode"],
            "live_mcp_gated_collection",
        )
        self.assertTrue(summary["recommended_assignment"]["execution_contract"]["requires_live_mcp_ownership"])
        self.assertIn(
            "control_live_mcp_without_explicit_ownership",
            summary["recommended_assignment"]["execution_contract"]["forbidden_operations"],
        )
        runner_prompt = summary["recommended_assignment"]["assignment_prompt"]
        self.assertIn("Agent: runner_agent", runner_prompt)
        self.assertIn("Action: collect_a0_manifest_batch", runner_prompt)
        self.assertIn("Execution mode: live_mcp_gated_collection", runner_prompt)
        self.assertIn("Requires live MCP ownership: True", runner_prompt)
        self.assertIn(f"Input paths: {empty}", runner_prompt)
        self.assertIn("Warnings: no_resolved_logs", runner_prompt)
        self.assertIn("Allowed operations: read_logs", runner_prompt)
        self.assertIn("Source runs: none", runner_prompt)
        self.assertIn("mode=live_mcp_gated_collection", summary["run_status_next_line"])


if __name__ == "__main__":
    unittest.main()
