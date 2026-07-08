import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.train_external_structure_priors import (
    CARD_PURGE_PRIORS_FILE,
    CARD_REWARD_DECISIONS_FILE,
    DECK_CYCLE_PRIORS_FILE,
    STRUCTURE_MODEL_FILE,
    train_external_structure_priors,
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class TrainExternalStructurePriorsTests(unittest.TestCase):
    def test_trains_take_skip_purge_and_cycle_rows_as_isolated_external_priors(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw" / "runs.jsonl"
            rows_dir = root / "rows"
            model_dir = root / "models"
            write_jsonl(
                raw,
                [
                    {
                        "character_chosen": "IRONCLAD",
                        "ascension_level": 20,
                        "victory": True,
                        "floor_reached": 57,
                        "score": 4000,
                        "card_choices": [
                            {"floor": 1, "picked": "Battle Trance", "not_picked": ["Clash", "Flex"]},
                            {"floor": 4, "picked": "SKIP", "not_picked": ["Clash", "Fire Breathing"]},
                        ],
                        "items_purged": ["Strike_R"],
                        "items_purged_floors": [8],
                        "purchased_purges": 1,
                        "master_deck": ["Strike_R", "Defend_R", "Bash", "Battle Trance"],
                    },
                    {
                        "character_chosen": "IRONCLAD",
                        "ascension_level": 20,
                        "victory": False,
                        "floor_reached": 10,
                        "card_choices": [
                            {"floor": 2, "picked": "SKIP", "not_picked": ["Clash", "Wild Strike"]}
                        ],
                        "master_deck": ["Strike_R", "Defend_R", "Bash"],
                    },
                ],
            )

            summary = train_external_structure_priors(
                [raw],
                source_id="unit_external",
                source_uri="https://example.test/unit",
                source_weight=0.1,
                rows_dir=rows_dir,
                model_dir=model_dir,
                backend="stats",
            )

            reward_rows = read_jsonl(rows_dir / CARD_REWARD_DECISIONS_FILE)
            purge_rows = read_jsonl(rows_dir / CARD_PURGE_PRIORS_FILE)
            deck_rows = read_jsonl(rows_dir / DECK_CYCLE_PRIORS_FILE)
            model = json.loads((model_dir / STRUCTURE_MODEL_FILE).read_text(encoding="utf-8"))

        self.assertEqual(summary["status"], "trained")
        self.assertEqual(summary["runs"], 2)
        self.assertEqual(summary["reward_decision_rows"], 3)
        self.assertEqual(summary["purge_rows"], 1)
        self.assertEqual(summary["deck_cycle_rows"], 2)
        self.assertFalse(summary["runtime_authority"])
        self.assertFalse(summary["runtime_default_enabled"])
        self.assertTrue(summary["does_not_control_live_mcp"])
        self.assertIn("runtime_authority", summary["forbidden_uses"])

        self.assertEqual([row["decision"] for row in reward_rows], ["take", "skip", "skip"])
        self.assertEqual(reward_rows[1]["options"], ["Clash", "Fire Breathing"])
        self.assertEqual(reward_rows[1]["source_validation_grade"], "external_prior")
        self.assertEqual(reward_rows[1]["source_validation_flags"], ["external", "not_mcp", "not_pristine"])
        self.assertEqual(purge_rows[0]["removed_base"], "Strike_R")
        self.assertTrue(purge_rows[0]["likely_shop_purge"])
        self.assertEqual(deck_rows[0]["card_reward_skip_count"], 1)
        self.assertGreaterEqual(deck_rows[0]["deck_size"], 4)

        self.assertEqual(model["metadata"]["model_kind"], "external_structure_prior")
        self.assertEqual(model["metadata"]["training_source_quality"], "external_prior")
        self.assertFalse(model["metadata"]["runtime_authority"])
        self.assertIn("IRONCLAD::Battle Trance", model["card_reward"]["take_card_deltas"])
        self.assertIn("IRONCLAD::Strike", model["purge"]["removed_card_deltas"])
        self.assertIn("act1", model["card_reward"]["floor_skip_rates"])


    def test_manifest_filters_and_limit_are_applied_to_structure_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw" / "runs.jsonl"
            rows_dir = root / "rows"
            model_dir = root / "models"
            write_jsonl(
                raw,
                [
                    {
                        "character_chosen": "IRONCLAD",
                        "ascension_level": 0,
                        "victory": True,
                        "floor_reached": 57,
                        "card_choices": [
                            {"floor": 1, "picked": "Pommel Strike", "not_picked": ["Clash"]}
                        ],
                        "master_deck": ["Strike_R", "Defend_R", "Bash", "Pommel Strike"],
                    },
                    {
                        "character_chosen": "IRONCLAD",
                        "ascension_level": 0,
                        "victory": True,
                        "floor_reached": 57,
                        "card_choices": [
                            {"floor": 2, "picked": "Battle Trance", "not_picked": ["Flex"]}
                        ],
                        "master_deck": ["Strike_R", "Defend_R", "Bash", "Battle Trance"],
                    },
                    {
                        "character_chosen": "SILENT",
                        "ascension_level": 0,
                        "victory": True,
                        "floor_reached": 57,
                        "card_choices": [
                            {"floor": 3, "picked": "Backflip", "not_picked": ["Slice"]}
                        ],
                        "master_deck": ["Strike_G", "Defend_G", "Backflip"],
                    },
                ],
            )
            manifest = root / "external_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "manifest_type": "external_prior_manifest",
                        "source_id": "limited_manifest",
                        "source_weight": 0.25,
                        "resolved_files": [str(raw)],
                        "filters": {
                            "characters": ["IRONCLAD"],
                            "ascension_min": 0,
                            "ascension_max": 0,
                            "limit_runs": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = train_external_structure_priors(
                [manifest],
                rows_dir=rows_dir,
                model_dir=model_dir,
                backend="stats",
            )
            reward_rows = read_jsonl(rows_dir / CARD_REWARD_DECISIONS_FILE)

        self.assertEqual(summary["seen_runs"], 1)
        self.assertEqual(summary["runs"], 1)
        self.assertEqual(summary["reward_decision_rows"], 1)
        self.assertEqual(reward_rows[0]["picked"], "Pommel Strike")
        self.assertEqual(summary["training_source"]["filters"]["limit_runs"], 1)
if __name__ == "__main__":
    unittest.main()
