import gzip
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from slay_ai.import_external_runs import CARD_PRIOR_ROWS_FILE, import_external_runs, iter_external_records, main


class ImportExternalRunsTests(unittest.TestCase):
    def test_imports_filtered_run_history_to_external_manifest_and_card_rows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            source = raw / "runs.jsonl"
            source.write_text(
                "\n".join(
                    json.dumps(record)
                    for record in [
                        {
                            "run_id": "win1",
                            "character_chosen": "IRONCLAD",
                            "ascension_level_chosen": 0,
                            "victory": True,
                            "floor_reached": 50,
                            "score": 1234,
                            "seed_played": "abc123",
                            "build_version": "2020-11",
                            "card_reward_choices": [
                                {"floor": 1, "picked": "Shrug It Off", "not_picked": ["Anger", "Flex"]},
                                {"floor": 2, "picked": "Skip", "not_picked": ["Clash", "Wild Strike"]},
                            ],
                        },
                        {
                            "run_id": "defect1",
                            "character_chosen": "DEFECT",
                            "ascension_level": 0,
                            "victory": False,
                            "floor_reached": 12,
                            "card_choices": [{"picked": "Ball Lightning", "not_picked": ["Leap"]}],
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "out"

            result = import_external_runs(
                [raw],
                source_id="sample77m",
                source_uri="https://example.test/source",
                output_dir=output,
                characters=["IRONCLAD"],
                ascension_max=1,
                source_weight=0.25,
            )

            manifest = result.manifest
            rows = [json.loads(line) for line in (output / CARD_PRIOR_ROWS_FILE).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(manifest["manifest_type"], "external_prior_manifest")
        self.assertEqual(manifest["summary"]["accepted_runs"], 1)
        self.assertEqual(manifest["summary"]["rejected_runs"], 1)
        self.assertEqual(manifest["summary"]["card_prior_rows"], 1)
        self.assertEqual(manifest["categories"]["rejected_external"][0]["reason"], "filtered_character")
        self.assertEqual(manifest["categories"]["external_run_history"][0]["seed"], "abc123")
        self.assertEqual(manifest["categories"]["external_run_history"][0]["game_version"], "2020-11")
        self.assertEqual(rows[0]["picked"], "Shrug It Off")
        self.assertEqual(rows[0]["options"], ["Shrug It Off", "Anger", "Flex"])
        self.assertEqual(rows[0]["source_validation_grade"], "external_prior")
        self.assertEqual(rows[0]["source_validation_flags"], ["external", "not_mcp", "not_pristine"])
        self.assertEqual(rows[0]["source_weight"], 0.25)
        self.assertFalse("clean_trainable" in manifest["categories"])

    def test_reads_gzipped_json_array(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.json.gz"
            payload = [
                {
                    "character": "IRONCLAD",
                    "ascension": 0,
                    "won": "yes",
                    "floor": 50,
                    "cardChoices": [{"picked": "Pommel Strike", "notPicked": ["Clash"]}],
                }
            ]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle)

            records = list(iter_external_records(path))

        self.assertEqual(records[0]["character"], "IRONCLAD")
        self.assertEqual(records[0]["cardChoices"][0]["picked"], "Pommel Strike")

    def test_imports_choice_with_cards_shape(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "runs.json"
            raw.write_text(
                json.dumps(
                    {
                        "class": "IRONCLAD",
                        "ascension": 0,
                        "won": False,
                        "final_floor": 24,
                        "card_choices": [
                            {"floor": 3, "choice": "Pommel Strike", "cards": ["Anger", "Pommel Strike", "Flex"]}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = import_external_runs([raw], source_id="shape", output_dir=root / "out")

        self.assertEqual(result.card_prior_rows[0]["picked"], "Pommel Strike")
        self.assertEqual(result.card_prior_rows[0]["options"], ["Pommel Strike", "Anger", "Flex"])

    def test_reads_gzipped_jsonl_stream(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "runs.jsonl.gz"
            payload = [
                {
                    "character": "IRONCLAD",
                    "ascension": 0,
                    "victory": True,
                    "floor_reached": 57,
                    "card_choices": [{"picked": "Pommel Strike", "not_picked": ["Clash"]}],
                },
                {
                    "character": "SILENT",
                    "ascension": 1,
                    "victory": False,
                    "floor_reached": 11,
                    "card_choices": [{"picked": "Backflip", "not_picked": ["Dodge and Roll"]}],
                },
            ]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                for record in payload:
                    handle.write(json.dumps(record) + "\n")

            records = list(iter_external_records(path))

        self.assertEqual([record["character"] for record in records], ["IRONCLAD", "SILENT"])

    def test_limit_runs_stops_streaming_array_before_invalid_tail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "runs.json"
            one = {
                "character": "IRONCLAD",
                "ascension": 0,
                "victory": True,
                "floor_reached": 57,
                "card_choices": [{"picked": "Shrug It Off", "not_picked": ["Flex"]}],
            }
            two = {
                "character": "IRONCLAD",
                "ascension": 0,
                "victory": False,
                "floor_reached": 12,
                "card_choices": [{"picked": "Pommel Strike", "not_picked": ["Clash"]}],
            }
            raw.write_text("[" + json.dumps(one) + "," + json.dumps(two) + ", not-json", encoding="utf-8")

            result = import_external_runs(
                [raw],
                source_id="limited_stream",
                output_dir=root / "out",
                limit_runs=2,
            )

        self.assertEqual(result.manifest["sample_method"], "stream_first_n")
        self.assertEqual(result.manifest["summary"]["accepted_runs"], 2)
        self.assertEqual(result.manifest["summary"]["seen_runs"], 2)
        self.assertEqual(len(result.card_prior_rows), 2)
    def test_cli_writes_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "run.json"
            raw.write_text(
                json.dumps(
                    {
                        "character": "IRONCLAD",
                        "ascension": 0,
                        "victory": False,
                        "floor_reached": 20,
                        "card_choices": [{"picked": "Inflame", "not_picked": ["Clash"]}],
                    }
                ),
                encoding="utf-8",
            )
            out = root / "external"

            self.assertEqual(main([str(raw), "--source", "unit", "--output-dir", str(out)]), 0)

            self.assertTrue((out / "external_manifest.json").exists())
            self.assertTrue((out / CARD_PRIOR_ROWS_FILE).exists())


if __name__ == "__main__":
    unittest.main()
