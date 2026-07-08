"""Train a shadow combat-search imitation/value model from search-label rows."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import COMBAT_SEARCH_MODEL_PATH, CombatSearchModel, combat_card_key, combat_search_context_keys
from .shadow_inputs import category_training_source, iter_shadow_files, resolve_shadow_training_source
from .shadow_quality import DEFAULT_SOURCE_QUALITY, add_source_quality_argument, source_quality_allowed, source_quality_summary


@dataclass(frozen=True)
class CombatSearchExample:
    row: dict[str, Any]
    first_card_key: str
    label_value: float


@dataclass(frozen=True)
class CombatSearchLoadResult:
    examples: list[CombatSearchExample]
    stats: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a shadow model from combat_search_labels JSONL rows.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="combat_search_labels.jsonl files or shadow directories.")
    parser.add_argument("--model-path", type=Path, default=COMBAT_SEARCH_MODEL_PATH)
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
    training_source = resolve_shadow_training_source(args.shadow_rows, categories=("combat_search",))
    model.metadata["training_source"] = category_training_source(
        training_source,
        "combat_search",
        source_quality=args.source_quality,
    )
    model.metadata["training_source_quality"] = args.source_quality
    model.save()
    print(f"Loaded {len(examples)} combat-search label examples.")
    if load_result.stats.get("skipped"):
        print(
            "Skipped "
            f"{load_result.stats['skipped']} combat-search label rows: "
            f"{load_result.stats.get('skip_reasons') or {}}."
        )
    print(f"Wrote {len(model.card_priors)} card priors and {len(model.context_card_scores)} context buckets to {model.path}.")
    return 0


def load_examples(paths: Iterable[Path], *, source_quality: str = DEFAULT_SOURCE_QUALITY) -> list[CombatSearchExample]:
    return load_examples_with_stats(paths, source_quality=source_quality).examples


def load_examples_with_stats(
    paths: Iterable[Path],
    *,
    source_quality: str = DEFAULT_SOURCE_QUALITY,
) -> CombatSearchLoadResult:
    examples: list[CombatSearchExample] = []
    skip_reasons: dict[str, int] = {}
    rows = 0
    files = 0
    for path in iter_shadow_files(paths, "combat_search"):
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
                if row.get("label_missed_direct_kill"):
                    _count_skip(skip_reasons, "missed_direct_kill")
                    continue
                if row.get("label_missed_single_card_search"):
                    _count_skip(skip_reasons, "missed_single_card_search")
                    continue
                first_card_key = _label_first_card_key(row)
                if not first_card_key:
                    _count_skip(skip_reasons, "missing_label_first_card_key")
                    continue
                examples.append(
                    CombatSearchExample(
                        row=row,
                        first_card_key=first_card_key,
                        label_value=_label_value(row),
                    )
                )
    skipped = sum(skip_reasons.values())
    return CombatSearchLoadResult(
        examples=examples,
        stats={
            "files": files,
            "rows": rows,
            "accepted": len(examples),
            "skipped": skipped,
            "skip_reasons": dict(sorted(skip_reasons.items())),
        },
    )


def train_stats_model(
    examples: list[CombatSearchExample],
    model_path: Path = COMBAT_SEARCH_MODEL_PATH,
    *,
    min_count: int = 1,
    max_weight: float = 2.0,
    load_quality: dict[str, Any] | None = None,
) -> CombatSearchModel:
    values = [example.label_value for example in examples]
    global_mean = _mean(values)
    card_priors: dict[str, float] = {}
    context_card_scores: dict[str, dict[str, float]] = {}

    for card_key in sorted({example.first_card_key for example in examples}):
        card_values = [example.label_value for example in examples if example.first_card_key == card_key]
        if len(card_values) < min_count:
            continue
        card_priors[card_key] = round(min(max_weight, _mean(card_values) * _shrink(len(card_values))), 4)

    context_keys = sorted({context for example in examples for context in combat_search_context_keys(example.row)})
    for context in context_keys:
        scores: dict[str, float] = {}
        examples_in_context = [example for example in examples if context in combat_search_context_keys(example.row)]
        for card_key in sorted({example.first_card_key for example in examples_in_context}):
            card_values = [example.label_value for example in examples_in_context if example.first_card_key == card_key]
            if len(card_values) < min_count:
                continue
            centered = (_mean(card_values) - global_mean) * _shrink(len(card_values))
            if abs(centered) >= 0.001:
                scores[card_key] = round(max(-max_weight, min(max_weight, centered)), 4)
        if scores:
            context_card_scores[context] = scores

    return CombatSearchModel(
        path=model_path,
        card_priors=card_priors,
        context_card_scores=context_card_scores,
        metadata={
            "version": 1,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "frequency_context_stats",
            "examples": len(examples),
            "label_cards": len({example.first_card_key for example in examples}),
            "target": "label_first_card_key",
            "value_target": "loss_delta_attacks_removed_kills_retaliation_damage_avoided_lethal",
            "min_count": min_count,
            "max_weight": max_weight,
            "source_quality": source_quality_summary(example.row for example in examples),
            "load_quality": load_quality or _load_quality_from_examples(examples),
        },
    )


def _count_skip(skip_reasons: dict[str, int], reason: str) -> None:
    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1


def _load_quality_from_examples(examples: list[CombatSearchExample]) -> dict[str, Any]:
    return {
        "files": None,
        "rows": len(examples),
        "accepted": len(examples),
        "skipped": 0,
        "skip_reasons": {},
    }


def _label_first_card_key(row: dict[str, Any]) -> str:
    key = combat_card_key(row.get("label_first_card_key"))
    if key:
        return key
    sequence = row.get("label_sequence_card_keys")
    if isinstance(sequence, list) and sequence:
        return combat_card_key(sequence[0])
    return ""


def _label_value(row: dict[str, Any]) -> float:
    initial_loss = _numeric(row.get("initial_loss"))
    projected_loss = _numeric(row.get("projected_loss"))
    loss_delta = row.get("loss_delta")
    if loss_delta is None:
        loss_delta = initial_loss - projected_loss
    reduction_ratio = max(0.0, min(1.0, _numeric(loss_delta) / max(initial_loss, 1.0)))
    value = 1.0 + reduction_ratio
    value += min(3.0, _numeric(row.get("attacks_removed"))) * 0.25
    value += min(3.0, _numeric(row.get("kills"))) * 0.2
    value += min(3.0, _numeric(row.get("retaliation_damage")) / 8.0) * 0.25
    if row.get("avoided_lethal"):
        value += 0.5
    return round(value, 4)


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


def _shrink(count: int) -> float:
    root = math.sqrt(max(count, 0))
    return root / (root + 2.0) if root > 0 else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
