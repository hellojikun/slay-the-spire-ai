import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.combat_label_audit import (
    build_audit_report,
    compact_audit_report,
    compact_excluded_label_examples,
    main,
    prioritize_label_examples,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class CombatLabelAuditTests(unittest.TestCase):
    def test_reports_excluded_combat_search_labels(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_log": "run_a.jsonl",
                        "source_validation_grade": "pristine",
                        "floor": 16,
                        "turn": 10,
                        "step": 120,
                        "current_hp": 7,
                        "incoming": 12,
                        "enemy_ids": ["Hexaghost"],
                        "enemy_hps": [4],
                        "hand_ids": ["Defend_R", "Strike_R"],
                        "label_first_card_key": "Defend_R",
                        "label_sequence_card_keys": ["Defend_R"],
                        "direct_kill_available": True,
                        "direct_kill_card_keys": ["Strike_R"],
                        "direct_kill_enemy_hp": 4,
                        "label_missed_direct_kill": True,
                    },
                    {
                        "source_log": "run_b.jsonl",
                        "source_validation_grade": "usable_with_recoveries",
                        "floor": 16,
                        "turn": 8,
                        "label_first_card_key": "Bash",
                        "label_missed_single_card_search": True,
                    },
                    {
                        "source_log": "run_c.jsonl",
                        "source_validation_grade": "pristine",
                        "label_first_card_key": "Bash",
                        "label_sequence_card_keys": ["Bash"],
                    },
                ],
            )

            report = build_audit_report([shadow])

        self.assertEqual(report["resolved_file_count"], 1)
        self.assertEqual(report["rows"], 3)
        self.assertEqual(report["accepted_rows"], 1)
        self.assertEqual(report["excluded_rows"], 2)
        self.assertEqual(report["exclusion_reasons"], {"missed_direct_kill": 1, "missed_single_card_search": 1})
        self.assertEqual(report["source_quality"], {"pristine": 2, "usable_with_recoveries": 1})
        self.assertIn("excluded=2", report["status_line"])
        self.assertEqual(report["examples"][0]["source_log"], "run_a.jsonl")
        self.assertEqual(report["examples"][0]["direct_kill_card_keys"], ["Strike_R"])
        self.assertEqual(report["examples"][0]["enemy_ids"], ["Hexaghost"])

    def test_source_quality_filter_keeps_exclusions_but_counts_filtered_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_labels.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "source_validation_grade": "usable_with_recoveries",
                        "label_first_card_key": "Defend_R",
                        "label_missed_direct_kill": True,
                    },
                    {
                        "source_validation_grade": "pristine",
                        "label_first_card_key": "Defend_R",
                        "label_missed_direct_kill": True,
                    },
                ],
            )

            report = build_audit_report([path], source_quality="pristine")

        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["filtered_rows"], 1)
        self.assertEqual(report["excluded_rows"], 1)
        self.assertEqual(report["exclusion_reasons"], {"missed_direct_kill": 1})

    def test_compact_audit_report_bounds_examples(self):
        report = {
            "status_line": "combat_label_audit files=1 rows=3 excluded=2 missed_direct_kill=2",
            "resolved_file_count": 1,
            "rows": 3,
            "accepted_rows": 1,
            "excluded_rows": 2,
            "exclusion_reasons": {"missed_direct_kill": 2},
            "source_quality": {"pristine": 3},
            "examples": [{"step": 1}, {"step": 2}],
        }

        compact = compact_audit_report(report, example_limit=1)

        self.assertEqual(compact["excluded_rows"], 2)
        self.assertEqual(compact["exclusion_reasons"], {"missed_direct_kill": 2})
        self.assertEqual(compact["examples"], [{"step": 1}])

    def test_compact_excluded_label_examples_can_summarize_in_memory_rows(self):
        examples = compact_excluded_label_examples(
            [
                {"label_first_card_key": "Strike_R"},
                {
                    "source_log": "run.jsonl",
                    "turn": 9,
                    "label_first_card_key": "Defend_R",
                    "direct_kill_card_keys": ["Strike_R"],
                    "enemy_ids": ["Hexaghost"],
                    "label_missed_direct_kill": True,
                },
            ]
        )

        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["reasons"], ["missed_direct_kill"])
        self.assertEqual(examples[0]["source_log"], "run.jsonl")
        self.assertEqual(examples[0]["direct_kill_card_keys"], ["Strike_R"])

    def test_compact_examples_prioritize_act1_boss_context(self):
        examples = compact_excluded_label_examples(
            [
                {
                    "source_log": "early.jsonl",
                    "act": 1,
                    "floor": 2,
                    "incoming": 10,
                    "enemy_ids": ["AcidSlime_M"],
                    "label_missed_direct_kill": True,
                },
                {
                    "source_log": "boss.jsonl",
                    "act": 1,
                    "floor": 16,
                    "incoming": 24,
                    "enemy_ids": ["Hexaghost"],
                    "label_missed_direct_kill": True,
                },
                {
                    "source_log": "late.jsonl",
                    "act": 2,
                    "floor": 29,
                    "incoming": 32,
                    "enemy_ids": ["Shelled Parasite"],
                    "label_missed_direct_kill": True,
                },
            ],
            limit=2,
        )

        self.assertEqual([example["source_log"] for example in examples], ["boss.jsonl", "late.jsonl"])

    def test_prioritize_label_examples_sorts_compact_examples(self):
        examples = prioritize_label_examples(
            [
                {"source_log": "early.jsonl", "act": 1, "floor": 2, "enemy_ids": ["JawWorm"]},
                {"source_log": "boss.jsonl", "act": 1, "floor": 16, "enemy_ids": ["SlimeBoss"]},
            ],
            limit=1,
        )

        self.assertEqual(examples[0]["source_log"], "boss.jsonl")

    def test_audit_examples_prioritize_act1_boss_context(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "combat_search_labels.jsonl"
            write_jsonl(
                path,
                [
                    {
                        "source_log": "early.jsonl",
                        "act": 1,
                        "floor": 2,
                        "incoming": 10,
                        "enemy_ids": ["AcidSlime_M"],
                        "label_missed_direct_kill": True,
                    },
                    {
                        "source_log": "boss.jsonl",
                        "act": 1,
                        "floor": 16,
                        "incoming": 24,
                        "enemy_ids": ["TheGuardian"],
                        "label_missed_direct_kill": True,
                    },
                ],
            )

            report = build_audit_report([path], example_limit=1)

        self.assertEqual(report["examples"][0]["source_log"], "boss.jsonl")
        self.assertEqual(report["examples"][0]["enemy_ids"], ["TheGuardian"])

    def test_cli_writes_ascii_safe_report(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "combat_search_labels.jsonl"
            output = root / "audit.json"
            write_jsonl(
                path,
                [
                    {
                        "source_validation_grade": "pristine",
                        "label_first_card_key": "防御",
                        "label_missed_direct_kill": True,
                    }
                ],
            )

            code = main([str(path), "--output", str(output), "--compact"])
            raw = output.read_text(encoding="utf-8")
            payload = json.loads(raw)

        self.assertEqual(code, 0)
        self.assertTrue(all(ord(char) < 128 for char in raw))
        self.assertEqual(payload["excluded_rows"], 1)


if __name__ == "__main__":
    unittest.main()
