import json
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.training_manifest import (
    CLEAN_TRAINABLE,
    DIAGNOSTIC_EXCLUDED,
    INFRA_BLOCKED,
    build_manifest,
    clean_log_paths_from_manifest,
    iter_log_files,
    main as training_manifest_main,
    refresh_manifest_from_existing,
    write_shadow_examples,
)
from slay_ai.static_knowledge import StaticKnowledge


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


class TrainingManifestTests(unittest.TestCase):
    def test_iter_log_files_expands_directories_and_files_in_stable_order(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            first = logs / "a.jsonl"
            second = logs / "b.jsonl"
            ignored = logs / "notes.txt"
            write_jsonl(second, [{"step": 2}])
            write_jsonl(first, [{"step": 1}])
            ignored.write_text("not a log", encoding="utf-8")
            direct = root / "direct.jsonl"
            write_jsonl(direct, [{"step": 3}])

            paths = list(iter_log_files([logs, direct, root / "missing.jsonl"]))

            self.assertEqual(paths, [first, second, direct])

    def test_classifies_logs_and_extracts_shadow_examples(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            infra = root / "infra.jsonl"
            diagnostic = root / "diagnostic.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "current_hp": 66,
                            "max_hp": 80,
                            "gold": 39,
                            "deck": ["Strike_R", "Defend_R", "Bash", "Shrug It Off"],
                            "relics": ["local display burning blood"],
                            "relic_items": [{"id": "Burning Blood"}],
                            "potions": [{"id": "Dexterity Potion"}],
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
                                            "readiness_penalty": 12,
                                            "readiness_flags": ["thin_block"],
                                            "act2_route_flags": ["early_elite"],
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
                            "ascension_level": 4,
                            "current_hp": 30,
                            "max_hp": 80,
                            "potions": [{"id": "Dexterity Potion"}],
                            "combat": {
                                "turn": 2,
                                "incoming_damage": 18,
                                "monsters": [{"id": "Hexaghost"}],
                            },
                        },
                        "decision": {"actions": [{"action": "use_potion", "slot": 1}]},
                    },
                    {
                        "step": 3,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale target_index: target_index=2 outside current monster count 1.",
                    },
                    {
                        "step": 4,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "outcome": {"victory": False, "score": 321},
                        },
                        "decision": {"actions": [], "learn_card_pick": "Shrug It Off"},
                    },
                ],
            )
            write_jsonl(
                infra,
                [
                    {"step": 1, "event": "action_result", "action_status": "failed"},
                    {"step": 2, "state": {"screen_type": "GAME_OVER", "floor": 3}},
                ],
            )
            write_jsonl(diagnostic, [{"step": 1, "state": {"screen_type": "MAP", "floor": 1}}])

            manifest, shadow = build_manifest([root])

        self.assertEqual(manifest["inputs"], [str(root)])
        self.assertEqual(manifest["resolved_logs"], [str(clean), str(diagnostic), str(infra)])
        self.assertEqual(manifest["summary"]["input_count"], 1)
        self.assertEqual(manifest["summary"]["resolved_log_count"], 3)
        self.assertEqual(manifest["warnings"], [])
        self.assertEqual(manifest["summary"]["warnings"], [])
        self.assertEqual(manifest["summary"]["total_logs"], 3)
        self.assertEqual(manifest["summary"][CLEAN_TRAINABLE], 1)
        self.assertEqual(manifest["summary"][INFRA_BLOCKED], 1)
        self.assertEqual(manifest["summary"][DIAGNOSTIC_EXCLUDED], 1)
        self.assertEqual(manifest["summary"]["classification_reasons"][CLEAN_TRAINABLE], {"completed_clean": 1})
        self.assertEqual(manifest["summary"]["classification_reasons"][INFRA_BLOCKED], {"failed_action": 1})
        self.assertEqual(manifest["summary"]["classification_reasons"][DIAGNOSTIC_EXCLUDED], {"no_terminal_outcome": 1})
        clean_item = manifest["categories"][CLEAN_TRAINABLE][0]
        self.assertEqual(clean_item["reason"], "completed_clean")
        self.assertEqual(clean_item["failure_attribution"], "combat_planning")
        self.assertIn("boss_combat", clean_item["failure_tags"])
        self.assertEqual(clean_item["recovered_actions"], 1)
        self.assertEqual(clean_item["action_recovery_summary"]["recovered"], 1)
        self.assertEqual(clean_item["action_recovery_summary"]["unrecovered"], 0)
        self.assertEqual(clean_item["action_recovery_summary"]["by_kind"]["stale_target_index"], 1)
        clean_action_example = clean_item["action_recovery_summary"]["examples"][0]
        self.assertEqual(clean_action_example["step"], 3)
        self.assertEqual(clean_action_example["kind"], "stale_target_index")
        self.assertTrue(clean_action_example["recovered"])
        self.assertEqual(clean_action_example["last_state"]["screen_type"], "NONE")
        self.assertEqual(clean_action_example["last_state"]["floor"], 16)
        action_recovery_summary = dict(manifest["summary"]["action_recovery"])
        action_recovery_examples = action_recovery_summary.pop("examples")
        self.assertEqual(
            action_recovery_summary,
            {
                "total": 1,
                "recovered": 1,
                "unrecovered": 0,
                "runs_with_action_recovery": 1,
                "runs_with_recovered_action": 1,
                "runs_with_unrecovered_action": 0,
                "by_status": {"preflight_mismatch": 1},
                "by_kind": {"stale_target_index": 1},
            },
        )
        self.assertEqual(action_recovery_examples[0]["source_log"], str(clean))
        self.assertEqual(action_recovery_examples[0]["source_category"], CLEAN_TRAINABLE)
        self.assertEqual(clean_item["validation_grade"], "usable_with_recoveries")
        self.assertIn("recovered_action_race", clean_item["validation_flags"])
        self.assertTrue(clean_item["validation_evidence"]["act1_boss"]["reached"])
        self.assertFalse(clean_item["validation_evidence"]["act1_boss"]["cleared"])
        self.assertEqual(clean_item["card_picks"], 1)
        infra_item = manifest["categories"][INFRA_BLOCKED][0]
        self.assertEqual(infra_item["failure_attribution"], "mcp_execution")
        self.assertIn("action_execution", infra_item["failure_tags"])
        diagnostic_item = manifest["categories"][DIAGNOSTIC_EXCLUDED][0]
        self.assertEqual(diagnostic_item["failure_attribution"], "diagnostic_incomplete")
        self.assertEqual(diagnostic_item["validation_grade"], "diagnostic")
        self.assertEqual(manifest["summary"]["failure_attributions"]["combat_planning"], 1)
        self.assertEqual(manifest["summary"]["failure_attributions"]["mcp_execution"], 1)
        self.assertEqual(manifest["summary"]["validation"]["by_grade"]["usable_with_recoveries"], 1)
        self.assertEqual(
            manifest["summary"]["validation"]["by_flag"],
            {
                "diagnostic_excluded": 1,
                "infra_blocked": 1,
                "no_terminal_outcome": 1,
                "recovered_action_race": 1,
            },
        )
        self.assertEqual(manifest["summary"]["validation"]["act1_boss_reached"], 1)
        self.assertEqual(
            manifest["summary"]["validation"]["act1_boss_prefix_blockers"],
            {"recovered_action_race": 1},
        )
        self.assertEqual(manifest["summary"]["validation"]["act1_boss_clear_blockers"], {})
        self.assertEqual(len(shadow["route_risk"]), 1)
        self.assertEqual(len(shadow["potion_tempo"]), 1)
        self.assertEqual(len(shadow["pre_boss_deck_quality"]), 1)
        for key in ("route_risk", "potion_tempo", "pre_boss_deck_quality"):
            row = shadow[key][0]
            self.assertEqual(row["source_category"], CLEAN_TRAINABLE)
            self.assertEqual(row["source_reason"], "completed_clean")
            self.assertEqual(row["source_validation_grade"], "usable_with_recoveries")
            self.assertEqual(row["source_validation_flags"], ["recovered_action_race"])
            self.assertEqual(row["source_recovered_actions"], 1)
            self.assertEqual(row["source_failed_actions"], 0)
            self.assertTrue(row["source_has_recovered_action_race"])
            self.assertEqual(row["source_action_recovery_kinds"], {"stale_target_index": 1})
        self.assertEqual(shadow["route_risk"][0]["readiness_penalty"], 12)
        self.assertEqual(shadow["route_risk"][0]["readiness_flags"], ["thin_block"])
        self.assertEqual(shadow["route_risk"][0]["act2_route_flags"], ["early_elite"])
        self.assertTrue(shadow["potion_tempo"][0]["used_potion"])
        self.assertEqual(shadow["potion_tempo"][0]["used_potion_slot"], 1)
        self.assertFalse(shadow["potion_tempo"][0]["used_potion_targeted"])
        self.assertEqual(shadow["pre_boss_deck_quality"][0]["block_cards"], 2)
        self.assertFalse(shadow["pre_boss_deck_quality"][0]["boss_potion_gap"])

    def test_manifest_warns_when_inputs_resolve_no_logs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty"
            empty.mkdir()

            manifest, shadow = build_manifest([empty])

        self.assertEqual(manifest["inputs"], [str(empty)])
        self.assertEqual(manifest["resolved_logs"], [])
        self.assertEqual(manifest["warnings"], ["no_resolved_logs"])
        self.assertEqual(manifest["summary"]["warnings"], ["no_resolved_logs"])
        self.assertEqual(manifest["summary"]["input_count"], 1)
        self.assertEqual(manifest["summary"]["resolved_log_count"], 0)
        self.assertEqual(manifest["summary"]["total_logs"], 0)
        self.assertEqual(manifest["summary"][CLEAN_TRAINABLE], 0)
        self.assertEqual(manifest["summary"][DIAGNOSTIC_EXCLUDED], 0)
        self.assertEqual(manifest["summary"][INFRA_BLOCKED], 0)
        self.assertEqual(shadow["route_risk"], [])

    def test_refresh_manifest_from_existing_prefers_resolved_logs_for_stable_schema_backfill(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            clean = logs / "clean.jsonl"
            extra = logs / "extra.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False, "score": 100},
                        },
                    }
                ],
            )
            write_jsonl(
                extra,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 3,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False, "score": 10},
                        },
                    }
                ],
            )
            stale = root / "training_manifest_stale.json"
            stale.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "inputs": [str(logs)],
                        "resolved_logs": [str(clean)],
                        "summary": {"total_logs": 1},
                        "categories": {"clean_trainable": [], "diagnostic_excluded": [], "infra_blocked": []},
                    }
                ),
                encoding="utf-8",
            )

            manifest, shadow = refresh_manifest_from_existing(stale)

        self.assertEqual(manifest["inputs"], [str(clean)])
        self.assertEqual(manifest["resolved_logs"], [str(clean)])
        self.assertEqual(manifest["summary"]["total_logs"], 1)
        self.assertEqual(manifest["summary"][CLEAN_TRAINABLE], 1)
        self.assertIn("shadow_label_quality", manifest["summary"])
        self.assertIn("action_recovery", manifest["summary"])
        self.assertIn("terminal_recovery", manifest["summary"])
        self.assertIn("validation", manifest["summary"])
        self.assertEqual(manifest["refresh_source"]["manifest_path"], str(stale))
        self.assertEqual(manifest["refresh_source"]["source"], "resolved_logs")
        self.assertEqual(manifest["refresh_source"]["path_count"], 1)
        self.assertFalse(manifest["refresh_source"]["touches_live_mcp"])
        self.assertFalse(manifest["refresh_source"]["trains_models"])
        self.assertEqual(shadow["route_risk"], [])

    def test_main_refresh_from_existing_writes_refreshed_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    }
                ],
            )
            stale = root / "stale.json"
            stale.write_text(json.dumps({"inputs": [str(clean)], "summary": {}}), encoding="utf-8")
            output = root / "refreshed.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                code = training_manifest_main(["--refresh-from", str(stale), "--output", str(output)])

            written = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(written["refresh_source"]["source"], "inputs")
        self.assertEqual(written["resolved_logs"], [str(clean)])
        self.assertIn("Refreshed 1 logs", stdout.getvalue())
        self.assertIn(f"Wrote manifest: {output}", stdout.getvalue())

    def test_refresh_manifest_from_existing_falls_back_to_category_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 31,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    }
                ],
            )
            stale = root / "early_manifest_without_top_level_sources.json"
            stale.write_text(
                json.dumps(
                    {
                        "summary": {"total_logs": 1},
                        "categories": {
                            "clean_trainable": [{"path": str(clean)}],
                            "diagnostic_excluded": [],
                            "infra_blocked": [],
                        },
                    }
                ),
                encoding="utf-8",
            )

            manifest, _shadow = refresh_manifest_from_existing(stale)

        self.assertEqual(manifest["refresh_source"]["source"], "category_paths")
        self.assertEqual(manifest["refresh_source"]["path_count"], 1)
        self.assertEqual(manifest["resolved_logs"], [str(clean)])
        self.assertEqual(manifest["summary"]["total_logs"], 1)
        self.assertIn("action_recovery", manifest["summary"])
        self.assertIn("validation", manifest["summary"])

    def test_extracts_combat_search_label_examples_and_writes_jsonl(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "search.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 7,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 14,
                            "max_hp": 80,
                            "deck": ["Strike_R", "Defend_R", "Bash"],
                            "potions": [],
                            "relics": ["Burning Blood"],
                            "combat": {
                                "turn": 3,
                                "incoming_damage": 18,
                                "player": {
                                    "current_hp": 14,
                                    "max_hp": 80,
                                    "block": 0,
                                    "current_energy": 2,
                                },
                                "hand_cards": [
                                    {
                                        "name": "Defend",
                                        "id": "Defend_R",
                                        "type": "SKILL",
                                        "cost": 1,
                                        "block": 5,
                                        "is_playable": True,
                                    },
                                    {
                                        "name": "Bash",
                                        "id": "Bash",
                                        "type": "ATTACK",
                                        "cost": 2,
                                        "damage": 8,
                                        "is_playable": True,
                                        "has_target": True,
                                    },
                                ],
                                "monsters": [
                                    {
                                        "name": "Jaw Worm",
                                        "id": "JawWorm",
                                        "hp": 31,
                                        "max_hp": 40,
                                        "intent": "ATTACK",
                                        "move": {"damage": 18},
                                    }
                                ],
                            },
                        },
                        "decision": {
                            "actions": [{"action": "play_card", "card_index": 2, "target_index": 1}],
                            "metadata": {
                                "search": {
                                    "type": "one_turn_search",
                                    "sequence_card_keys": ["bash"],
                                    "first_card_key": "bash",
                                    "score": 17.5,
                                    "initial_loss": 18,
                                    "projected_loss": 10,
                                    "kills": 0,
                                    "attacks_removed": 1,
                                    "avoided_lethal": True,
                                }
                            },
                        },
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 7,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False, "score": 99},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean])
            output = root / "shadow"
            write_shadow_examples(output, shadow)
            written_rows = [
                json.loads(line)
                for line in (output / "combat_search_labels.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(manifest["summary"]["shadow_examples"]["combat_search_labels"], 1)
        quality = manifest["summary"]["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(quality["total"], 1)
        self.assertEqual(quality["trainable"], 1)
        self.assertEqual(quality["excluded_from_training"], 0)
        self.assertEqual(quality["direct_kill_available"], 0)
        self.assertEqual(quality["exclusion_reasons"], {})
        row = shadow["combat_search_labels"][0]
        self.assertEqual(row["source_log"], str(clean))
        self.assertEqual(row["source_category"], CLEAN_TRAINABLE)
        self.assertEqual(row["source_reason"], "completed_clean")
        self.assertEqual(row["source_validation_grade"], "pristine")
        self.assertEqual(row["source_validation_flags"], [])
        self.assertFalse(row["source_has_recovered_action_race"])
        self.assertEqual(row["source_recovered_actions"], 0)
        self.assertEqual(row["source_action_recovery_kinds"], {})
        self.assertEqual(row["turn"], 3)
        self.assertEqual(row["current_hp"], 14)
        self.assertEqual(row["current_energy"], 2)
        self.assertEqual(row["incoming"], 18)
        self.assertEqual(row["hand_ids"], ["Defend_R", "Bash"])
        self.assertEqual(row["enemy_ids"], ["JawWorm"])
        self.assertEqual(row["enemy_hps"], [31])
        self.assertEqual(row["actual_action"], "play_card")
        self.assertEqual(row["actual_card_index"], 2)
        self.assertEqual(row["actual_target_index"], 1)
        self.assertEqual(row["label_action"], "play_card")
        self.assertEqual(row["label_card_index"], 2)
        self.assertEqual(row["label_target_index"], 1)
        self.assertEqual(row["label_first_card_key"], "bash")
        self.assertEqual(row["label_sequence_card_keys"], ["bash"])
        self.assertEqual(row["initial_loss"], 18)
        self.assertEqual(row["projected_loss"], 10)
        self.assertEqual(row["loss_delta"], 8)
        self.assertEqual(row["attacks_removed"], 1)
        self.assertEqual(row["retaliation_damage"], 0)
        self.assertTrue(row["avoided_lethal"])
        self.assertFalse(row["direct_kill_available"])
        self.assertEqual(row["direct_kill_card_indices"], [])
        self.assertEqual(row["direct_kill_card_keys"], [])
        self.assertFalse(row["label_missed_direct_kill"])
        self.assertFalse(row["label_missed_single_card_search"])
        self.assertEqual(written_rows, [row])

    def test_combat_search_labels_include_static_boss_numeric_features(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "hexaghost_search.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 32,
                            "max_hp": 80,
                            "deck": ["Strike_R", "Defend_R", "Bash", "Flame Barrier"],
                            "potions": [{"id": "BlockPotion"}],
                            "relics": ["Burning Blood"],
                            "combat": {
                                "turn": 8,
                                "incoming_damage": 18,
                                "player": {
                                    "current_hp": 32,
                                    "max_hp": 80,
                                    "block": 0,
                                    "current_energy": 2,
                                },
                                "hand_cards": [
                                    {
                                        "name": "Defend",
                                        "id": "Defend_R",
                                        "type": "SKILL",
                                        "cost": 1,
                                        "block": 5,
                                        "is_playable": True,
                                    },
                                    {
                                        "name": "Bash",
                                        "id": "Bash",
                                        "type": "ATTACK",
                                        "cost": 2,
                                        "damage": 8,
                                        "is_playable": True,
                                        "has_target": True,
                                    },
                                ],
                                "monsters": [
                                    {
                                        "name": "Hexaghost",
                                        "id": "Hexaghost",
                                        "hp": 180,
                                        "max_hp": 250,
                                        "move": {"hits": 6, "damage": 3},
                                    }
                                ],
                            },
                        },
                        "decision": {
                            "actions": [{"action": "play_card", "card_index": 2, "target_index": 1}],
                            "metadata": {
                                "search": {
                                    "type": "one_turn_search",
                                    "sequence_card_keys": ["Bash"],
                                    "first_card_key": "Bash",
                                    "score": 12.0,
                                    "initial_loss": 18,
                                    "projected_loss": 10,
                                    "kills": 0,
                                    "attacks_removed": 0,
                                    "retaliation_damage": 0,
                                    "avoided_lethal": False,
                                }
                            },
                        },
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False, "score": 99},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean], knowledge=StaticKnowledge.load())

        row = shadow["combat_search_labels"][0]
        self.assertEqual(manifest["summary"]["shadow_examples"]["combat_search_labels"], 1)
        self.assertEqual(row["enemy_boss_count"], 1)
        self.assertEqual(row["enemy_elite_or_boss_count"], 1)
        self.assertEqual(row["enemy_total_expected_attack"], 36)
        self.assertEqual(row["enemy_status_pressure_count"], 1)
        self.assertEqual(row["enemy_scaling_pressure_count"], 1)
        self.assertEqual(row["enemy_frontload_check_count"], 1)
        self.assertTrue(row["boss_identity_known"])
        self.assertEqual(row["boss_known_count"], 1)
        self.assertEqual(row["boss_mechanic_hp_scaled_opening"], 1)
        self.assertEqual(row["boss_search_hint_burn_cleanup_pressure"], 1)
        self.assertEqual(row["boss_max_expected_attack"], 36)
        self.assertEqual(row["boss_total_expected_attack"], 36)
        self.assertEqual(row["boss_max_hit_count"], 6)
        self.assertEqual(row["boss_max_burn_damage"], 2)
        self.assertEqual(row["boss_max_upgraded_burn_damage"], 4)
        self.assertEqual(row["deck_tag_boss_defense"], 1)
        self.assertEqual(row["potion_block_value"], 12)
        self.assertTrue(row["has_burning_blood"])
        self.assertEqual(row["relic_known_count"], 1)
        self.assertEqual(row["relic_unknown_count"], 0)

    def test_combat_search_labels_mark_missed_direct_kill(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
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
                        "decision": {
                            "actions": [{"action": "play_card", "card_index": 2}],
                            "metadata": {
                                "search": {
                                    "type": "one_turn_search",
                                    "sequence_card_keys": ["Defend_R"],
                                    "first_card_key": "Defend_R",
                                    "score": 12.0,
                                    "initial_loss": 12,
                                    "projected_loss": 7,
                                    "kills": 0,
                                    "attacks_removed": 0,
                                    "avoided_lethal": False,
                                }
                            },
                        },
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean])

        row = shadow["combat_search_labels"][0]
        self.assertTrue(row["direct_kill_available"])
        self.assertEqual(row["direct_kill_card_indices"], [1])
        self.assertEqual(row["direct_kill_card_keys"], ["Twin Strike"])
        self.assertEqual(row["direct_kill_enemy_id"], "Hexaghost")
        self.assertEqual(row["direct_kill_enemy_hp"], 4)
        self.assertTrue(row["label_missed_direct_kill"])
        quality = manifest["summary"]["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(quality["total"], 1)
        self.assertEqual(quality["trainable"], 0)
        self.assertEqual(quality["excluded_from_training"], 1)
        self.assertEqual(quality["direct_kill_available"], 1)
        self.assertEqual(quality["exclusion_reasons"], {"missed_direct_kill": 1})
        self.assertEqual(quality["exclusion_examples"][0]["reasons"], ["missed_direct_kill"])
        self.assertEqual(quality["exclusion_examples"][0]["source_log"], str(clean))
        self.assertEqual(quality["exclusion_examples"][0]["label_first_card_key"], "Defend_R")
        self.assertEqual(quality["exclusion_examples"][0]["direct_kill_card_keys"], ["Twin Strike"])
        self.assertEqual(quality["exclusion_examples"][0]["enemy_ids"], ["Hexaghost"])

    def test_combat_search_labels_mark_missed_single_card_search_without_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "guardian.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 12,
                            "max_hp": 80,
                            "deck": ["Strike_R", "Defend_R", "Bash"],
                            "potions": [],
                            "relics": ["Burning Blood"],
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
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean])

        row = shadow["combat_search_labels"][0]
        self.assertEqual(row["search_type"], "single_card_search_diagnostic")
        self.assertEqual(row["actual_action"], "play_card")
        self.assertEqual(row["actual_card_index"], 2)
        self.assertEqual(row["label_action"], "play_card")
        self.assertEqual(row["label_card_index"], 1)
        self.assertEqual(row["label_target_index"], 1)
        self.assertEqual(row["label_first_card_key"], "Strike_R")
        self.assertEqual(row["label_sequence_card_keys"], ["Strike_R"])
        self.assertEqual(row["initial_loss"], 12)
        self.assertEqual(row["projected_loss"], 0)
        self.assertEqual(row["attacks_removed"], 12)
        self.assertTrue(row["avoided_lethal"])
        self.assertFalse(row["label_missed_direct_kill"])
        self.assertTrue(row["label_missed_single_card_search"])
        quality = manifest["summary"]["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(quality["total"], 1)
        self.assertEqual(quality["trainable"], 0)
        self.assertEqual(quality["excluded_from_training"], 1)
        self.assertEqual(quality["direct_kill_available"], 0)
        self.assertEqual(quality["missed_single_card_search"], 1)
        self.assertEqual(quality["exclusion_reasons"], {"missed_single_card_search": 1})
        self.assertEqual(quality["exclusion_examples"][0]["reasons"], ["missed_single_card_search"])
        self.assertEqual(quality["exclusion_examples"][0]["label_first_card_key"], "Strike_R")
        self.assertEqual(quality["exclusion_examples"][0]["enemy_ids"], ["TheGuardian"])

    def test_combat_search_labels_mark_missed_retaliation_search_without_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "hexaghost.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 12,
                            "max_hp": 80,
                            "deck": ["Strike_R", "Defend_R", "Bash"],
                            "potions": [],
                            "relics": ["Burning Blood"],
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
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean])

        row = shadow["combat_search_labels"][0]
        self.assertEqual(row["search_type"], "single_card_search_diagnostic")
        self.assertEqual(row["actual_card_index"], 1)
        self.assertEqual(row["label_card_index"], 2)
        self.assertEqual(row["label_first_card_key"], "Flame Barrier")
        self.assertEqual(row["label_sequence_card_keys"], ["Flame Barrier"])
        self.assertEqual(row["initial_loss"], 12)
        self.assertEqual(row["projected_loss"], 0)
        self.assertEqual(row["attacks_removed"], 0)
        self.assertEqual(row["retaliation_damage"], 24)
        self.assertTrue(row["avoided_lethal"])
        self.assertFalse(row["label_missed_direct_kill"])
        self.assertTrue(row["label_missed_single_card_search"])
        quality = manifest["summary"]["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(quality["total"], 1)
        self.assertEqual(quality["trainable"], 0)
        self.assertEqual(quality["excluded_from_training"], 1)
        self.assertEqual(quality["missed_single_card_search"], 1)
        self.assertEqual(quality["exclusion_reasons"], {"missed_single_card_search": 1})

    def test_combat_search_labels_ignore_non_boss_single_card_search_without_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "hallway.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 1,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 12,
                            "max_hp": 80,
                            "combat": {
                                "turn": 1,
                                "incoming_damage": 6,
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
                                        "id": "FuzzyLouseNormal",
                                        "name": "Louse",
                                        "hp": 6,
                                        "max_hp": 12,
                                        "move": {"damage": 6},
                                    }
                                ],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 2}]},
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean])

        self.assertEqual(shadow["combat_search_labels"], [])
        quality = manifest["summary"]["shadow_label_quality"]["combat_search_labels"]
        self.assertEqual(quality["total"], 0)
        self.assertEqual(quality["excluded_from_training"], 0)
        self.assertEqual(quality["exclusion_reasons"], {})

    def test_clean_log_paths_from_manifest_returns_only_clean_bucket(self):
        with TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "categories": {
                            CLEAN_TRAINABLE: [{"path": "clean_a.jsonl"}, {"path": "clean_b.jsonl"}],
                            DIAGNOSTIC_EXCLUDED: [{"path": "diag.jsonl"}],
                            INFRA_BLOCKED: [{"path": "infra.jsonl"}],
                        }
                    }
                ),
                encoding="utf-8",
            )

            paths = clean_log_paths_from_manifest(manifest_path)

        self.assertEqual([str(path) for path in paths], ["clean_a.jsonl", "clean_b.jsonl"])

    def test_synthetic_main_menu_terminal_is_diagnostic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "main_menu.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "CARD_REWARD",
                            "floor": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "deck": ["Strike_R", "Defend_R"],
                        },
                        "decision": {"learn_card_pick": "Perfected Strike"},
                    },
                    {
                        "step": 2,
                        "event": "synthetic_terminal_state",
                        "source": "main_menu_after_in_game",
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "outcome": {"victory": False, "source": "synthetic_after_main_menu"},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([log])

        self.assertEqual(manifest["summary"][CLEAN_TRAINABLE], 0)
        self.assertEqual(manifest["summary"][DIAGNOSTIC_EXCLUDED], 1)
        item = manifest["categories"][DIAGNOSTIC_EXCLUDED][0]
        self.assertEqual(item["reason"], "synthetic_after_main_menu")
        self.assertEqual(item["floor"], 1)
        self.assertEqual(item["failure_evidence"]["synthetic_terminal"]["step"], 2)
        self.assertEqual(item["failure_evidence"]["synthetic_terminal"]["source"], "main_menu_after_in_game")
        self.assertEqual(item["failure_evidence"]["terminal_outcome_source"], "synthetic_after_main_menu")
        self.assertEqual(manifest["summary"]["terminal_recovery"], {"not_attempted": 1, "synthetic_terminals": 1})
        self.assertEqual(shadow["route_risk"], [])
        self.assertEqual(shadow["potion_tempo"], [])
        self.assertEqual(shadow["pre_boss_deck_quality"], [])

    def test_summary_rolls_up_terminal_recovery_outcomes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            succeeded = root / "terminal_recovered.jsonl"
            failed = root / "terminal_failed.jsonl"
            base_combat = {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "floor": 16,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "current_hp": 1,
                "max_hp": 80,
                "combat": {
                    "incoming_damage": 9,
                    "monsters": [{"id": "TheGuardian", "current_hp": 60, "max_hp": 240}],
                },
            }
            terminal_state = {
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
            write_jsonl(
                succeeded,
                [
                    {"step": 1, "state": base_combat},
                    {
                        "step": 2,
                        "event": "synthetic_terminal_state",
                        "terminal_recovery_attempted": True,
                        "terminal_recovery_succeeded": True,
                        "post_recovery_diagnostics": {"status": "healthy"},
                    },
                    {"step": 2, "state": terminal_state},
                ],
            )
            write_jsonl(
                failed,
                [
                    {"step": 1, "state": base_combat},
                    {
                        "step": 2,
                        "event": "synthetic_terminal_state",
                        "terminal_recovery_attempted": True,
                        "terminal_recovery_succeeded": False,
                        "post_recovery_diagnostics": {"status": "state_broken"},
                    },
                    {"step": 2, "state": terminal_state},
                ],
            )

            manifest, _ = build_manifest([succeeded, failed])

        self.assertEqual(
            manifest["summary"]["terminal_recovery"],
            {
                "attempted": 2,
                "synthetic_terminals": 2,
                "failed": 1,
                "succeeded": 1,
                "unhealthy": 1,
            },
        )

    def test_diagnostic_max_steps_preserves_act1_boss_clear_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "max_steps_after_boss.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 46,
                            "max_hp": 80,
                            "potions": [{"id": "FirePotion"}],
                            "combat": {
                                "turn": 12,
                                "incoming_damage": 9,
                                "monsters": [{"id": "TheGuardian", "current_hp": 12, "max_hp": 240}],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 1, "target_index": 1}]},
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "BOSS_REWARD",
                            "room_phase": "COMPLETE",
                            "floor": 17,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 52,
                            "max_hp": 80,
                        },
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 25,
                            "act": 2,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 47,
                            "max_hp": 80,
                            "combat": {
                                "turn": 1,
                                "incoming_damage": 0,
                                "monsters": [{"id": "SphericGuardian", "current_hp": 20, "max_hp": 20}],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 1}]},
                    },
                ],
            )

            manifest, shadow = build_manifest([log], knowledge=StaticKnowledge.load())

        self.assertEqual(manifest["summary"][DIAGNOSTIC_EXCLUDED], 1)
        item = manifest["categories"][DIAGNOSTIC_EXCLUDED][0]
        self.assertEqual(item["reason"], "no_terminal_outcome")
        self.assertEqual(item["validation_grade"], "diagnostic")
        self.assertIn("no_terminal_outcome", item["validation_flags"])
        boss = item["validation_evidence"]["act1_boss"]
        self.assertTrue(boss["reached"])
        self.assertTrue(boss["cleared"])
        self.assertEqual(boss["enemy_ids"], ["TheGuardian"])
        self.assertEqual(boss["entry_potion_count"], 1)
        self.assertTrue(boss["prefix_pristine_clear"])
        self.assertEqual(boss["prefix_blockers"], [])
        self.assertEqual(boss["clear_step"], 2)
        self.assertEqual(boss["last_step"], 1)
        self.assertEqual(manifest["summary"]["validation"]["act1_boss_cleared"], 1)
        self.assertEqual(manifest["summary"]["validation"]["pristine_act1_boss_cleared"], 1)
        self.assertEqual(shadow["route_risk"], [])
        self.assertEqual(shadow["combat_search_labels"], [])

    def test_validation_summary_counts_non_pristine_act1_boss_clear_blockers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "recovered_clear.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 40,
                            "max_hp": 80,
                            "combat": {
                                "turn": 6,
                                "incoming_damage": 0,
                                "monsters": [{"id": "TheGuardian", "current_hp": 3, "max_hp": 240}],
                            },
                        },
                        "decision": {"actions": [{"action": "play_card", "card_index": 1, "target_index": 1}]},
                    },
                    {
                        "step": 2,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale target_index: target_index=2 outside current monster count 1.",
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "BOSS_REWARD",
                            "room_phase": "COMPLETE",
                            "floor": 17,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 40,
                            "max_hp": 80,
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([log], knowledge=StaticKnowledge.load())

        item = manifest["categories"][DIAGNOSTIC_EXCLUDED][0]
        boss = item["validation_evidence"]["act1_boss"]
        self.assertTrue(boss["cleared"])
        self.assertFalse(boss["prefix_pristine_clear"])
        self.assertEqual(boss["prefix_blockers"], ["recovered_action_race"])
        validation = manifest["summary"]["validation"]
        self.assertEqual(validation["act1_boss_cleared"], 1)
        self.assertEqual(validation["pristine_act1_boss_cleared"], 0)
        self.assertEqual(validation["act1_boss_prefix_blockers"], {"recovered_action_race": 1})
        self.assertEqual(validation["act1_boss_clear_blockers"], {"recovered_action_race": 1})
        self.assertEqual(shadow["route_risk"], [])

    def test_read_state_error_without_terminal_outcome_is_infra_blocked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "read_failed.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                        },
                    },
                    {
                        "step": 2,
                        "event": "error",
                        "error": "read_state_failed: Cannot reach MCPTheSpire at http://127.0.0.1:8080/mcp",
                        "actions": [],
                    },
                ],
            )

            manifest, shadow = build_manifest([log])

        self.assertEqual(manifest["summary"][INFRA_BLOCKED], 1)
        item = manifest["categories"][INFRA_BLOCKED][0]
        self.assertEqual(item["reason"], "mcp_unreachable")
        self.assertEqual(item["failure_attribution"], "mcp_execution")
        self.assertIn("mcp", item["failure_tags"])
        self.assertEqual(item["failure_evidence"]["mcp_read"]["step"], 2)
        self.assertEqual(item["failure_evidence"]["mcp_read"]["event"], "error")
        self.assertEqual(item["failure_evidence"]["mcp_read"]["last_state"]["screen_type"], "NONE")
        self.assertEqual(item["failure_evidence"]["mcp_read"]["last_state"]["floor"], 16)
        self.assertEqual(shadow["route_risk"], [])

    def test_repeated_reward_screen_without_terminal_is_infra_blocked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "boss_reward_stall.jsonl"
            reward_state = {
                "screen_type": "BOSS_REWARD",
                "room_phase": "COMPLETE",
                "floor": 17,
                "act": 1,
                "class": "IRONCLAD",
                "ascension_level": 0,
                "screen_state": {
                    "relics": [
                        {"id": "Black Blood", "name": "Black Blood"},
                        {"id": "Coffee Dripper", "name": "Coffee Dripper"},
                        {"id": "Sozu", "name": "Sozu"},
                    ]
                },
            }
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": reward_state,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 2,
                        "state": reward_state,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 3,
                        "state": reward_state,
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                ],
            )

            manifest, shadow = build_manifest([log])

        self.assertEqual(manifest["summary"][INFRA_BLOCKED], 1)
        item = manifest["categories"][INFRA_BLOCKED][0]
        self.assertEqual(item["reason"], "boss_reward_screen_stall")
        self.assertEqual(
            manifest["summary"]["classification_reasons"][INFRA_BLOCKED],
            {"boss_reward_screen_stall": 1},
        )
        self.assertEqual(item["failure_attribution"], "mcp_execution")
        self.assertIn("screen_stall", item["failure_tags"])
        self.assertIn("relic_collection", item["failure_tags"])
        self.assertEqual(item["validation_grade"], "infra_blocked")
        stall = item["failure_evidence"]["screen_stall"]
        self.assertEqual(stall["screen_type"], "BOSS_REWARD")
        self.assertEqual(stall["repeat_count"], 3)
        self.assertEqual(stall["first_step"], 1)
        self.assertEqual(stall["last_step"], 3)
        self.assertEqual(stall["initial_relic_count"], 3)
        self.assertEqual(stall["last_relic_count"], 3)
        self.assertEqual(stall["last_actions"], [{"action": "choose", "choice_index": 1}])
        self.assertEqual(manifest["summary"]["failure_attributions"], {"mcp_execution": 1})
        self.assertEqual(
            manifest["summary"]["failure_evidence"],
            {
                "runs_with_evidence": 1,
                "by_type": {"screen_stall": 1},
                "screen_stalls": {
                    "by_screen": {"BOSS_REWARD": 1},
                    "by_reason": {"boss_reward_screen_stall": 1},
                },
            },
        )
        self.assertEqual(shadow["route_risk"], [])

    def test_single_reward_screen_without_terminal_remains_diagnostic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "single_card_reward.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "CARD_REWARD",
                            "room_phase": "COMPLETE",
                            "floor": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "screen_state": {"cards": [{"id": "Shrug It Off", "name": "Shrug It Off"}]},
                        },
                        "decision": {"actions": [{"action": "skip"}]},
                    }
                ],
            )

            manifest, shadow = build_manifest([log])

        self.assertEqual(manifest["summary"][DIAGNOSTIC_EXCLUDED], 1)
        item = manifest["categories"][DIAGNOSTIC_EXCLUDED][0]
        self.assertEqual(item["reason"], "no_terminal_outcome")
        self.assertEqual(item["failure_attribution"], "diagnostic_incomplete")
        self.assertEqual(shadow["route_risk"], [])

    def test_unrecovered_action_race_is_infra_blocked(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "unrecovered_action.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 3,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                        },
                    },
                    {
                        "step": 1,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": False,
                    },
                ],
            )

            manifest, _ = build_manifest([log])

        self.assertEqual(manifest["summary"][INFRA_BLOCKED], 1)
        item = manifest["categories"][INFRA_BLOCKED][0]
        self.assertEqual(item["reason"], "unrecovered_action_race")
        self.assertEqual(item["recovered_actions"], 0)
        self.assertEqual(item["action_recovery_summary"]["recovered"], 0)
        self.assertEqual(item["action_recovery_summary"]["unrecovered"], 1)
        self.assertEqual(item["action_recovery_summary"]["by_kind"]["preflight_mismatch"], 1)
        action_recovery_summary = dict(manifest["summary"]["action_recovery"])
        action_recovery_examples = action_recovery_summary.pop("examples")
        self.assertEqual(
            action_recovery_summary,
            {
                "total": 1,
                "recovered": 0,
                "unrecovered": 1,
                "runs_with_action_recovery": 1,
                "runs_with_recovered_action": 0,
                "runs_with_unrecovered_action": 1,
                "by_status": {"preflight_mismatch": 1},
                "by_kind": {"preflight_mismatch": 1},
            },
        )
        self.assertEqual(action_recovery_examples[0]["source_log"], str(log))
        self.assertEqual(action_recovery_examples[0]["source_category"], INFRA_BLOCKED)
        self.assertEqual(action_recovery_examples[0]["step"], 1)
        self.assertFalse(action_recovery_examples[0]["recovered"])
        self.assertEqual(item["failure_attribution"], "mcp_execution")
        self.assertIn("action_execution", item["failure_tags"])

    def test_recovered_stale_potion_slot_is_counted_separately(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "stale_potion.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 6,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                        },
                    },
                    {
                        "step": 1,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale use_potion: potion_slot=1 points to unavailable potion ('FirePotion', 'Fire Potion').",
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 6,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, _ = build_manifest([log])

        item = manifest["categories"][CLEAN_TRAINABLE][0]
        self.assertEqual(item["recovered_actions"], 1)
        self.assertEqual(item["action_recovery_summary"]["by_kind"]["stale_potion_slot"], 1)

    def test_recovered_stale_potion_target_is_counted_separately(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "stale_potion_target.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 6,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                        },
                    },
                    {
                        "step": 1,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale target_index: target_index=2 outside current monster count 1 for use_potion.",
                    },
                    {
                        "step": 1,
                        "event": "action_result",
                        "action_status": "preflight_mismatch",
                        "recovered": True,
                        "last_error": "Preflight stale targeted use_potion: non-numeric potion_slot=1, target_index='bad'.",
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 6,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, _ = build_manifest([log])

        item = manifest["categories"][CLEAN_TRAINABLE][0]
        self.assertEqual(item["recovered_actions"], 2)
        self.assertEqual(item["action_recovery_summary"]["by_kind"]["stale_potion_target_index"], 2)
        self.assertEqual(manifest["summary"]["action_recovery"]["by_kind"], {"stale_potion_target_index": 2})

    def test_boss_loss_attribution_uses_readiness_and_potion_gap(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "boss_loss.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 38,
                            "max_hp": 80,
                            "deck": ["Strike_R", "Strike_R", "Strike_R", "Defend_R", "Defend_R", "Bash"],
                            "potions": [],
                            "boss_available": True,
                        },
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
                            "current_hp": 8,
                            "max_hp": 80,
                            "potions": [],
                            "combat": {
                                "turn": 4,
                                "incoming_damage": 30,
                                "monsters": [{"id": "TheGuardian"}],
                            },
                        },
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, _ = build_manifest([log], knowledge=StaticKnowledge.load())

        item = manifest["categories"][CLEAN_TRAINABLE][0]
        self.assertEqual(item["failure_attribution"], "potion_planning")
        self.assertIn("boss_combat", item["failure_tags"])
        self.assertIn("potion_planning", item["failure_tags"])
        self.assertIn("deck_quality", item["failure_tags"])
        self.assertIn("card_selection", item["failure_tags"])
        self.assertEqual(item["failure_evidence"]["boss_combat"]["enemy_ids"], ["TheGuardian"])
        self.assertIn("boss_no_tempo_potion", item["failure_evidence"]["pre_boss"]["readiness_risk_flags"])
        self.assertEqual(manifest["summary"]["failure_attributions"], {"potion_planning": 1})

    def test_act2_loss_does_not_reuse_stale_act1_boss_gap_as_primary_attribution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "act2_loss.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 75,
                            "max_hp": 80,
                            "deck": ["Strike_R", "Strike_R", "Strike_R", "Defend_R", "Defend_R", "Bash"],
                            "potions": [{"id": "FirePotion"}],
                            "boss_available": True,
                        },
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 21,
                            "act": 2,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 5,
                            "max_hp": 80,
                            "potions": [],
                            "combat": {
                                "turn": 3,
                                "incoming_damage": 24,
                                "monsters": [{"id": "Cultist"}, {"id": "Cultist"}, {"id": "Cultist"}],
                            },
                        },
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 21,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, _ = build_manifest([log], knowledge=StaticKnowledge.load())

        item = manifest["categories"][CLEAN_TRAINABLE][0]
        self.assertEqual(item["failure_attribution"], "combat_planning")
        self.assertNotIn("deck_quality", item["failure_tags"])
        self.assertEqual(item["failure_evidence"]["pre_boss"]["floor"], 15)
        self.assertEqual(manifest["summary"]["failure_attributions"], {"combat_planning": 1})

    def test_shadow_examples_can_be_enriched_with_static_knowledge(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "current_hp": 44,
                            "max_hp": 80,
                            "deck": ["local strike", "local defend", "local bash", "local weak", "local draw"],
                            "deck_cards": [
                                {"name": "local strike", "card_id": "Strike_R"},
                                {"name": "local defend", "card_id": "Defend_R"},
                                {"name": "local bash", "card_id": "Bash"},
                                {"name": "local weak", "card_id": "Clothesline"},
                                {"name": "local draw", "card_id": "Shrug It Off"},
                            ],
                            "potions": [
                                {"id": None, "name": "local liquid", "potion_id": "LiquidMemories"},
                                {"id": "BlockPotion"},
                                {"id": "SwiftPotion"},
                            ],
                            "relics": [
                                "local burning blood",
                                "local marbles",
                                "local lantern",
                                "local ink",
                            ],
                            "relic_items": [
                                {"id": "Burning Blood"},
                                {"name": "Bag of Marbles"},
                                {"id": "Lantern"},
                                {"id": "Ink Bottle"},
                            ],
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
                        "step": 2,
                        "state": {
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "current_hp": 24,
                            "max_hp": 80,
                            "potions": [
                                {"id": None, "name": "local liquid", "potion_id": "LiquidMemories"},
                                {"id": "BlockPotion"},
                                {"id": "SwiftPotion"},
                            ],
                            "relics": [
                                "local burning blood",
                                "local marbles",
                                "local lantern",
                                "local ink",
                            ],
                            "relic_items": [
                                {"id": "Burning Blood"},
                                {"name": "Bag of Marbles"},
                                {"id": "Lantern"},
                                {"id": "Ink Bottle"},
                            ],
                            "combat": {
                                "turn": 4,
                                "incoming_damage": 30,
                                "monsters": [{"id": "Hexaghost"}],
                            },
                        },
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            manifest, shadow = build_manifest([clean], knowledge=StaticKnowledge.load())

        self.assertEqual(manifest["static_knowledge"]["source_counts"]["cards"], StaticKnowledge.load().source_counts["cards"])
        coverage = manifest["summary"]["shadow_feature_coverage"]
        self.assertEqual(coverage["source"], "memory")
        self.assertEqual(coverage["total_rows"], 3)
        self.assertEqual(coverage["categories"]["route_risk"]["source_quality"], {"pristine": 1})
        self.assertEqual(coverage["categories"]["route_risk"]["feature_prefixes"]["deck_"]["rows_with_nonzero"], 1)
        self.assertEqual(coverage["categories"]["route_risk"]["feature_prefixes"]["potion_"]["rows_with_nonzero"], 1)
        self.assertEqual(coverage["categories"]["route_risk"]["feature_prefixes"]["relic_"]["rows_with_nonzero"], 1)
        self.assertEqual(coverage["categories"]["potion_tempo"]["feature_prefixes"]["enemy_"]["rows_with_nonzero"], 1)
        self.assertEqual(coverage["categories"]["potion_tempo"]["feature_prefixes"]["boss_"]["rows_with_nonzero"], 1)
        self.assertEqual(coverage["categories"]["pre_boss_deck_quality"]["feature_prefixes"]["boss_"]["rows_with_nonzero"], 1)
        self.assertEqual(coverage["feature_focus"]["categories_with_issues"], 0)
        self.assertFalse(coverage["feature_focus"]["categories"]["route_risk"]["attention_required"])
        self.assertEqual(
            coverage["feature_focus"]["categories"]["potion_tempo"]["nonzero_prefixes"],
            {"enemy_": 1, "potion_": 1},
        )
        route = shadow["route_risk"][0]
        potion = shadow["potion_tempo"][0]
        pre_boss = shadow["pre_boss_deck_quality"][0]
        self.assertEqual(route["deck_known_cards"], 5)
        self.assertEqual(route["deck_total_current_vulnerable"], 2)
        self.assertEqual(route["deck_total_current_weak"], 2)
        self.assertEqual(route["deck_total_current_draw"], 1)
        self.assertTrue(route["has_liquid_memories"])
        self.assertEqual(route["potion_draw_value"], 3)
        self.assertTrue(route["has_burning_blood"])
        self.assertEqual(route["relic_known_count"], 4)
        self.assertEqual(route["relic_tag_frontload"], 2)
        self.assertEqual(route["relic_turn_one_energy_value"], 1)
        self.assertEqual(route["relic_draw_every_cards_value"], 10)
        self.assertIn("readiness_score_elite", route)
        self.assertIn("readiness_gaps", route)
        self.assertEqual(potion["enemy_boss_count"], 1)
        self.assertEqual(potion["enemy_elite_or_boss_count"], 1)
        self.assertEqual(potion["enemy_total_expected_attack"], 36)
        self.assertEqual(potion["enemy_average_expected_attack"], 36.0)
        self.assertEqual(potion["enemy_status_pressure_count"], 1)
        self.assertEqual(potion["enemy_scaling_pressure_count"], 1)
        self.assertEqual(potion["enemy_frontload_check_count"], 1)
        self.assertEqual(potion["enemy_multi_enemy_pressure_count"], 0)
        self.assertEqual(potion["enemy_act_1_count"], 1)
        self.assertTrue(potion["boss_identity_known"])
        self.assertEqual(potion["boss_known_count"], 1)
        self.assertEqual(potion["boss_mechanic_hp_scaled_opening"], 1)
        self.assertEqual(potion["boss_potion_need_block"], 1)
        self.assertEqual(potion["boss_max_expected_attack"], 36)
        self.assertEqual(potion["boss_total_expected_attack"], 36)
        self.assertEqual(potion["boss_max_hit_count"], 6)
        self.assertEqual(potion["boss_max_burn_damage"], 2)
        self.assertEqual(potion["boss_max_upgraded_burn_damage"], 4)
        self.assertIn("LiquidMemories", potion["potion_ids"])
        self.assertEqual(potion["potion_block_value"], 12)
        self.assertEqual(potion["potion_draw_value"], 3)
        self.assertEqual(potion["relic_known_count"], 4)
        self.assertEqual(potion["relic_tag_vulnerable"], 1)
        self.assertEqual(potion["relic_turn_one_energy_value"], 1)
        self.assertEqual(potion["relic_draw_every_cards_value"], 10)
        self.assertFalse(pre_boss["boss_identity_known"])
        self.assertEqual(pre_boss["boss_possible_count"], 3)
        self.assertEqual(pre_boss["boss_mechanic_mode_shift"], 1)
        self.assertEqual(pre_boss["boss_mechanic_split_threshold"], 1)
        self.assertEqual(pre_boss["boss_need_premium_block"], 3)
        self.assertEqual(pre_boss["boss_total_expected_attack"], 110)
        self.assertEqual(pre_boss["boss_max_split_threshold_percent"], 50)
        self.assertEqual(pre_boss["boss_max_mode_shift_threshold"], 30)
        self.assertEqual(pre_boss["boss_max_sharp_hide_damage"], 3)
        self.assertEqual(pre_boss["boss_max_post_split_enemy_count"], 2)
        self.assertEqual(pre_boss["deck_tag_weak"], 1)
        self.assertEqual(pre_boss["deck_tag_premium_defense"], 1)
        self.assertEqual(pre_boss["deck_total_current_vulnerable"], 2)
        self.assertEqual(pre_boss["deck_total_current_weak"], 2)
        self.assertEqual(pre_boss["deck_total_current_draw"], 1)
        self.assertEqual(pre_boss["potion_draw_value"], 3)
        self.assertEqual(pre_boss["relic_tag_healing"], 1)
        self.assertEqual(pre_boss["relic_tag_boss_damage"], 1)
        self.assertEqual(pre_boss["relic_turn_one_energy_value"], 1)
        self.assertEqual(pre_boss["relic_draw_every_cards_value"], 10)
        self.assertIn("readiness_score_boss", pre_boss)
        self.assertFalse(pre_boss["boss_potion_gap"])

    def test_static_knowledge_deck_features_fall_back_to_card_pick_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {"screen_type": "CARD_REWARD", "floor": 1, "class": "IRONCLAD", "ascension_level": 4},
                        "decision": {"learn_card_pick": "Clothesline"},
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "current_hp": 44,
                            "max_hp": 80,
                            "deck": ["���", "����"],
                            "potions": [],
                            "boss_available": True,
                            "route_evaluation": {
                                "options": [{"choice_index": 1, "symbol": "B", "score": 80, "lookahead": {}}]
                            },
                        },
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            _, shadow = build_manifest([clean], knowledge=StaticKnowledge.load())

        pre_boss = shadow["pre_boss_deck_quality"][0]
        self.assertEqual(pre_boss["deck_known_cards"], 11)
        self.assertEqual(pre_boss["deck_unknown_cards"], 0)
        self.assertEqual(pre_boss["deck_tag_weak"], 1)
        self.assertEqual(pre_boss["deck_tag_starter"], 10)
        self.assertAlmostEqual(pre_boss["readiness_score_hp"], 63.3, places=1)
        self.assertTrue(pre_boss["boss_potion_gap"])

    def test_static_knowledge_deck_features_use_observed_card_reward_aliases(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean = root / "clean.jsonl"
            write_jsonl(
                clean,
                [
                    {
                        "step": 1,
                        "state": {
                            "screen_type": "CARD_REWARD",
                            "floor": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "card_reward_options": [{"name": "CN Shrug", "card_id": "Shrug It Off"}],
                        },
                    },
                    {
                        "step": 2,
                        "state": {
                            "screen_type": "MAP",
                            "floor": 15,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "current_hp": 44,
                            "max_hp": 80,
                            "deck": ["Strike_R", "CN Shrug"],
                            "potions": [],
                            "boss_available": True,
                            "route_evaluation": {
                                "options": [{"choice_index": 1, "symbol": "B", "score": 80, "lookahead": {}}]
                            },
                        },
                        "decision": {"actions": [{"action": "choose", "choice_index": 1}]},
                    },
                    {
                        "step": 3,
                        "state": {
                            "screen_type": "GAME_OVER",
                            "floor": 16,
                            "class": "IRONCLAD",
                            "ascension_level": 4,
                            "outcome": {"victory": False},
                        },
                    },
                ],
            )

            _, shadow = build_manifest([clean], knowledge=StaticKnowledge.load())

        pre_boss = shadow["pre_boss_deck_quality"][0]
        self.assertEqual(pre_boss["deck_known_cards"], 2)
        self.assertEqual(pre_boss["deck_unknown_cards"], 0)
        self.assertEqual(pre_boss["deck_tag_block"], 1)
        self.assertEqual(pre_boss["deck_total_current_draw"], 1)


if __name__ == "__main__":
    unittest.main()
