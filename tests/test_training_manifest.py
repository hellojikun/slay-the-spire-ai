import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.training_manifest import (
    CLEAN_TRAINABLE,
    DIAGNOSTIC_EXCLUDED,
    INFRA_BLOCKED,
    build_manifest,
    clean_log_paths_from_manifest,
)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


class TrainingManifestTests(unittest.TestCase):
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
                            "relics": ["Burning Blood"],
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

        self.assertEqual(manifest["summary"][CLEAN_TRAINABLE], 1)
        self.assertEqual(manifest["summary"][INFRA_BLOCKED], 1)
        self.assertEqual(manifest["summary"][DIAGNOSTIC_EXCLUDED], 1)
        clean_item = manifest["categories"][CLEAN_TRAINABLE][0]
        self.assertEqual(clean_item["reason"], "completed_clean")
        self.assertEqual(clean_item["recovered_actions"], 1)
        self.assertEqual(clean_item["card_picks"], 1)
        self.assertEqual(len(shadow["route_risk"]), 1)
        self.assertEqual(len(shadow["potion_tempo"]), 1)
        self.assertEqual(len(shadow["pre_boss_deck_quality"]), 1)
        self.assertEqual(shadow["pre_boss_deck_quality"][0]["block_cards"], 2)

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


if __name__ == "__main__":
    unittest.main()
