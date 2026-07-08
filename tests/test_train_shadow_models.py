import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.model import CombatSearchModel, DeckQualityModel, PotionTempoModel, RouteRiskModel
from slay_ai.train_shadow_models import train_all_shadow_models


def write_jsonl(path: Path, rows: list[dict]) -> None:
    rows = [{**row, "source_validation_grade": row.get("source_validation_grade", "pristine")} for row in rows]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class TrainShadowModelsTests(unittest.TestCase):
    def test_trains_all_shadow_models_and_optional_advice(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [
                    {
                        "floor": 14,
                        "act": 1,
                        "hp_ratio": 0.2,
                        "forced_elite_within_3": True,
                        "readiness_penalty": 30,
                        "final_floor": 16,
                        "victory": False,
                        "floor_delta": 2,
                    },
                    {
                        "floor": 14,
                        "act": 1,
                        "hp_ratio": 0.9,
                        "forced_elite_within_3": False,
                        "readiness_penalty": 0,
                        "final_floor": 22,
                        "victory": False,
                        "floor_delta": 8,
                    },
                ],
            )
            write_jsonl(
                shadow / "potion_tempo.jsonl",
                [
                    {"floor": 16, "act": 1, "incoming": 35, "hp_ratio": 0.2, "potion_known_count": 1, "used_potion": True},
                    {"floor": 3, "act": 1, "incoming": 5, "hp_ratio": 0.9, "potion_known_count": 0, "used_potion": False},
                ],
            )
            write_jsonl(
                shadow / "pre_boss_deck_quality.jsonl",
                [
                    {
                        "floor": 15,
                        "act": 1,
                        "final_floor": 18,
                        "victory": False,
                        "deck_tag_weak": 1,
                        "readiness_score_boss": 80,
                    },
                    {
                        "floor": 15,
                        "act": 1,
                        "final_floor": 16,
                        "victory": False,
                        "deck_tag_weak": 0,
                        "readiness_score_boss": 35,
                    },
                ],
            )
            write_jsonl(
                shadow / "combat_search_labels.jsonl",
                [
                    {
                        "act": 1,
                        "turn": 3,
                        "hp_ratio": 0.2,
                        "current_hp": 12,
                        "current_block": 0,
                        "current_energy": 2,
                        "incoming": 18,
                        "enemy_count": 1,
                        "enemy_ids": ["JawWorm"],
                        "hand_ids": ["Defend_R", "Bash"],
                        "label_first_card_key": "Bash",
                        "initial_loss": 18,
                        "projected_loss": 8,
                        "attacks_removed": 1,
                        "avoided_lethal": True,
                    },
                    {
                        "act": 1,
                        "turn": 2,
                        "hp_ratio": 0.8,
                        "current_hp": 64,
                        "current_block": 12,
                        "current_energy": 1,
                        "incoming": 6,
                        "enemy_count": 1,
                        "enemy_ids": ["Louse"],
                        "hand_ids": ["Defend_R", "Strike_R"],
                        "label_first_card_key": "Defend_R",
                        "initial_loss": 0,
                        "projected_loss": 0,
                    },
                ],
            )
            route_path = root / "models" / "route.json"
            potion_path = root / "models" / "potion.json"
            deck_path = root / "models" / "deck.json"
            combat_path = root / "models" / "combat.json"

            summary = train_all_shadow_models(
                [shadow],
                route_model_path=route_path,
                potion_model_path=potion_path,
                deck_model_path=deck_path,
                combat_model_path=combat_path,
                min_count=1,
                advice_output_dir=root / "advice",
            )

            route = RouteRiskModel.load(route_path)
            potion = PotionTempoModel.load(potion_path)
            deck = DeckQualityModel.load(deck_path)
            combat = CombatSearchModel.load(combat_path)
            advice_summary = json.loads((root / "advice" / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["models"]["route_risk"]["examples"], 2)
        self.assertEqual(summary["source_quality"], "pristine")
        self.assertEqual(summary["shadow_training_source"]["inputs"], [str(shadow)])
        self.assertEqual(summary["shadow_training_source"]["source_quality_policy"], "pristine")
        self.assertEqual(summary["shadow_training_source"]["warnings"], [])
        self.assertEqual(
            summary["shadow_training_source"]["resolved_files"],
            [
                str(shadow / "route_risk.jsonl"),
                str(shadow / "potion_tempo.jsonl"),
                str(shadow / "pre_boss_deck_quality.jsonl"),
                str(shadow / "combat_search_labels.jsonl"),
            ],
        )
        self.assertEqual(summary["models"]["potion_tempo"]["examples"], 2)
        self.assertEqual(summary["models"]["pre_boss_deck_quality"]["examples"], 2)
        self.assertEqual(summary["models"]["combat_search"]["examples"], 2)
        self.assertEqual(summary["models"]["route_risk"]["training_source_quality"], "pristine")
        self.assertEqual(summary["models"]["route_risk"]["training_source"]["inputs"], [str(shadow)])
        self.assertEqual(
            summary["models"]["route_risk"]["training_source"]["resolved_files"],
            [str(shadow / "route_risk.jsonl")],
        )
        self.assertEqual(summary["models"]["potion_tempo"]["training_source"]["category"], "potion_tempo")
        self.assertEqual(
            summary["models"]["combat_search"]["training_source"]["resolved_files"],
            [str(shadow / "combat_search_labels.jsonl")],
        )
        self.assertEqual(
            summary["models"]["route_risk"]["load_quality"],
            {"files": 1, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertEqual(
            summary["models"]["potion_tempo"]["load_quality"],
            {"files": 1, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertEqual(
            summary["models"]["pre_boss_deck_quality"]["load_quality"],
            {"files": 1, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertEqual(
            summary["models"]["combat_search"]["load_quality"],
            {"files": 1, "rows": 2, "accepted": 2, "skipped": 0, "skip_reasons": {}},
        )
        self.assertEqual(route.metadata["load_quality"]["accepted"], 2)
        self.assertEqual(potion.metadata["load_quality"]["accepted"], 2)
        self.assertEqual(deck.metadata["load_quality"]["accepted"], 2)
        self.assertEqual(combat.metadata["load_quality"]["accepted"], 2)
        self.assertEqual(route.metadata["training_source"]["resolved_files"], [str(shadow / "route_risk.jsonl")])
        self.assertEqual(potion.metadata["training_source"]["source_quality_policy"], "pristine")
        self.assertEqual(deck.metadata["training_source"]["category"], "pre_boss_deck_quality")
        self.assertEqual(combat.metadata["training_source_quality"], "pristine")
        self.assertGreater(summary["models"]["route_risk"]["feature_weights"], 0)
        self.assertGreater(summary["models"]["potion_tempo"]["feature_weights"], 0)
        self.assertGreater(summary["models"]["pre_boss_deck_quality"]["feature_weights"], 0)
        self.assertGreater(summary["models"]["combat_search"]["card_priors"], 0)
        self.assertGreater(summary["models"]["combat_search"]["context_buckets"], 0)
        self.assertGreater(
            route.score_row({"hp_ratio": 0.2, "forced_elite_within_3": True}),
            route.score_row({"hp_ratio": 0.9, "forced_elite_within_3": False}),
        )
        self.assertGreater(
            potion.score_row({"incoming": 35, "hp_ratio": 0.2}),
            potion.score_row({"incoming": 5, "hp_ratio": 0.9}),
        )
        self.assertGreater(
            deck.score_row({"readiness_score_boss": 80}),
            deck.score_row({"readiness_score_boss": 35}),
        )
        ranked = combat.rank_hand(
            {
                "act": 1,
                "turn": 3,
                "hp_ratio": 0.2,
                "current_hp": 12,
                "current_block": 0,
                "current_energy": 2,
                "incoming": 18,
                "enemy_count": 1,
                "enemy_ids": ["JawWorm"],
                "hand_ids": ["Defend_R", "Bash"],
            }
        )
        self.assertEqual(ranked[0]["card_key"], "bash")
        self.assertEqual(summary["shadow_advice"]["advice_rows"]["route_risk"], 2)
        self.assertEqual(advice_summary["advice_rows"]["potion_tempo"], 2)
        self.assertEqual(summary["shadow_advice"]["advice_rows"]["combat_search"], 2)
        self.assertEqual(advice_summary["advice_rows"]["combat_search"], 2)

    def test_train_all_shadow_models_uses_source_quality_gate(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            shadow = root / "shadow"
            shadow.mkdir()
            write_jsonl(
                shadow / "route_risk.jsonl",
                [
                    {
                        "floor": 14,
                        "act": 1,
                        "hp_ratio": 0.2,
                        "final_floor": 16,
                        "victory": False,
                        "floor_delta": 2,
                        "source_validation_grade": "pristine",
                    },
                    {
                        "floor": 14,
                        "act": 1,
                        "hp_ratio": 0.9,
                        "final_floor": 22,
                        "victory": False,
                        "floor_delta": 8,
                        "source_validation_grade": "usable_with_recoveries",
                    },
                ],
            )

            pristine = train_all_shadow_models(
                [shadow],
                route_model_path=root / "models" / "route_pristine.json",
                potion_model_path=root / "models" / "potion_pristine.json",
                deck_model_path=root / "models" / "deck_pristine.json",
                combat_model_path=root / "models" / "combat_pristine.json",
                min_count=1,
            )
            usable = train_all_shadow_models(
                [shadow],
                route_model_path=root / "models" / "route_usable.json",
                potion_model_path=root / "models" / "potion_usable.json",
                deck_model_path=root / "models" / "deck_usable.json",
                combat_model_path=root / "models" / "combat_usable.json",
                min_count=1,
                source_quality="usable",
            )

        self.assertEqual(pristine["models"]["route_risk"]["examples"], 1)
        self.assertEqual(pristine["models"]["route_risk"]["load_quality"]["skipped"], 1)
        self.assertEqual(pristine["models"]["route_risk"]["load_quality"]["skip_reasons"], {"source_quality": 1})
        self.assertEqual(pristine["source_quality"], "pristine")
        self.assertEqual(usable["models"]["route_risk"]["examples"], 2)
        self.assertEqual(usable["models"]["route_risk"]["load_quality"]["skipped"], 0)
        self.assertEqual(usable["source_quality"], "usable")


if __name__ == "__main__":
    unittest.main()
