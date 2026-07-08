"""Train a shadow potion-tempo model from manifest shadow rows."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import POTION_TEMPO_MODEL_PATH, PotionTempoModel
from .shadow_inputs import category_training_source, iter_shadow_files, resolve_shadow_training_source
from .shadow_quality import DEFAULT_SOURCE_QUALITY, add_source_quality_argument, source_quality_allowed, source_quality_summary


SAFE_EXACT_FEATURES = {
    "floor",
    "act",
    "turn",
    "hp_ratio",
    "incoming",
    "used_potion_targeted",
}
SAFE_FEATURE_PREFIXES = (
    "potion_",
    "enemy_",
    "boss_max_",
    "boss_total_",
    "boss_mechanic_",
    "boss_search_hint_",
    "boss_need_",
    "boss_potion_need_",
    "boss_tag_",
    "relic_",
)
LEAKY_FEATURES = {
    "source_log",
    "step",
    "character",
    "ascension",
    "potion_ids",
    "enemy_ids",
    "used_potion",
    "used_potion_slot",
    "died_same_floor",
    "final_floor",
    "victory",
}


@dataclass(frozen=True)
class PotionTempoExample:
    row: dict[str, Any]
    used_potion: bool


@dataclass(frozen=True)
class PotionTempoLoadResult:
    examples: list[PotionTempoExample]
    stats: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a shadow model from potion_tempo JSONL rows.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="potion_tempo.jsonl files or shadow directories.")
    parser.add_argument("--model-path", type=Path, default=POTION_TEMPO_MODEL_PATH)
    parser.add_argument("--min-count", type=int, default=1)
    parser.add_argument("--max-weight", type=float, default=2.0)
    add_source_quality_argument(parser)
    args = parser.parse_args(argv)

    load_result = load_examples_with_stats(args.shadow_rows, source_quality=args.source_quality)
    examples = load_result.examples
    model = train_stats_model(
        examples,
        args.model_path,
        min_count=args.min_count,
        max_weight=args.max_weight,
        load_quality=load_result.stats,
    )
    training_source = resolve_shadow_training_source(args.shadow_rows, categories=("potion_tempo",))
    model.metadata["training_source"] = category_training_source(
        training_source,
        "potion_tempo",
        source_quality=args.source_quality,
    )
    model.metadata["training_source_quality"] = args.source_quality
    model.save()
    print(f"Loaded {len(examples)} potion-tempo examples.")
    if load_result.stats.get("skipped"):
        print(f"Skipped {load_result.stats['skipped']} potion-tempo rows: {load_result.stats.get('skip_reasons') or {}}.")
    print(f"Wrote {len(model.feature_weights)} feature weights to {model.path}.")
    return 0


def load_examples(paths: Iterable[Path], *, source_quality: str = DEFAULT_SOURCE_QUALITY) -> list[PotionTempoExample]:
    return load_examples_with_stats(paths, source_quality=source_quality).examples


def load_examples_with_stats(
    paths: Iterable[Path],
    *,
    source_quality: str = DEFAULT_SOURCE_QUALITY,
) -> PotionTempoLoadResult:
    examples: list[PotionTempoExample] = []
    skip_reasons: dict[str, int] = {}
    rows = 0
    files = 0
    for path in iter_shadow_files(paths, "potion_tempo"):
        files += 1
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rows += 1
                row = json.loads(line)
                if not isinstance(row, dict):
                    _count_skip(skip_reasons, "non_object_row")
                    continue
                if not source_quality_allowed(row, source_quality):
                    _count_skip(skip_reasons, "source_quality")
                    continue
                examples.append(PotionTempoExample(row=row, used_potion=bool(row.get("used_potion"))))
    return PotionTempoLoadResult(
        examples=examples,
        stats={
            "files": files,
            "rows": rows,
            "accepted": len(examples),
            "skipped": sum(skip_reasons.values()),
            "skip_reasons": dict(sorted(skip_reasons.items())),
        },
    )


def train_stats_model(
    examples: list[PotionTempoExample],
    model_path: Path = POTION_TEMPO_MODEL_PATH,
    *,
    min_count: int = 1,
    max_weight: float = 2.0,
    load_quality: dict[str, Any] | None = None,
) -> PotionTempoModel:
    positives = sum(1 for example in examples if example.used_potion)
    base_rate = (positives + 0.5) / (len(examples) + 1.0) if examples else 0.5
    intercept = round(_logit(base_rate), 4)
    features = sorted({feature for example in examples for feature in _safe_numeric_features(example.row)})
    weights: dict[str, float] = {}
    feature_means: dict[str, float] = {}
    feature_scales: dict[str, float] = {}

    for feature in features:
        present = [example for example in examples if feature in example.row]
        if len(present) < min_count:
            continue
        pos_values = [_numeric(example.row.get(feature)) for example in present if example.used_potion]
        neg_values = [_numeric(example.row.get(feature)) for example in present if not example.used_potion]
        if not pos_values or not neg_values:
            continue
        all_values = pos_values + neg_values
        span = max(all_values) - min(all_values)
        if span <= 0:
            continue
        feature_means[feature] = round(_mean(all_values), 4)
        feature_scales[feature] = round(span, 4)
        direction = (_mean(pos_values) - _mean(neg_values)) / span
        shrink = math.sqrt(len(present)) / (math.sqrt(len(present)) + 3.0)
        weight = max(-max_weight, min(max_weight, direction * max_weight * shrink))
        if abs(weight) >= 0.001:
            weights[feature] = round(weight, 4)

    return PotionTempoModel(
        path=model_path,
        intercept=intercept,
        feature_weights=weights,
        feature_means=feature_means,
        feature_scales=feature_scales,
        metadata={
            "version": 1,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "stats",
            "examples": len(examples),
            "positive_examples": positives,
            "target": "used_potion",
            "min_count": min_count,
            "max_weight": max_weight,
            "source_quality": source_quality_summary(example.row for example in examples),
            "load_quality": load_quality or _load_quality_from_examples(examples),
        },
    )


def _count_skip(skip_reasons: dict[str, int], reason: str) -> None:
    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1


def _load_quality_from_examples(examples: list[PotionTempoExample]) -> dict[str, Any]:
    return {
        "files": None,
        "rows": len(examples),
        "accepted": len(examples),
        "skipped": 0,
        "skip_reasons": {},
    }


def _safe_numeric_features(row: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, value in row.items():
        if key in LEAKY_FEATURES:
            continue
        if key not in SAFE_EXACT_FEATURES and not any(key.startswith(prefix) for prefix in SAFE_FEATURE_PREFIXES):
            continue
        if not _is_numeric_like(value):
            continue
        result[key] = _numeric(value)
    return result


def _is_numeric_like(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if value is None or isinstance(value, (list, dict)):
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _logit(probability: float) -> float:
    probability = max(0.001, min(0.999, probability))
    return math.log(probability / (1.0 - probability))


if __name__ == "__main__":
    raise SystemExit(main())
