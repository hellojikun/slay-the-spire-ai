import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.shadow_feature_audit import (
    audit_adjacent_shadow_features,
    audit_shadow_examples,
    audit_shadow_features,
    candidate_shadow_dirs_for_manifest,
    main,
)
from slay_ai.shadow_quality import LEGACY_UNGRADED


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class ShadowFeatureAuditTests(unittest.TestCase):
    def test_audits_in_memory_shadow_examples_like_file_rows(self):
        summary = audit_shadow_examples(
            {
                "route_risk": [
                    {
                        "source_validation_grade": "pristine",
                        "deck_total_current_draw": 1,
                        "deck_unknown_cards": 2,
                        "boss_mechanic_mode_shift": True,
                    }
                ],
                "combat_search_labels": [
                    {
                        "source_validation_grade": "usable_with_recoveries",
                        "enemy_total_expected_attack": 36,
                        "enemy_unknown_count": 1,
                    }
                ],
            }
        )

        self.assertEqual(summary["source"], "memory")
        self.assertEqual(summary["total_files"], 0)
        self.assertEqual(summary["total_rows"], 2)
        self.assertIn("combat_search", summary["categories"])
        self.assertNotIn("combat_search_labels", summary["categories"])
        route = summary["categories"]["route_risk"]
        combat = summary["categories"]["combat_search"]
        self.assertEqual(route["feature_prefixes"]["deck_"]["rows_with_nonzero"], 1)
        self.assertEqual(route["feature_prefixes"]["boss_"]["rows_with_nonzero"], 1)
        self.assertEqual(route["unknown_static_features"]["deck_unknown_cards"]["total"], 2)
        self.assertEqual(route["unknown_static_features"]["deck_unknown_cards"]["nonzero"], 1)
        self.assertEqual(combat["feature_prefixes"]["enemy_"]["fields"]["enemy_total_expected_attack"]["nonzero"], 1)
        self.assertEqual(combat["unknown_static_features"]["enemy_unknown_count"]["total"], 1)
        self.assertEqual(combat["source_quality"], {"usable_with_recoveries": 1})
        focus = summary["feature_focus"]
        self.assertEqual(focus["categories_with_issues"], 2)
        self.assertEqual(focus["unknown_static_total"], 3)
        self.assertEqual(focus["unknown_static_features"], {
            "combat_search:enemy_unknown_count": 1,
            "route_risk:deck_unknown_cards": 2,
        })
        self.assertTrue(focus["categories"]["route_risk"]["attention_required"])
        self.assertEqual(focus["categories"]["route_risk"]["issues"], ["unknown_static_features"])
        self.assertEqual(focus["categories"]["route_risk"]["nonzero_prefixes"], {
            "deck_": 1,
        })
        self.assertEqual(focus["categories"]["combat_search"]["missing_prefixes"], ["deck_"])
        self.assertEqual(focus["categories"]["combat_search"]["issues"], ["missing_prefixes", "unknown_static_features"])

    def test_audits_static_feature_prefix_coverage_by_shadow_category(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [
                    {
                        "source_log": "run_a.jsonl",
                        "source_validation_grade": "pristine",
                        "deck_total_current_draw": 2,
                        "deck_total_current_energy": 0,
                        "relic_draw_every_cards_value": 10,
                        "boss_mechanic_mode_shift": True,
                    },
                    {
                        "source_log": "run_b.jsonl",
                        "source_validation_grade": "usable_with_recoveries",
                        "deck_total_current_draw": 0,
                        "potion_vulnerable_value": 3,
                        "enemy_total_expected_attack": 36,
                        "boss_mechanic_mode_shift": False,
                    },
                ],
            )
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "source_log": "run_c.jsonl",
                        "enemy_total_expected_attack": 0,
                        "enemy_elite_or_boss_count": 1,
                        "label_first_card_key": "Bash",
                    }
                ],
            )

            summary = audit_shadow_features([shadow])

        self.assertEqual(summary["total_files"], 2)
        self.assertEqual(summary["total_rows"], 3)
        route = summary["categories"]["route_risk"]
        combat = summary["categories"]["combat_search"]
        self.assertEqual(route["rows"], 2)
        self.assertEqual(route["source_quality"], {"pristine": 1, "usable_with_recoveries": 1})
        self.assertEqual(combat["source_quality"], {LEGACY_UNGRADED: 1})

        deck = route["feature_prefixes"]["deck_"]
        self.assertEqual(deck["field_count"], 2)
        self.assertEqual(deck["rows_with_any"], 2)
        self.assertEqual(deck["rows_with_nonzero"], 1)
        self.assertEqual(deck["fields"]["deck_total_current_draw"]["present"], 2)
        self.assertEqual(deck["fields"]["deck_total_current_draw"]["nonzero"], 1)
        self.assertEqual(deck["fields"]["deck_total_current_draw"]["coverage"], 1.0)
        self.assertEqual(deck["fields"]["deck_total_current_energy"]["nonzero_rate"], 0.0)

        potion = route["feature_prefixes"]["potion_"]
        self.assertEqual(potion["rows_with_any"], 1)
        self.assertEqual(potion["rows_with_nonzero"], 1)
        self.assertEqual(potion["fields"]["potion_vulnerable_value"]["nonzero"], 1)

        enemy = combat["feature_prefixes"]["enemy_"]
        self.assertEqual(enemy["field_count"], 2)
        self.assertEqual(enemy["rows_with_any"], 1)
        self.assertEqual(enemy["rows_with_nonzero"], 1)
        self.assertEqual(enemy["fields"]["enemy_total_expected_attack"]["nonzero"], 0)
        self.assertEqual(enemy["fields"]["enemy_elite_or_boss_count"]["nonzero"], 1)
        focus = summary["feature_focus"]
        self.assertEqual(focus["categories"]["route_risk"]["zero_prefixes"], [])
        self.assertFalse(focus["categories"]["route_risk"]["attention_required"])
        self.assertEqual(focus["categories"]["combat_search"]["missing_prefixes"], ["deck_"])
        self.assertTrue(focus["categories"]["combat_search"]["attention_required"])

    def test_cli_writes_custom_prefix_audit_json(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            output = root / "audit.json"
            write_jsonl(
                shadow / "potion_tempo.jsonl",
                [
                    {
                        "source_validation_grade": "pristine",
                        "potion_draw_value": 3,
                        "deck_total_current_draw": 1,
                    }
                ],
            )

            code = main([str(shadow), "--prefix", "potion_", "--output", str(output)])
            summary = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(summary["prefixes"], ["potion_"])
        self.assertEqual(summary["categories"]["potion_tempo"]["feature_prefixes"]["potion_"]["field_count"], 1)
        self.assertNotIn("deck_", summary["categories"]["potion_tempo"]["feature_prefixes"])
        self.assertEqual(summary["feature_focus"]["categories"]["potion_tempo"]["expected_prefixes"], ["potion_"])
        self.assertNotIn("route_risk", summary["feature_focus"]["categories"])

    def test_audits_adjacent_shadow_features_from_manifest_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe777_a0.json"
            manifest.write_text("{}\n", encoding="utf-8")
            write_jsonl(
                root / "shadow_probe777_a0" / "route_risk.jsonl",
                [{"source_validation_grade": "pristine", "deck_total_current_damage": 38}],
            )

            summary, source = audit_adjacent_shadow_features(manifest)

        self.assertEqual(source, "shadow_dir_fallback")
        self.assertEqual(summary["source"], "shadow_dir_fallback")
        self.assertEqual(summary["total_rows"], 1)
        self.assertEqual(summary["categories"]["route_risk"]["feature_prefixes"]["deck_"]["rows_with_nonzero"], 1)

    def test_audits_suffixed_adjacent_shadow_features_from_manifest_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe778_a0.json"
            manifest.write_text("{}\n", encoding="utf-8")
            write_jsonl(
                root / "shadow_probe778_a0_regen" / "route_risk.jsonl",
                [{"source_validation_grade": "pristine", "deck_total_current_damage": 38}],
            )

            summary, source = audit_adjacent_shadow_features(manifest)
            candidates = candidate_shadow_dirs_for_manifest(manifest)

        self.assertEqual(source, "shadow_dir_fallback")
        self.assertIn(root / "shadow_probe778_a0_regen", candidates)
        self.assertEqual(summary["total_rows"], 1)

    def test_adjacent_shadow_features_reports_empty_shadow_dir(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "training_manifest_probe779_a0.json"
            manifest.write_text("{}\n", encoding="utf-8")
            write_jsonl(root / "shadow_probe779_a0" / "route_risk.jsonl", [])

            summary, source = audit_adjacent_shadow_features(manifest)

        self.assertEqual(summary, {})
        self.assertEqual(source, "shadow_dir_empty")


if __name__ == "__main__":
    unittest.main()
