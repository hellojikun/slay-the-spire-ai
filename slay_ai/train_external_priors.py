"""Train isolated card-prior models from external run-history rows."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .import_external_runs import CARD_PRIOR_ROWS_FILE, EXTERNAL_PRIOR_GRADE
from .memory import normalize_card_name
from .model import MODEL_PATH, CardValueModel
from .train_card_model import hardware_metadata


@dataclass
class ExternalCardPriorExample:
    character: str
    ascension: int
    floor: int
    picked: str
    options: list[str]
    has_reward_options: bool
    victory: bool
    final_floor: int
    source_weight: float
    source_dataset: str


@dataclass
class LoadResult:
    examples: list[ExternalCardPriorExample]
    stats: dict[str, Any]
    training_source: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train scratch card priors from external run-history rows.")
    parser.add_argument("inputs", nargs="+", type=Path, help="External manifest, prior JSONL, or directory.")
    parser.add_argument("--model-dir", type=Path, help="Directory for card_prior_model.json. Defaults to data/external_models/<source>.")
    parser.add_argument("--model-path", type=Path, help="Explicit model output path.")
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--max-delta", type=float, default=4.0)
    parser.add_argument("--prior-scale", type=float, default=0.35)
    parser.add_argument("--backend", choices=["auto", "stats"], default="auto")
    parser.add_argument(
        "--allow-runtime-output",
        action="store_true",
        help="Allow writing an external-prior model to a runtime model path. Intended only for audited promotion.",
    )
    args = parser.parse_args(argv)

    load_result = load_examples_with_stats(args.inputs)
    model_path = _resolve_model_path(args.model_path, args.model_dir, load_result.training_source)
    if not args.allow_runtime_output:
        reason = external_prior_runtime_output_risk(model_path)
        if reason:
            parser.error(
                f"refusing external-prior runtime output ({reason}): {model_path}. "
                "Use a scratch --model-dir under data/external_models, or pass --allow-runtime-output after audited promotion."
            )
    model = train_external_card_prior_model(
        load_result.examples,
        model_path,
        min_count=args.min_count,
        max_delta=args.max_delta,
        prior_scale=args.prior_scale,
        load_quality=load_result.stats,
    )
    model.metadata["training_source"] = load_result.training_source
    model.metadata["training_source_quality"] = EXTERNAL_PRIOR_GRADE
    model.metadata.update(hardware_metadata(args.backend))
    model.save()
    summary = {
        "version": 1,
        "status": "trained",
        "model_path": str(model_path),
        "examples": len(load_result.examples),
        "deltas": len(model.card_deltas),
        "min_count": args.min_count,
        "max_delta": args.max_delta,
        "prior_scale": args.prior_scale,
        "load_quality": load_result.stats,
        "training_source": load_result.training_source,
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "does_not_control_live_mcp": True,
        "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"],
    }
    summary_path = args.summary_output or (model_path.parent / "external_prior_training_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "external_prior_training: "
        f"rows={load_result.stats['rows']} accepted={load_result.stats['accepted']} "
        f"examples={len(load_result.examples)} deltas={len(model.card_deltas)}"
    )
    print(f"Wrote external card prior model: {model_path}")
    print(f"Wrote summary: {summary_path}")
    return 0


def load_examples_with_stats(inputs: Iterable[Path]) -> LoadResult:
    input_paths = [Path(path) for path in inputs]
    training_source = resolve_external_prior_training_source(input_paths)
    examples: list[ExternalCardPriorExample] = []
    stats: dict[str, Any] = {
        "files": training_source["resolved_file_count"],
        "rows": 0,
        "accepted": 0,
        "skipped": 0,
        "skip_reasons": {},
        "source_datasets": {},
        "has_reward_options_count": 0,
        "picked_only_count": 0,
        "source_weight_total": 0.0,
    }
    for path_text in training_source["resolved_files"]:
        path = Path(path_text)
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                stats["rows"] += 1
                row = json.loads(line)
                example, reason = _example_from_row(row)
                if reason:
                    stats["skipped"] += 1
                    _count(stats["skip_reasons"], reason)
                    continue
                assert example is not None
                examples.append(example)
                stats["accepted"] += 1
                _count(stats["source_datasets"], example.source_dataset)
                if example.has_reward_options:
                    stats["has_reward_options_count"] += 1
                else:
                    stats["picked_only_count"] += 1
                stats["source_weight_total"] = round(stats["source_weight_total"] + example.source_weight, 6)
    return LoadResult(examples=examples, stats=stats, training_source=training_source)


def resolve_external_prior_training_source(inputs: Iterable[Path]) -> dict[str, Any]:
    input_paths = [Path(path) for path in inputs]
    resolved_files: list[Path] = []
    manifest_paths: list[Path] = []
    missing_inputs: list[str] = []
    source_ids: list[str] = []
    warnings: list[str] = []
    for path in input_paths:
        if not path.exists():
            missing_inputs.append(str(path))
            continue
        if path.is_dir():
            candidate = path / CARD_PRIOR_ROWS_FILE
            if candidate.exists():
                resolved_files.append(candidate)
            for manifest in sorted(path.glob("external_manifest.json")):
                manifest_paths.append(manifest)
                _add_manifest_source(manifest, resolved_files, source_ids)
            continue
        if path.name == "external_manifest.json" or path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                resolved_files.append(path)
                continue
            if payload.get("manifest_type") == "external_prior_manifest":
                manifest_paths.append(path)
                _add_manifest_payload(path, payload, resolved_files, source_ids)
            else:
                resolved_files.append(path)
            continue
        resolved_files.append(path)
    resolved_unique = [str(path) for path in dict.fromkeys(resolved_files)]
    if missing_inputs:
        warnings.append("missing_external_prior_inputs")
    if not resolved_unique:
        warnings.append("no_resolved_external_prior_rows")
    source_id = source_ids[0] if source_ids else "external_prior"
    return {
        "mode": "external_prior_rows",
        "source_category": "external_run_history",
        "source_quality_policy": EXTERNAL_PRIOR_GRADE,
        "source_ids": list(dict.fromkeys(source_ids)),
        "source_id": source_id,
        "inputs": [str(path) for path in input_paths],
        "missing_inputs": missing_inputs,
        "manifest_paths": [str(path) for path in dict.fromkeys(manifest_paths)],
        "resolved_files": resolved_unique,
        "resolved_file_count": len(resolved_unique),
        "warnings": warnings,
        "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"],
    }


def train_external_card_prior_model(
    examples: list[ExternalCardPriorExample],
    model_path: Path,
    *,
    min_count: int = 2,
    max_delta: float = 4.0,
    prior_scale: float = 0.35,
    load_quality: dict[str, Any] | None = None,
) -> CardValueModel:
    totals: dict[str, float] = defaultdict(float)
    weights: dict[str, float] = defaultdict(float)
    raw_counts: dict[str, int] = defaultdict(int)
    option_penalty_count = 0
    for example in examples:
        reward = _reward(example) * prior_scale
        weight = max(0.0, float(example.source_weight))
        if weight <= 0:
            continue
        key = f"{example.character}::{normalize_card_name(example.picked)}"
        totals[key] += reward * weight
        weights[key] += weight
        raw_counts[key] += 1

        if example.has_reward_options and len(example.options) > 1:
            opportunity_penalty = max(-1.0, min(1.0, -reward * 0.08))
            for option in example.options:
                normalized = normalize_card_name(option)
                if normalized == normalize_card_name(example.picked):
                    continue
                alt_key = f"{example.character}::{normalized}"
                totals[alt_key] += opportunity_penalty * weight
                weights[alt_key] += weight
                raw_counts[alt_key] += 1
                option_penalty_count += 1

    deltas: dict[str, float] = {}
    for key, total in totals.items():
        raw_count = raw_counts[key]
        if raw_count < min_count:
            continue
        shrink = math.sqrt(raw_count) / (math.sqrt(raw_count) + 3.0)
        delta = (total / max(weights[key], 1e-9)) * shrink
        deltas[key] = round(max(-max_delta, min(max_delta, delta)), 3)

    return CardValueModel(
        path=model_path,
        card_deltas=deltas,
        metadata={
            "version": 1,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "stats",
            "model_kind": "external_card_prior",
            "examples": len(examples),
            "min_count": min_count,
            "max_delta": max_delta,
            "prior_scale": prior_scale,
            "option_penalty_count": option_penalty_count,
            "load_quality": load_quality or {},
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "does_not_control_live_mcp": True,
            "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"],
        },
    )


def _example_from_row(row: dict[str, Any]) -> tuple[ExternalCardPriorExample | None, str | None]:
    if row.get("source_validation_grade") != EXTERNAL_PRIOR_GRADE:
        return None, "source_quality"
    picked = str(row.get("picked") or "").strip()
    if not picked:
        return None, "missing_picked"
    victory = row.get("victory")
    if not isinstance(victory, bool):
        return None, "missing_victory"
    final_floor = _int_or_none(row.get("final_floor"))
    if final_floor is None:
        return None, "missing_final_floor"
    character = str(row.get("character") or "UNKNOWN").strip().upper()
    options = [str(option) for option in row.get("options") or [picked] if str(option).strip()]
    if picked not in options:
        options = [picked, *options]
    return (
        ExternalCardPriorExample(
            character=character,
            ascension=int(row.get("ascension") or 0),
            floor=int(row.get("floor") or 0),
            picked=picked,
            options=options,
            has_reward_options=bool(row.get("has_reward_options")) and len(options) > 1,
            victory=victory,
            final_floor=final_floor,
            source_weight=float(row.get("source_weight") or 1.0),
            source_dataset=str(row.get("source_dataset") or "external_prior"),
        ),
        None,
    )


def _reward(example: ExternalCardPriorExample) -> float:
    if example.victory:
        return 8.0
    progress = max(0.0, min(1.0, example.final_floor / 50.0))
    if example.final_floor < 17:
        return -5.0 + progress
    if example.final_floor < 34:
        return -2.5 + progress * 2.0
    return 0.5 + progress * 2.0


def _resolve_model_path(explicit: Path | None, model_dir: Path | None, training_source: dict[str, Any]) -> Path:
    if explicit is not None:
        return explicit
    source_id = str(training_source.get("source_id") or "external_prior")
    if model_dir is not None:
        return model_dir / "card_prior_model.json"
    return Path("data") / "external_models" / source_id / "card_prior_model.json"


def external_prior_runtime_output_risk(model_path: Path) -> str | None:
    resolved = model_path.resolve()
    runtime_model = MODEL_PATH.resolve()
    if resolved == runtime_model:
        return "default_card_value_model"
    if resolved.name == MODEL_PATH.name and resolved.parent.name == runtime_model.parent.name:
        return "card_value_model_under_models_dir"
    return None


def _add_manifest_source(manifest: Path, resolved_files: list[Path], source_ids: list[str]) -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    _add_manifest_payload(manifest, payload, resolved_files, source_ids)


def _add_manifest_payload(manifest: Path, payload: dict[str, Any], resolved_files: list[Path], source_ids: list[str]) -> None:
    source_id = str(payload.get("source_id") or "").strip()
    if source_id:
        source_ids.append(source_id)
    artifact = (payload.get("artifacts") or {}).get("card_reward_priors")
    if not artifact:
        return
    artifact_path = Path(artifact)
    if not artifact_path.is_absolute():
        artifact_path = (manifest.parent / artifact_path).resolve() if not artifact_path.exists() else artifact_path
    if artifact_path.exists():
        resolved_files.append(artifact_path)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


if __name__ == "__main__":
    raise SystemExit(main())
