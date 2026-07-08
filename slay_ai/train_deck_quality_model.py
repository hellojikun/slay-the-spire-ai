"""Train a shadow pre-boss deck-quality model from manifest shadow rows."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import DECK_QUALITY_MODEL_PATH, DeckQualityModel
from .shadow_inputs import category_training_source, iter_shadow_files, resolve_shadow_training_source
from .shadow_quality import DEFAULT_SOURCE_QUALITY, add_source_quality_argument, source_quality_allowed, source_quality_summary


SAFE_EXACT_FEATURES = {
    "hp_ratio",
    "deck_size",
    "attack_cards",
    "block_cards",
    "draw_cards",
    "potion_count",
    "relic_count",
    "gold",
    "boss_potion_gap",
}
SAFE_FEATURE_PREFIXES = (
    "deck_",
    "potion_",
    "relic_",
    "readiness_score_",
    "boss_max_",
    "boss_total_",
    "boss_mechanic_",
    "boss_search_hint_",
    "boss_need_",
    "boss_potion_need_",
    "boss_tag_",
)
LEAKY_FEATURES = {
    "source_log",
    "step",
    "character",
    "ascension",
    "floor",
    "act",
    "final_floor",
    "victory",
    "readiness_gaps",
    "readiness_risk_flags",
    "readiness_recommendations",
}


@dataclass(frozen=True)
class DeckQualityExample:
    row: dict[str, Any]
    cleared_act1_boss: bool


@dataclass(frozen=True)
class DeckQualityLoadResult:
    examples: list[DeckQualityExample]
    stats: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a shadow model from pre_boss_deck_quality JSONL rows.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="pre_boss_deck_quality.jsonl files or shadow directories.")
    parser.add_argument("--model-path", type=Path, default=DECK_QUALITY_MODEL_PATH)
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
    training_source = resolve_shadow_training_source(args.shadow_rows, categories=("pre_boss_deck_quality",))
    model.metadata["training_source"] = category_training_source(
        training_source,
        "pre_boss_deck_quality",
        source_quality=args.source_quality,
    )
    model.metadata["training_source_quality"] = args.source_quality
    model.save()
    print(f"Loaded {len(examples)} pre-boss deck-quality examples.")
    if load_result.stats.get("skipped"):
        print(f"Skipped {load_result.stats['skipped']} pre-boss deck-quality rows: {load_result.stats.get('skip_reasons') or {}}.")
    print(f"Wrote {len(model.feature_weights)} feature weights to {model.path}.")
    return 0


def load_examples(paths: Iterable[Path], *, source_quality: str = DEFAULT_SOURCE_QUALITY) -> list[DeckQualityExample]:
    return load_examples_with_stats(paths, source_quality=source_quality).examples


def load_examples_with_stats(
    paths: Iterable[Path],
    *,
    source_quality: str = DEFAULT_SOURCE_QUALITY,
) -> DeckQualityLoadResult:
    examples: list[DeckQualityExample] = []
    skip_reasons: dict[str, int] = {}
    rows = 0
    files = 0
    for path in iter_shadow_files(paths, "pre_boss_deck_quality"):
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
                examples.append(DeckQualityExample(row=row, cleared_act1_boss=_cleared_act1_boss(row)))
    return DeckQualityLoadResult(
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
    examples: list[DeckQualityExample],
    model_path: Path = DECK_QUALITY_MODEL_PATH,
    *,
    min_count: int = 1,
    max_weight: float = 2.0,
    load_quality: dict[str, Any] | None = None,
) -> DeckQualityModel:
    positives = sum(1 for example in examples if example.cleared_act1_boss)
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
        pos_values = [_numeric(example.row.get(feature)) for example in present if example.cleared_act1_boss]
        neg_values = [_numeric(example.row.get(feature)) for example in present if not example.cleared_act1_boss]
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

    return DeckQualityModel(
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
            "target": "cleared_act1_boss",
            "min_count": min_count,
            "max_weight": max_weight,
            "source_quality": source_quality_summary(example.row for example in examples),
            "load_quality": load_quality or _load_quality_from_examples(examples),
        },
    )


def _count_skip(skip_reasons: dict[str, int], reason: str) -> None:
    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1


def _load_quality_from_examples(examples: list[DeckQualityExample]) -> dict[str, Any]:
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


def _cleared_act1_boss(row: dict[str, Any]) -> bool:
    if bool(row.get("victory")):
        return True
    return _numeric(row.get("final_floor")) >= 17


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
