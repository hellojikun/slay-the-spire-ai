"""Train all shadow-only models from manifest shadow rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .model import COMBAT_SEARCH_MODEL_PATH, DECK_QUALITY_MODEL_PATH, POTION_TEMPO_MODEL_PATH, ROUTE_RISK_MODEL_PATH
from .shadow_advice import ShadowModels, score_shadow_inputs, write_advice
from .shadow_inputs import category_training_source, resolve_shadow_training_source
from .shadow_quality import DEFAULT_SOURCE_QUALITY, add_source_quality_argument
from .train_combat_search_model import load_examples_with_stats as load_combat_examples_with_stats
from .train_combat_search_model import train_stats_model as train_combat_model
from .train_deck_quality_model import load_examples_with_stats as load_deck_examples_with_stats
from .train_deck_quality_model import train_stats_model as train_deck_model
from .train_potion_tempo_model import load_examples_with_stats as load_potion_examples_with_stats
from .train_potion_tempo_model import train_stats_model as train_potion_model
from .train_route_risk_model import load_examples_with_stats as load_route_examples_with_stats
from .train_route_risk_model import train_stats_model as train_route_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train route, potion, deck-quality, and combat-search shadow models together.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="Shadow directories or JSONL files.")
    parser.add_argument("--model-dir", type=Path, help="Directory for all model files. Defaults to each model's standard path.")
    parser.add_argument("--route-model-path", type=Path)
    parser.add_argument("--potion-model-path", type=Path)
    parser.add_argument("--deck-model-path", type=Path)
    parser.add_argument("--combat-model-path", type=Path)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--max-weight", type=float, default=2.0)
    parser.add_argument("--advice-output-dir", type=Path, help="Optionally score the same shadow rows after training.")
    parser.add_argument("--summary-output", type=Path, help="Optional JSON summary path.")
    add_source_quality_argument(parser)
    args = parser.parse_args(argv)

    paths = list(args.shadow_rows)
    model_paths = {
        "route_risk": _resolve_model_path(args.route_model_path, args.model_dir, ROUTE_RISK_MODEL_PATH),
        "potion_tempo": _resolve_model_path(args.potion_model_path, args.model_dir, POTION_TEMPO_MODEL_PATH),
        "pre_boss_deck_quality": _resolve_model_path(args.deck_model_path, args.model_dir, DECK_QUALITY_MODEL_PATH),
        "combat_search": _resolve_model_path(args.combat_model_path, args.model_dir, COMBAT_SEARCH_MODEL_PATH),
    }
    summary = train_all_shadow_models(
        paths,
        route_model_path=model_paths["route_risk"],
        potion_model_path=model_paths["potion_tempo"],
        deck_model_path=model_paths["pre_boss_deck_quality"],
        combat_model_path=model_paths["combat_search"],
        min_count=args.min_count,
        max_weight=args.max_weight,
        advice_output_dir=args.advice_output_dir,
        source_quality=args.source_quality,
    )
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote shadow model summary: {args.summary_output}")
    print(
        "Shadow models trained: "
        f"route={summary['models']['route_risk']['feature_weights']} weights, "
        f"potion={summary['models']['potion_tempo']['feature_weights']} weights, "
        f"deck={summary['models']['pre_boss_deck_quality']['feature_weights']} weights, "
        f"combat={summary['models']['combat_search']['card_priors']} card priors."
    )
    if args.advice_output_dir:
        print(f"Wrote shadow advice: {args.advice_output_dir}")
    return 0


def train_all_shadow_models(
    paths: Iterable[Path],
    *,
    route_model_path: Path = ROUTE_RISK_MODEL_PATH,
    potion_model_path: Path = POTION_TEMPO_MODEL_PATH,
    deck_model_path: Path = DECK_QUALITY_MODEL_PATH,
    combat_model_path: Path = COMBAT_SEARCH_MODEL_PATH,
    min_count: int = 2,
    max_weight: float = 2.0,
    advice_output_dir: Path | None = None,
    source_quality: str = DEFAULT_SOURCE_QUALITY,
) -> dict[str, Any]:
    path_list = list(paths)
    training_source = resolve_shadow_training_source(path_list)
    route_load = load_route_examples_with_stats(path_list, source_quality=source_quality)
    potion_load = load_potion_examples_with_stats(path_list, source_quality=source_quality)
    deck_load = load_deck_examples_with_stats(path_list, source_quality=source_quality)
    combat_load = load_combat_examples_with_stats(path_list, source_quality=source_quality)
    route_examples = route_load.examples
    potion_examples = potion_load.examples
    deck_examples = deck_load.examples
    combat_examples = combat_load.examples

    route_model = train_route_model(
        route_examples,
        route_model_path,
        min_count=min_count,
        max_weight=max_weight,
        load_quality=route_load.stats,
    )
    potion_model = train_potion_model(
        potion_examples,
        potion_model_path,
        min_count=min_count,
        max_weight=max_weight,
        load_quality=potion_load.stats,
    )
    deck_model = train_deck_model(
        deck_examples,
        deck_model_path,
        min_count=min_count,
        max_weight=max_weight,
        load_quality=deck_load.stats,
    )
    combat_model = train_combat_model(
        combat_examples,
        combat_model_path,
        min_count=min_count,
        max_weight=max_weight,
        load_quality=combat_load.stats,
    )
    model_sources = {
        "route_risk": (route_model, "route_risk"),
        "potion_tempo": (potion_model, "potion_tempo"),
        "pre_boss_deck_quality": (deck_model, "pre_boss_deck_quality"),
        "combat_search": (combat_model, "combat_search"),
    }
    for model, category in model_sources.values():
        model.metadata["training_source"] = category_training_source(
            training_source,
            category,
            source_quality=source_quality,
        )
        model.metadata["training_source_quality"] = source_quality
    for model in (route_model, potion_model, deck_model, combat_model):
        model.save()

    summary: dict[str, Any] = {
        "version": 1,
        "shadow_inputs": [str(path) for path in path_list],
        "shadow_training_source": {
            **training_source,
            "source_quality_policy": source_quality,
        },
        "min_count": min_count,
        "max_weight": max_weight,
        "source_quality": source_quality,
        "models": {
            "route_risk": _model_summary(route_model_path, len(route_examples), route_model.metadata, route_model.feature_weights),
            "potion_tempo": _model_summary(
                potion_model_path,
                len(potion_examples),
                potion_model.metadata,
                potion_model.feature_weights,
            ),
            "pre_boss_deck_quality": _model_summary(deck_model_path, len(deck_examples), deck_model.metadata, deck_model.feature_weights),
            "combat_search": _combat_model_summary(
                combat_model_path,
                len(combat_examples),
                combat_model.metadata,
                combat_model.card_priors,
                combat_model.context_card_scores,
            ),
        },
    }
    if advice_output_dir is not None:
        models = ShadowModels.load(
            route_model_path=route_model_path,
            potion_model_path=potion_model_path,
            deck_model_path=deck_model_path,
            combat_model_path=combat_model_path,
        )
        advice = score_shadow_inputs(path_list, models=models)
        summary["shadow_advice"] = {
            **write_advice(
                advice_output_dir,
                advice,
                shadow_inputs={
                    **training_source,
                    "source_quality_policy": source_quality,
                },
            ),
            "path": str(advice_output_dir),
        }
    return summary


def _resolve_model_path(explicit: Path | None, model_dir: Path | None, default: Path) -> Path:
    if explicit is not None:
        return explicit
    if model_dir is not None:
        return model_dir / default.name
    return default


def _model_summary(path: Path, examples: int, metadata: dict[str, Any], weights: dict[str, float]) -> dict[str, Any]:
    return {
        "path": str(path),
        "examples": examples,
        "positive_examples": metadata.get("positive_examples"),
        "target": metadata.get("target"),
        "feature_weights": len(weights),
        "source_quality": metadata.get("source_quality") or {},
        "training_source_quality": metadata.get("training_source_quality"),
        "training_source": metadata.get("training_source") or {},
        "load_quality": metadata.get("load_quality") or {},
    }


def _combat_model_summary(
    path: Path,
    examples: int,
    metadata: dict[str, Any],
    card_priors: dict[str, float],
    context_card_scores: dict[str, dict[str, float]],
) -> dict[str, Any]:
    return {
        "path": str(path),
        "examples": examples,
        "label_cards": metadata.get("label_cards"),
        "target": metadata.get("target"),
        "value_target": metadata.get("value_target"),
        "card_priors": len(card_priors),
        "context_buckets": len(context_card_scores),
        "source_quality": metadata.get("source_quality") or {},
        "training_source_quality": metadata.get("training_source_quality"),
        "training_source": metadata.get("training_source") or {},
        "load_quality": metadata.get("load_quality") or {},
    }


if __name__ == "__main__":
    raise SystemExit(main())
