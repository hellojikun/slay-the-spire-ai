"""Score shadow-model rows without handing control to learned models."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model import (
    COMBAT_SEARCH_MODEL_PATH,
    DECK_QUALITY_MODEL_PATH,
    POTION_TEMPO_MODEL_PATH,
    ROUTE_RISK_MODEL_PATH,
    CombatSearchModel,
    DeckQualityModel,
    PotionTempoModel,
    RouteRiskModel,
    combat_card_key,
)
from .shadow_inputs import resolve_shadow_training_source


SHADOW_FILES = {
    "route_risk": "route_risk.jsonl",
    "potion_tempo": "potion_tempo.jsonl",
    "pre_boss_deck_quality": "pre_boss_deck_quality.jsonl",
    "combat_search": "combat_search_labels.jsonl",
}
ADVICE_FILES = {
    "route_risk": "route_risk_advice.jsonl",
    "potion_tempo": "potion_tempo_advice.jsonl",
    "pre_boss_deck_quality": "pre_boss_deck_quality_advice.jsonl",
    "combat_search": "combat_search_advice.jsonl",
}


@dataclass(frozen=True)
class ShadowModels:
    route: RouteRiskModel
    potion: PotionTempoModel
    deck: DeckQualityModel
    combat: CombatSearchModel
    route_available: bool
    potion_available: bool
    deck_available: bool
    combat_available: bool

    @classmethod
    def load(
        cls,
        *,
        route_model_path: Path = ROUTE_RISK_MODEL_PATH,
        potion_model_path: Path = POTION_TEMPO_MODEL_PATH,
        deck_model_path: Path = DECK_QUALITY_MODEL_PATH,
        combat_model_path: Path = COMBAT_SEARCH_MODEL_PATH,
    ) -> "ShadowModels":
        return cls(
            route=RouteRiskModel.load(route_model_path),
            potion=PotionTempoModel.load(potion_model_path),
            deck=DeckQualityModel.load(deck_model_path),
            combat=CombatSearchModel.load(combat_model_path),
            route_available=route_model_path.exists(),
            potion_available=potion_model_path.exists(),
            deck_available=deck_model_path.exists(),
            combat_available=combat_model_path.exists(),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write shadow-model advice JSONL without changing policy control.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="Shadow directories or JSONL files to score.")
    parser.add_argument("--output-dir", type=Path, default=Path("data") / "shadow_advice")
    parser.add_argument("--route-model-path", type=Path, default=ROUTE_RISK_MODEL_PATH)
    parser.add_argument("--potion-model-path", type=Path, default=POTION_TEMPO_MODEL_PATH)
    parser.add_argument("--deck-model-path", type=Path, default=DECK_QUALITY_MODEL_PATH)
    parser.add_argument("--combat-model-path", type=Path, default=COMBAT_SEARCH_MODEL_PATH)
    args = parser.parse_args(argv)

    models = ShadowModels.load(
        route_model_path=args.route_model_path,
        potion_model_path=args.potion_model_path,
        deck_model_path=args.deck_model_path,
        combat_model_path=args.combat_model_path,
    )
    advice = score_shadow_inputs(args.shadow_rows, models=models)
    summary = write_advice(
        args.output_dir,
        advice,
        shadow_inputs=resolve_shadow_training_source(args.shadow_rows),
    )
    print(f"Wrote shadow advice to {args.output_dir}.")
    print(f"Advice rows: {summary['advice_rows']}")
    print(f"Models available: {summary['models_available']}")
    return 0


def score_shadow_inputs(paths: Iterable[Path], *, models: ShadowModels) -> dict[str, list[dict[str, Any]]]:
    advice = {
        "route_risk": [],
        "potion_tempo": [],
        "pre_boss_deck_quality": [],
        "combat_search": [],
    }
    for category, path in _iter_shadow_files(paths):
        for row in _read_jsonl(path):
            scored = _score_row(category, row, models)
            if scored is not None:
                scored["shadow_source_mode"] = "shadow_rows"
                scored["shadow_source_category"] = category
                scored["shadow_source_file"] = str(path)
                advice[category].append(scored)
    return advice


def score_shadow_examples(
    examples: dict[str, list[dict[str, Any]]],
    *,
    models: ShadowModels,
) -> dict[str, list[dict[str, Any]]]:
    advice = {
        "route_risk": [],
        "potion_tempo": [],
        "pre_boss_deck_quality": [],
        "combat_search": [],
    }
    for category, rows in examples.items():
        advice_category = "combat_search" if category == "combat_search_labels" else category
        if advice_category not in advice:
            continue
        for row in rows:
            scored = _score_row(advice_category, row, models)
            if scored is not None:
                scored["shadow_source_mode"] = "in_memory_examples"
                scored["shadow_source_category"] = advice_category
                advice[advice_category].append(scored)
    return advice


def write_advice(
    output_dir: Path,
    advice: dict[str, list[dict[str, Any]]],
    *,
    shadow_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    for category, rows in advice.items():
        path = output_dir / ADVICE_FILES[category]
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "version": 1,
        "shadow_inputs": _shadow_input_summary(advice, shadow_inputs=shadow_inputs),
        "advice_rows": {category: len(rows) for category, rows in advice.items()},
        "models_available": {
            category: _any_available(advice.get(category, []))
            for category in ADVICE_FILES
        },
        "model_training_source": {
            category: _first_model_training_source(advice.get(category, []))
            for category in ADVICE_FILES
        },
        "model_training_source_quality": {
            category: _first_non_empty_value(advice.get(category, []), "model_training_source_quality")
            for category in ADVICE_FILES
        },
        "model_load_quality": {
            category: _first_model_load_quality(advice.get(category, []))
            for category in ADVICE_FILES
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _route_advice(row: dict[str, Any], models: ShadowModels) -> dict[str, Any]:
    score = models.route.score_row(row) if models.route_available else None
    return _base_advice_row(
        row,
        model_type="route_risk",
        model_path=models.route.path,
        model_available=models.route_available,
        score_name="route_risk_score",
        score=score,
        advice=_risk_advice(score, high="avoid_or_require_recovery", medium="watch_route", low="route_ok"),
        model_metadata=models.route.metadata,
    )


def _score_row(category: str, row: dict[str, Any], models: ShadowModels) -> dict[str, Any] | None:
    if category == "route_risk":
        return _route_advice(row, models)
    if category == "potion_tempo":
        return _potion_advice(row, models)
    if category == "pre_boss_deck_quality":
        return _deck_advice(row, models)
    if category == "combat_search":
        return _combat_search_advice(row, models)
    return None


def _potion_advice(row: dict[str, Any], models: ShadowModels) -> dict[str, Any]:
    score = models.potion.score_row(row) if models.potion_available else None
    return _base_advice_row(
        row,
        model_type="potion_tempo",
        model_path=models.potion.path,
        model_available=models.potion_available,
        score_name="potion_tempo_score",
        score=score,
        advice=_risk_advice(score, high="consider_potion", medium="watch_potion_tempo", low="hold_potion"),
        model_metadata=models.potion.metadata,
    )


def _deck_advice(row: dict[str, Any], models: ShadowModels) -> dict[str, Any]:
    score = models.deck.score_row(row) if models.deck_available else None
    if score is None:
        advice = "model_missing"
    elif score >= 0.65:
        advice = "boss_ready"
    elif score >= 0.45:
        advice = "boss_borderline"
    else:
        advice = "needs_boss_resources"
    return _base_advice_row(
        row,
        model_type="pre_boss_deck_quality",
        model_path=models.deck.path,
        model_available=models.deck_available,
        score_name="deck_quality_score",
        score=score,
        advice=advice,
        model_metadata=models.deck.metadata,
    )


def _combat_search_advice(row: dict[str, Any], models: ShadowModels) -> dict[str, Any]:
    label = _combat_label_key(row)
    ranked = models.combat.rank_hand(row) if models.combat_available else []
    top = ranked[0] if ranked else {}
    top_score = top.get("score") if top else None
    label_score = models.combat.score_card(label, row) if models.combat_available and label else None
    agrees = bool(label and top.get("card_key") == label)
    if not models.combat_available:
        advice = "model_missing"
    elif row.get("label_missed_direct_kill"):
        advice = "review_missed_lethal"
    elif row.get("label_missed_single_card_search"):
        advice = "review_missed_single_card_search"
    elif not ranked:
        advice = "missing_hand"
    elif agrees:
        advice = "model_matches_search"
    else:
        advice = "review_search_label"
    return _base_advice_row(
        row,
        model_type="combat_search",
        model_path=models.combat.path,
        model_available=models.combat_available,
        score_name="combat_search_score",
        score=top_score,
        advice=advice,
        model_metadata=models.combat.metadata,
    ) | {
        "turn": row.get("turn"),
        "incoming": row.get("incoming"),
        "current_hp": row.get("current_hp"),
        "current_energy": row.get("current_energy"),
        "actual_action": row.get("actual_action"),
        "actual_card_index": row.get("actual_card_index"),
        "actual_target_index": row.get("actual_target_index"),
        "label_first_card_key": label,
        "label_card_index": row.get("label_card_index"),
        "label_target_index": row.get("label_target_index"),
        "label_sequence_card_keys": row.get("label_sequence_card_keys") or [],
        "model_top_card_key": top.get("card_key"),
        "model_top_card_index": top.get("card_index"),
        "model_label_score": label_score,
        "model_agrees_with_label": agrees if models.combat_available and ranked else None,
        "combat_search_margin": None if top_score is None or label_score is None else round(float(top_score) - float(label_score), 4),
        "ranked_cards": ranked[:3],
        "initial_loss": row.get("initial_loss"),
        "projected_loss": row.get("projected_loss"),
        "loss_delta": row.get("loss_delta"),
        "attacks_removed": row.get("attacks_removed"),
        "retaliation_damage": row.get("retaliation_damage"),
        "avoided_lethal": row.get("avoided_lethal"),
        "direct_kill_available": bool(row.get("direct_kill_available")),
        "direct_kill_card_indices": row.get("direct_kill_card_indices") or [],
        "direct_kill_card_keys": row.get("direct_kill_card_keys") or [],
        "direct_kill_enemy_id": row.get("direct_kill_enemy_id"),
        "direct_kill_enemy_hp": row.get("direct_kill_enemy_hp"),
        "label_missed_direct_kill": bool(row.get("label_missed_direct_kill")),
        "label_missed_single_card_search": bool(row.get("label_missed_single_card_search")),
    }


def _base_advice_row(
    row: dict[str, Any],
    *,
    model_type: str,
    model_path: Path,
    model_available: bool,
    score_name: str,
    score: float | None,
    advice: str,
    model_metadata: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "model_type": model_type,
        "model_path": str(model_path),
        "model_available": model_available,
        "model_examples": model_metadata.get("examples"),
        "model_source_quality": model_metadata.get("source_quality") or {},
        "model_training_source_quality": model_metadata.get("training_source_quality"),
        "model_training_source": model_metadata.get("training_source") or {},
        "model_load_quality": model_metadata.get("load_quality") or {},
        score_name: score,
        "advice": advice,
        "source_log": row.get("source_log"),
        "step": row.get("step"),
        "floor": row.get("floor"),
        "act": row.get("act"),
        "character": row.get("character"),
        "ascension": row.get("ascension"),
    }
    if model_type == "route_risk":
        result.update(
            {
                "selected_choice": row.get("selected_choice"),
                "selected_symbol": row.get("selected_symbol"),
                "route_score": row.get("route_score"),
                "readiness_penalty": row.get("readiness_penalty"),
                "readiness_flags": row.get("readiness_flags") or row.get("readiness_risk_flags") or [],
                "act2_route_flags": row.get("act2_route_flags") or [],
            }
        )
    elif model_type == "potion_tempo":
        result.update(
            {
                "turn": row.get("turn"),
                "incoming": row.get("incoming"),
                "potion_ids": row.get("potion_ids") or [],
                "used_potion": row.get("used_potion"),
                "used_potion_slot": row.get("used_potion_slot"),
                "used_potion_targeted": row.get("used_potion_targeted"),
                "enemy_ids": row.get("enemy_ids") or [],
            }
        )
        result.update(
            _copy_present_fields(
                row,
                (
                    "potion_block_value",
                    "potion_damage_value",
                    "potion_energy_value",
                    "potion_draw_value",
                    "potion_generated_options_value",
                    "potion_play_top_cards_value",
                    "potion_strength_value",
                    "potion_dexterity_value",
                    "potion_temporary_dexterity_value",
                    "potion_vulnerable_value",
                    "potion_weak_value",
                    "potion_artifact_value",
                    "potion_ritual_value",
                    "potion_healing_value",
                    "potion_heal_percent_max_hp",
                    "potion_targeted_count",
                    "potion_noncombat_count",
                    "enemy_boss_count",
                    "enemy_elite_or_boss_count",
                    "enemy_total_expected_attack",
                    "enemy_max_expected_attack",
                    "boss_identity_known",
                    "boss_known_count",
                ),
            )
        )
    elif model_type == "pre_boss_deck_quality":
        result.update(
            {
                "deck_size": row.get("deck_size"),
                "hp_ratio": row.get("hp_ratio"),
                "boss_potion_gap": row.get("boss_potion_gap"),
                "readiness_score_boss": row.get("readiness_score_boss"),
                "readiness_gaps": row.get("readiness_gaps") or [],
            }
        )
    return result


def _copy_present_fields(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: row[field] for field in fields if field in row}


def _first_model_load_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        value = row.get("model_load_quality")
        if isinstance(value, dict) and value:
            return value
    return {}


def _first_model_training_source(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in rows:
        value = row.get("model_training_source")
        if isinstance(value, dict) and value:
            return value
    return {}


def _first_non_empty_value(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        value = row.get(key)
        if value not in (None, "", {}, []):
            return value
    return None


def _shadow_input_summary(
    advice: dict[str, list[dict[str, Any]]],
    *,
    shadow_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    all_files: list[str] = []
    all_modes: set[str] = set()
    input_categories = shadow_inputs.get("categories", {}) if isinstance(shadow_inputs, dict) else {}
    for category in ADVICE_FILES:
        rows = advice.get(category, [])
        input_category = input_categories.get(category, {}) if isinstance(input_categories, dict) else {}
        source_files = input_category.get("resolved_files") if isinstance(input_category, dict) else None
        if isinstance(source_files, list):
            files = sorted({str(path) for path in source_files})
        else:
            files = sorted({str(row.get("shadow_source_file")) for row in rows if row.get("shadow_source_file")})
        modes = sorted({str(row.get("shadow_source_mode")) for row in rows if row.get("shadow_source_mode")})
        if not modes and files:
            modes = ["shadow_rows"]
        all_files.extend(files)
        all_modes.update(modes)
        warnings = list(input_category.get("warnings", [])) if isinstance(input_category, dict) else []
        if rows and not files and modes != ["in_memory_examples"]:
            warnings.append("no_shadow_source_files")
        categories[category] = {
            "row_count": len(rows),
            "modes": modes,
            "resolved_files": files,
            "resolved_file_count": len(files),
            "warnings": warnings,
        }
    input_files = shadow_inputs.get("resolved_files") if isinstance(shadow_inputs, dict) else None
    resolved_files = sorted({str(path) for path in input_files}) if isinstance(input_files, list) else sorted(set(all_files))
    if resolved_files:
        mode = str(shadow_inputs.get("mode") or "shadow_rows") if isinstance(shadow_inputs, dict) else "shadow_rows"
    elif all_modes == {"in_memory_examples"}:
        mode = "in_memory_examples"
    elif all_modes:
        mode = "mixed"
    else:
        mode = "empty"
    input_warnings = shadow_inputs.get("warnings", []) if isinstance(shadow_inputs, dict) else []
    warnings = list(dict.fromkeys([str(warning) for warning in input_warnings] + [warning for item in categories.values() for warning in item["warnings"]]))
    return {
        "mode": mode,
        "resolved_files": resolved_files,
        "resolved_file_count": len(resolved_files),
        "categories": categories,
        "warnings": warnings,
    }


def _combat_label_key(row: dict[str, Any]) -> str:
    key = combat_card_key(row.get("label_first_card_key"))
    if key:
        return key
    sequence = row.get("label_sequence_card_keys")
    if isinstance(sequence, list) and sequence:
        return combat_card_key(sequence[0])
    return ""


def _risk_advice(score: float | None, *, high: str, medium: str, low: str) -> str:
    if score is None:
        return "model_missing"
    if score >= 0.65:
        return high
    if score >= 0.45:
        return medium
    return low


def _iter_shadow_files(paths: Iterable[Path]) -> Iterable[tuple[str, Path]]:
    for path in paths:
        if path.is_dir():
            for category, filename in SHADOW_FILES.items():
                candidate = path / filename
                if candidate.exists():
                    yield category, candidate
        elif path.exists():
            category = _category_from_file(path)
            if category:
                yield category, path


def _category_from_file(path: Path) -> str | None:
    for category, filename in SHADOW_FILES.items():
        if path.name == filename or path.stem == Path(filename).stem:
            return category
    return None


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                yield row


def _any_available(rows: list[dict[str, Any]]) -> bool:
    return any(bool(row.get("model_available")) for row in rows)


if __name__ == "__main__":
    raise SystemExit(main())
