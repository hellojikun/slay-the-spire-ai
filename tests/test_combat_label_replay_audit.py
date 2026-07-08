import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.combat_label_replay_audit import build_replay_report, compact_replay_report, main


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class CombatLabelReplayAuditTests(unittest.TestCase):
    def test_replays_excluded_label_against_current_policy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            shadow = root / "shadow"
            write_jsonl(
                log,
                [
                    {
                        "step": 10,
                        "state": {
                            "in_game": True,
                            "screen_type": "NONE",
                            "room_phase": "COMBAT",
                            "floor": 16,
                            "act": 1,
                            "class": "IRONCLAD",
                            "ascension_level": 0,
                            "current_hp": 18,
                            "max_hp": 80,
                            "combat": {
                                "turn": 8,
                                "player": {"current_hp": 18, "max_hp": 80, "current_energy": 1, "block": 0},
                                "hand_cards": [
                                    {
                                        "id": "Defend_R",
                                        "name": "Defend",
                                        "type": "SKILL",
                                        "cost": 1,
                                        "block": 5,
                                        "is_playable": True,
                                    },
                                    {
                                        "id": "Strike_R",
                                        "name": "Strike",
                                        "type": "ATTACK",
                                        "cost": 1,
                                        "damage": 6,
                                        "is_playable": True,
                                        "has_target": True,
                                    },
                                ],
                                "monsters": [{"id": "Hexaghost", "name": "Hexaghost", "hp": 4, "max_hp": 250, "move": {"damage": 12}}],
                            },
                        },
                    }
                ],
            )
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_log": str(log),
                        "step": 10,
                        "act": 1,
                        "floor": 16,
                        "enemy_ids": ["Hexaghost"],
                        "actual_action": "play_card",
                        "actual_card_index": 1,
                        "label_action": "play_card",
                        "label_card_index": 2,
                        "label_target_index": 1,
                        "label_first_card_key": "Strike_R",
                        "label_missed_direct_kill": True,
                    }
                ],
            )

            report = build_replay_report([shadow])

        self.assertEqual(report["excluded_rows"], 1)
        self.assertEqual(report["replayed_rows"], 1)
        self.assertEqual(report["current_policy_matches_label"], 1)
        self.assertEqual(report["replay_outcomes"], {"current_policy_matches_label": 1})
        self.assertEqual(report["examples"][0]["policy_action"], {"action": "play_card", "card_index": 2, "target_index": 1})

    def test_missing_source_log_is_reported(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_labels.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "source_log": str(Path(tmp) / "missing.jsonl"),
                        "step": 10,
                        "label_missed_direct_kill": True,
                    }
                ],
            )

            report = build_replay_report([path])

        self.assertEqual(report["excluded_rows"], 1)
        self.assertEqual(report["missing_source_log"], 1)
        self.assertIn("missing_source_log=1", report["status_line"])

    def test_example_limit_zero_suppresses_examples(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_labels.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "source_log": str(Path(tmp) / "missing.jsonl"),
                        "step": 10,
                        "label_missed_direct_kill": True,
                    }
                ],
            )

            report = build_replay_report([path], example_limit=0)

        self.assertEqual(report["excluded_rows"], 1)
        self.assertEqual(report["example_limit"], 0)
        self.assertEqual(report["example_count"], 0)
        self.assertEqual(report["examples"], [])

    def test_compact_replay_report_keeps_counts_and_examples(self):
        compact = compact_replay_report(
            {
                "status_line": "combat_label_replay_audit files=1 excluded=2 replayed=2 matches_label=1 matches_actual=0 other=1",
                "resolved_file_count": 1,
                "excluded_rows": 2,
                "replayed_rows": 2,
                "current_policy_matches_label": 1,
                "current_policy_matches_actual": 0,
                "current_policy_other": 1,
                "missing_source_log": 0,
                "missing_source_step": 0,
                "policy_errors": 0,
                "replay_outcomes": {"current_policy_matches_label": 1, "current_policy_other": 1},
                "exclusion_reasons": {"missed_direct_kill": 2},
                "examples": [{"source_log": "run.jsonl", "step": 42}, {"source_log": "later.jsonl"}],
            },
            example_limit=1,
        )

        self.assertEqual(compact["current_policy_matches_label"], 1)
        self.assertEqual(compact["current_policy_other"], 1)
        self.assertEqual(compact["replay_outcomes"], {"current_policy_matches_label": 1, "current_policy_other": 1})
        self.assertEqual(compact["exclusion_reasons"], {"missed_direct_kill": 2})
        self.assertEqual(compact["examples"], [{"source_log": "run.jsonl", "step": 42}])

    def test_cli_writes_ascii_safe_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "combat_search_labels.jsonl"
            output = root / "replay.json"
            write_jsonl(path, [{"source_log": str(root / "missing.jsonl"), "step": 1, "label_missed_direct_kill": True}])

            code = main([str(path), "--output", str(output), "--compact"])
            raw = output.read_text(encoding="utf-8")
            payload = json.loads(raw)

        self.assertEqual(code, 0)
        self.assertTrue(all(ord(char) < 128 for char in raw))
        self.assertEqual(payload["missing_source_log"], 1)


if __name__ == "__main__":
    unittest.main()
