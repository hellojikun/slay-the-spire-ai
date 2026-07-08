import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.static_knowledge import StaticKnowledge
from slay_ai.static_knowledge_gaps import build_gap_report, compact_gap_report, main


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class StaticKnowledgeGapTests(unittest.TestCase):
    def test_reports_concrete_unknown_entities_from_logs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "act": 1,
                            "deck": ["Bash", "Mystery Strike"],
                            "card_reward_options": [{"id": "Skip"}, {"id": "Mystery Strike"}],
                            "potions": [{"id": "GhostPotion"}],
                            "relics": [{"name": "Mystery Relic"}],
                            "boss_name": "UnseenBoss",
                            "combat": {
                                "hand_cards": ["Strike_R", "Mystery Skill"],
                                "monsters": [{"id": "UnknownBlob"}, {"id": "Hexaghost"}],
                            },
                        },
                    }
                ],
            )

            report = build_gap_report([log], knowledge=StaticKnowledge.load(), source_limit=1)

        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["resolved_log_count"], 1)
        self.assertEqual(report["totals"]["cards"], {"occurrences": 3, "unique": 2})
        self.assertEqual(report["totals"]["potions"], {"occurrences": 1, "unique": 1})
        self.assertEqual(report["totals"]["relics"], {"occurrences": 1, "unique": 1})
        self.assertEqual(report["totals"]["monsters"], {"occurrences": 1, "unique": 1})
        self.assertEqual(report["totals"]["bosses"], {"occurrences": 1, "unique": 1})
        self.assertEqual(report["entities"]["cards"]["items"][0]["entity"], "Mystery Strike")
        self.assertEqual(report["entities"]["cards"]["items"][0]["count"], 2)
        self.assertEqual(len(report["entities"]["cards"]["items"][0]["sources"]), 1)
        self.assertEqual(report["entities"]["potions"]["items"][0]["entity"], "GhostPotion")
        self.assertEqual(report["entities"]["relics"]["items"][0]["entity"], "Mystery Relic")
        self.assertEqual(report["entities"]["monsters"]["items"][0]["entity"], "UnknownBlob")
        self.assertEqual(report["entities"]["bosses"]["items"][0]["entity"], "UnseenBoss")
        self.assertIn("missing=7", report["status_line"])
        self.assertIn("cards=3/2", report["status_line"])

    def test_compact_gap_report_keeps_bounded_entity_sources(self):
        report = {
            "status_line": "static_knowledge_gaps logs=1 missing=2 cards=2/1",
            "resolved_log_count": 1,
            "total_missing_occurrences": 2,
            "observed_aliases": {"cards": 3},
            "warnings": [],
            "totals": {"cards": {"occurrences": 2, "unique": 1}},
            "entities": {
                "cards": {
                    "items": [
                        {
                            "entity": "Mystery Strike",
                            "count": 2,
                            "sources": [
                                {"path": "run.jsonl", "step": 1, "field": "deck"},
                                {"path": "run.jsonl", "step": 2, "field": "combat.hand_cards"},
                                {"path": "run.jsonl", "step": 3, "field": "discard_pile"},
                            ],
                        }
                    ]
                }
            },
        }

        compact = compact_gap_report(report, source_limit=2)

        self.assertEqual(compact["total_missing_occurrences"], 2)
        self.assertEqual(compact["observed_aliases"], {"cards": 3})
        self.assertEqual(compact["totals"]["cards"], {"occurrences": 2, "unique": 1})
        self.assertEqual(compact["entities"]["cards"][0]["entity"], "Mystery Strike")
        self.assertEqual(len(compact["entities"]["cards"][0]["sources"]), 2)

    def test_observed_card_reward_aliases_do_not_count_as_gaps(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "deck": ["CN Shrug", "Mystery Strike"],
                            "card_reward_options": [{"name": "CN Shrug", "card_id": "Shrug It Off"}],
                        },
                    }
                ],
            )

            report = build_gap_report([log], knowledge=StaticKnowledge.load(), source_limit=1)

        self.assertEqual(report["observed_aliases"], {"cards": 1})
        self.assertEqual(report["totals"]["cards"], {"occurrences": 1, "unique": 1})
        self.assertEqual(report["entities"]["cards"]["items"][0]["entity"], "Mystery Strike")

    def test_explicit_identity_fields_do_not_count_as_gaps(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            write_jsonl(
                log,
                [
                    {
                        "step": 1,
                        "state": {
                            "deck": ["unmapped display"],
                            "deck_cards": [{"name": "unmapped display", "card_id": "Bash"}],
                            "potions": [{"name": "unmapped display", "potion_id": "FearPotion"}],
                            "relics": ["unmapped display"],
                            "relic_items": [{"name": "unmapped display", "relic_id": "Anchor"}],
                            "screen_state": {
                                "rewards": [
                                    {
                                        "reward_type": "RELIC",
                                        "name": "unmapped display",
                                        "relic_id": "Anchor",
                                    }
                                ]
                            },
                        },
                    }
                ],
            )

            report = build_gap_report([log], knowledge=StaticKnowledge.load(), source_limit=1)

        self.assertEqual(report["total_missing_occurrences"], 0)
        self.assertEqual(report["totals"]["cards"], {"occurrences": 0, "unique": 0})
        self.assertEqual(report["totals"]["potions"], {"occurrences": 0, "unique": 0})
        self.assertEqual(report["totals"]["relics"], {"occurrences": 0, "unique": 0})

    def test_cli_writes_gap_report_for_directory_inputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            output = root / "gaps.json"
            write_jsonl(logs / "b.jsonl", [{"step": 2, "state": {"potions": [{"id": "GhostPotion"}]}}])
            write_jsonl(logs / "a.jsonl", [{"step": 1, "state": {"deck": ["Mystery Strike"]}}])

            code = main([str(logs), "--output", str(output), "--compact", "--source-limit", "2"])
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["resolved_logs"], [str(logs / "a.jsonl"), str(logs / "b.jsonl")])
        self.assertEqual(payload["totals"]["cards"], {"occurrences": 1, "unique": 1})
        self.assertEqual(payload["totals"]["potions"], {"occurrences": 1, "unique": 1})
        self.assertEqual(payload["entities"]["cards"]["items"][0]["sources"][0]["field"], "deck")

    def test_cli_sanitizes_bad_unicode_for_json_consumers(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "run.jsonl"
            output = root / "gaps.json"
            write_jsonl(log, [{"step": 1, "state": {"deck": ["Bad\udcffCard"]}}])

            self.assertEqual(main([str(log), "--output", str(output), "--compact"]), 0)
            raw = output.read_text(encoding="utf-8")
            payload = json.loads(raw)

        self.assertTrue(all(ord(char) < 128 for char in raw))
        self.assertEqual(payload["totals"]["cards"], {"occurrences": 1, "unique": 1})
        self.assertEqual(payload["entities"]["cards"]["items"][0]["entity"], "Bad?Card")

    def test_empty_input_reports_no_resolved_logs_warning(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()

            report = build_gap_report([logs], knowledge=StaticKnowledge.load())

        self.assertEqual(report["warnings"], ["no_resolved_logs"])
        self.assertIn("warnings=no_resolved_logs", report["status_line"])
        self.assertEqual(report["total_missing_occurrences"], 0)


if __name__ == "__main__":
    unittest.main()
