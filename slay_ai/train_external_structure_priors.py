"""Train isolated structural priors from external Slay the Spire run histories.

These priors adapt external `.run` data into training evidence for reward
skipping, card removal, and deck cycle quality. They are scratch artifacts only:
they do not control live MCP decisions and must not be counted as pristine data.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .import_external_runs import EXTERNAL_PRIOR_GRADE, EXTERNAL_RUN_HISTORY
from .import_external_runs import iter_external_files, iter_external_records
from .memory import normalize_card_name
from .train_card_model import hardware_metadata


ROOT = Path(__file__).resolve().parents[1]
STATIC_CARDS_PATH = ROOT / "data" / "static_knowledge" / "cards.json"
CARD_REWARD_DECISIONS_FILE = "card_reward_decisions.jsonl"
CARD_PURGE_PRIORS_FILE = "card_purge_priors.jsonl"
DECK_CYCLE_PRIORS_FILE = "deck_cycle_priors.jsonl"
STRUCTURE_MODEL_FILE = "structure_prior_model.json"
FORBIDDEN_USES = ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"]


@dataclass
class ExternalStructureSource:
    source_id: str
    source_uri: str | None
    source_weight: float
    input_paths: list[Path]
    resolved_files: list[Path]
    manifest_paths: list[Path]
    warnings: list[str]
    filters: dict[str, Any]


@dataclass
class NormalizedExternalRun:
    source_file: Path
    source_index: int
    character: str
    ascension: int
    victory: bool
    final_floor: int
    score: int | None
    card_choices: list[dict[str, Any]]
    items_purged: list[str]
    items_purged_floors: list[int]
    purchased_purges: int
    master_deck: list[str]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train isolated structure priors from external STS run history.")
    parser.add_argument("inputs", nargs="+", type=Path, help="External manifest, run file, or directory.")
    parser.add_argument("--source", help="Stable source id when raw inputs do not include a manifest.")
    parser.add_argument("--source-uri", default=None)
    parser.add_argument("--source-weight", type=float, default=1.0)
    parser.add_argument("--rows-dir", type=Path, help="Where adapted JSONL rows are written.")
    parser.add_argument("--model-dir", type=Path, help="Where structure_prior_model.json is written.")
    parser.add_argument("--backend", choices=["auto", "stats"], default="auto")
    args = parser.parse_args(argv)

    summary = train_external_structure_priors(
        args.inputs,
        source_id=args.source,
        source_uri=args.source_uri,
        source_weight=args.source_weight,
        rows_dir=args.rows_dir,
        model_dir=args.model_dir,
        backend=args.backend,
    )
    print(
        "external_structure_training: "
        f"source={summary['training_source']['source_id']} runs={summary['runs']} "
        f"reward_decisions={summary['reward_decision_rows']} "
        f"purges={summary['purge_rows']} deck_cycles={summary['deck_cycle_rows']}"
    )
    print(f"Wrote structure prior model: {summary['model_path']}")
    print(f"Wrote summary: {summary['summary_path']}")
    return 0


def train_external_structure_priors(
    inputs: Iterable[Path],
    *,
    source_id: str | None = None,
    source_uri: str | None = None,
    source_weight: float = 1.0,
    rows_dir: Path | None = None,
    model_dir: Path | None = None,
    backend: str = "auto",
) -> dict[str, Any]:
    source = resolve_external_structure_source(
        inputs,
        source_id=source_id,
        source_uri=source_uri,
        source_weight=source_weight,
    )
    output_rows_dir = rows_dir or _default_rows_dir(source)
    output_model_dir = model_dir or (ROOT / "data" / "external_models" / source.source_id)
    output_rows_dir.mkdir(parents=True, exist_ok=True)
    output_model_dir.mkdir(parents=True, exist_ok=True)

    card_tags = load_card_tag_index()
    reward_rows: list[dict[str, Any]] = []
    purge_rows: list[dict[str, Any]] = []
    deck_rows: list[dict[str, Any]] = []
    accepted_runs = 0
    skipped_runs = 0
    seen_runs = 0
    skip_reasons: dict[str, int] = {}
    limit_runs = _source_limit_runs(source)
    stop_after_limit = False

    for file_path in source.resolved_files:
        if stop_after_limit:
            break
        for index, record in enumerate(iter_external_records(file_path), start=1):
            if limit_runs is not None and seen_runs >= limit_runs:
                stop_after_limit = True
                break
            seen_runs += 1
            run, reason = normalize_external_structure_run(record, file_path, index)
            if reason is None and run is not None:
                reason = _filter_external_structure_run(run, source)
            if reason:
                skipped_runs += 1
                _count(skip_reasons, reason)
                if limit_runs is not None and seen_runs >= limit_runs:
                    stop_after_limit = True
                    break
                continue
            assert run is not None
            accepted_runs += 1
            reward_rows.extend(card_reward_decision_rows(run, source))
            purge_rows.extend(card_purge_rows(run, source))
            deck_rows.append(deck_cycle_row(run, source, card_tags))
            if limit_runs is not None and seen_runs >= limit_runs:
                stop_after_limit = True
                break

    reward_path = output_rows_dir / CARD_REWARD_DECISIONS_FILE
    purge_path = output_rows_dir / CARD_PURGE_PRIORS_FILE
    deck_path = output_rows_dir / DECK_CYCLE_PRIORS_FILE
    _write_jsonl(reward_path, reward_rows)
    _write_jsonl(purge_path, purge_rows)
    _write_jsonl(deck_path, deck_rows)

    model = build_structure_prior_model(reward_rows, purge_rows, deck_rows, source, backend=backend)
    model_path = output_model_dir / STRUCTURE_MODEL_FILE
    model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "version": 1,
        "status": "trained" if accepted_runs else "skipped",
        "runs": accepted_runs,
        "seen_runs": seen_runs,
        "skipped_runs": skipped_runs,
        "skip_reasons": skip_reasons,
        "reward_decision_rows": len(reward_rows),
        "purge_rows": len(purge_rows),
        "deck_cycle_rows": len(deck_rows),
        "rows": {
            "card_reward_decisions": str(reward_path),
            "card_purge_priors": str(purge_path),
            "deck_cycle_priors": str(deck_path),
        },
        "model_path": str(model_path),
        "summary_path": str(output_model_dir / "external_structure_training_summary.json"),
        "training_source": training_source_payload(source),
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "does_not_control_live_mcp": True,
        "forbidden_uses": FORBIDDEN_USES,
    }
    summary_path = output_model_dir / "external_structure_training_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def resolve_external_structure_source(
    inputs: Iterable[Path],
    *,
    source_id: str | None,
    source_uri: str | None,
    source_weight: float,
) -> ExternalStructureSource:
    input_paths = [Path(path) for path in inputs]
    resolved_files: list[Path] = []
    manifest_paths: list[Path] = []
    source_ids: list[str] = []
    warnings: list[str] = []
    manifest_source_uri = source_uri
    manifest_source_weight: float | None = None
    manifest_filters: dict[str, Any] = {}

    for path in input_paths:
        if not path.exists():
            warnings.append(f"missing:{path}")
            continue
        if path.is_file() and path.name == "external_manifest.json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest_paths.append(path)
            manifest_source_uri = manifest_source_uri or payload.get("source_uri")
            if payload.get("source_id"):
                source_ids.append(str(payload["source_id"]))
            if payload.get("source_weight") is not None:
                manifest_source_weight = float(payload["source_weight"])
            if isinstance(payload.get("filters"), dict) and not manifest_filters:
                manifest_filters = dict(payload["filters"])
            for file_text in payload.get("resolved_files") or []:
                candidate = Path(file_text)
                if not candidate.is_absolute() and not candidate.exists():
                    candidate = (path.parent / candidate).resolve()
                if candidate.exists():
                    resolved_files.append(candidate)
            continue
        if path.is_dir():
            manifest = path / "external_manifest.json"
            if manifest.exists():
                nested = resolve_external_structure_source(
                    [manifest],
                    source_id=source_id,
                    source_uri=source_uri,
                    source_weight=source_weight,
                )
                manifest_paths.extend(nested.manifest_paths)
                resolved_files.extend(nested.resolved_files)
                source_ids.append(nested.source_id)
                manifest_source_uri = manifest_source_uri or nested.source_uri
                manifest_source_weight = nested.source_weight
                if nested.filters and not manifest_filters:
                    manifest_filters = dict(nested.filters)
            else:
                resolved_files.extend(iter_external_files([path]))
            continue
        resolved_files.extend(iter_external_files([path]))

    resolved_unique = list(dict.fromkeys(path.resolve() for path in resolved_files))
    if not resolved_unique:
        warnings.append("no_resolved_external_files")
    final_source_id = source_id or (source_ids[0] if source_ids else "external_structure_prior")
    return ExternalStructureSource(
        source_id=_safe_artifact_name(final_source_id),
        source_uri=manifest_source_uri,
        source_weight=float(manifest_source_weight if manifest_source_weight is not None else source_weight),
        input_paths=input_paths,
        resolved_files=resolved_unique,
        manifest_paths=list(dict.fromkeys(manifest_paths)),
        warnings=warnings,
        filters=manifest_filters,
    )


def normalize_external_structure_run(
    record: dict[str, Any], source_file: Path, source_index: int
) -> tuple[NormalizedExternalRun | None, str | None]:
    character = _normalize_character(_first_value(record, "character", "character_chosen", "player_chosen", "class", "hero"))
    ascension = _int_or_none(_first_value(record, "ascension", "ascension_level", "ascensionLevel", "ascension_level_chosen"))
    victory = _bool_or_none(_first_value(record, "victory", "is_victory", "won"))
    final_floor = _int_or_none(_first_value(record, "floor_reached", "floor", "final_floor", "floorReached"))
    if not character:
        return None, "missing_character"
    if ascension is None:
        return None, "missing_ascension"
    if victory is None:
        return None, "missing_victory"
    if final_floor is None:
        return None, "missing_final_floor"
    return (
        NormalizedExternalRun(
            source_file=source_file,
            source_index=source_index,
            character=character,
            ascension=ascension,
            victory=victory,
            final_floor=final_floor,
            score=_int_or_none(record.get("score")),
            card_choices=extract_card_choices_with_skip(record),
            items_purged=_list_of_text(_first_value(record, "items_purged", "cards_removed", "removed_cards")),
            items_purged_floors=_list_of_ints(_first_value(record, "items_purged_floors", "cards_removed_floors")),
            purchased_purges=_int_or_none(_first_value(record, "purchased_purges", "shop_purges")) or 0,
            master_deck=_list_of_text(_first_value(record, "master_deck", "deck", "cards")),
        ),
        None,
    )


def _source_limit_runs(source: ExternalStructureSource) -> int | None:
    value = source.filters.get("limit_runs") if isinstance(source.filters, dict) else None
    if value is None:
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, limit)


def _filter_external_structure_run(run: NormalizedExternalRun, source: ExternalStructureSource) -> str | None:
    filters = source.filters if isinstance(source.filters, dict) else {}
    characters = {_normalize_character(character) for character in filters.get("characters") or []}
    if characters and run.character not in characters:
        return "filtered_character"
    ascension_min = _int_or_none(filters.get("ascension_min"))
    ascension_max = _int_or_none(filters.get("ascension_max"))
    if ascension_min is not None and run.ascension < ascension_min:
        return "filtered_ascension"
    if ascension_max is not None and run.ascension > ascension_max:
        return "filtered_ascension"
    return None

def extract_card_choices_with_skip(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _first_value(record, "card_choices", "cardChoices", "card_rewards", "cardRewards", "card_reward_choices")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for index, choice in enumerate(raw, start=1):
        if isinstance(choice, str):
            picked = choice
            not_picked: list[str] = []
            floor = index
        elif isinstance(choice, dict):
            picked = _first_value(choice, "picked", "picked_card", "pickedCard", "choice")
            not_picked = _list_of_text(_first_value(choice, "not_picked", "notPicked", "options", "cards"))
            floor = _int_or_none(_first_value(choice, "floor", "floor_num", "floorNum")) or index
        else:
            continue
        picked_text = _card_text(picked)
        if not picked_text:
            continue
        is_skip = picked_text.lower() in {"skip", "skipped", "skip reward", "skip_card", "skip card"}
        options = _dedupe_cards(not_picked if is_skip else [picked_text, *not_picked])
        if not options:
            continue
        result.append(
            {
                "floor": floor,
                "decision": "skip" if is_skip else "take",
                "picked": None if is_skip else picked_text,
                "raw_picked": picked_text,
                "options": options,
            }
        )
    return result


def card_reward_decision_rows(run: NormalizedExternalRun, source: ExternalStructureSource) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for choice in run.card_choices:
        rows.append(
            _base_external_row(run, source)
            | {
                "floor": int(choice.get("floor") or 0),
                "decision": choice["decision"],
                "picked": choice.get("picked"),
                "options": choice.get("options") or [],
                "option_count": len(choice.get("options") or []),
                "transform_target": "take_vs_skip",
            }
        )
    return rows


def card_purge_rows(run: NormalizedExternalRun, source: ExternalStructureSource) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, card in enumerate(run.items_purged, start=1):
        floor = run.items_purged_floors[index - 1] if index <= len(run.items_purged_floors) else 0
        rows.append(
            _base_external_row(run, source)
            | {
                "floor": floor,
                "purge_index": index,
                "removed": card,
                "removed_base": _base_card_name(card),
                "purchased_purge_count": run.purchased_purges,
                "likely_shop_purge": index <= run.purchased_purges,
                "transform_target": "remove_vs_keep",
            }
        )
    return rows


def deck_cycle_row(
    run: NormalizedExternalRun, source: ExternalStructureSource, card_tags: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    deck = [_base_card_name(card) for card in run.master_deck]
    tag_counts: dict[str, int] = defaultdict(int)
    type_counts: dict[str, int] = defaultdict(int)
    for card in deck:
        info = card_tags.get(normalize_card_name(card), {})
        for tag in info.get("tags", []):
            tag_counts[str(tag).lower()] += 1
        if info.get("type"):
            type_counts[str(info["type"]).upper()] += 1
    take_count = sum(1 for choice in run.card_choices if choice.get("decision") == "take")
    skip_count = sum(1 for choice in run.card_choices if choice.get("decision") == "skip")
    deck_size = len(deck)
    starter_cards = {"Strike_R", "Strike", "Defend_R", "Defend", "Bash"}
    starter_count = sum(1 for card in deck if card in starter_cards)
    row = _base_external_row(run, source)
    row.update(
        {
            "deck_size": deck_size,
            "unique_card_count": len(set(deck)),
            "starter_count": starter_count,
            "strike_count": sum(1 for card in deck if card in {"Strike_R", "Strike"}),
            "defend_count": sum(1 for card in deck if card in {"Defend_R", "Defend"}),
            "curse_count": sum(1 for card in deck if "curse" in card.lower() or "bane" in card.lower()),
            "draw_card_count": tag_counts.get("draw", 0),
            "exhaust_card_count": tag_counts.get("exhaust", 0),
            "zero_cost_count": tag_counts.get("zero_cost", 0),
            "attack_count": type_counts.get("ATTACK", 0),
            "skill_count": type_counts.get("SKILL", 0),
            "power_count": type_counts.get("POWER", 0),
            "purge_count": len(run.items_purged),
            "card_reward_take_count": take_count,
            "card_reward_skip_count": skip_count,
            "card_reward_skip_rate": round(skip_count / max(take_count + skip_count, 1), 4),
            "starter_density": round(starter_count / max(deck_size, 1), 4),
            "draw_density": round(tag_counts.get("draw", 0) / max(deck_size, 1), 4),
            "transform_target": "draw_cycle_quality",
        }
    )
    return row


def build_structure_prior_model(
    reward_rows: list[dict[str, Any]],
    purge_rows: list[dict[str, Any]],
    deck_rows: list[dict[str, Any]],
    source: ExternalStructureSource,
    *,
    backend: str,
) -> dict[str, Any]:
    take_scores: dict[str, list[float]] = defaultdict(list)
    skipped_offer_scores: dict[str, list[float]] = defaultdict(list)
    floor_skip: dict[str, dict[str, int]] = defaultdict(lambda: {"skip": 0, "take": 0})
    for row in reward_rows:
        reward = _outcome_reward(bool(row["victory"]), int(row["final_floor"])) * float(row.get("source_weight") or 1.0)
        bucket = _floor_bucket(int(row.get("floor") or 0))
        floor_skip[bucket][str(row["decision"])] += 1
        if row["decision"] == "take" and row.get("picked"):
            take_scores[_card_key(str(row["picked"]), row["character"])].append(reward)
        elif row["decision"] == "skip":
            for option in row.get("options") or []:
                skipped_offer_scores[_card_key(str(option), row["character"])].append(reward)

    purge_scores: dict[str, list[float]] = defaultdict(list)
    for row in purge_rows:
        reward = _outcome_reward(bool(row["victory"]), int(row["final_floor"])) * float(row.get("source_weight") or 1.0)
        purge_scores[_card_key(str(row["removed_base"]), row["character"])].append(reward)

    deck_feature_means = _deck_feature_means(deck_rows)
    deck_feature_lifts = _deck_feature_lifts(deck_rows)
    metadata = {
        "version": 1,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "model_kind": "external_structure_prior",
        "training_source": training_source_payload(source),
        "training_source_quality": EXTERNAL_PRIOR_GRADE,
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "does_not_control_live_mcp": True,
        "forbidden_uses": FORBIDDEN_USES,
    }
    metadata.update(hardware_metadata(backend))
    return {
        "metadata": metadata,
        "card_reward": {
            "take_card_deltas": _mean_scores(take_scores),
            "skipped_offer_deltas": _mean_scores(skipped_offer_scores),
            "floor_skip_rates": {
                bucket: round(counts["skip"] / max(counts["skip"] + counts["take"], 1), 4)
                for bucket, counts in sorted(floor_skip.items())
            },
        },
        "purge": {
            "removed_card_deltas": _mean_scores(purge_scores),
        },
        "deck_cycle": {
            "feature_means": deck_feature_means,
            "victory_lifts": deck_feature_lifts,
        },
    }


def load_card_tag_index(path: Path = STATIC_CARDS_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(cards, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        keys = [card.get("id"), card.get("name"), *(card.get("aliases") or [])]
        for key in keys:
            if key:
                index[normalize_card_name(str(key))] = card
    return index


def training_source_payload(source: ExternalStructureSource) -> dict[str, Any]:
    return {
        "mode": "external_structure_prior",
        "source_category": EXTERNAL_RUN_HISTORY,
        "source_quality_policy": EXTERNAL_PRIOR_GRADE,
        "source_id": source.source_id,
        "source_uri": source.source_uri,
        "source_weight": source.source_weight,
        "inputs": [str(path) for path in source.input_paths],
        "manifest_paths": [str(path) for path in source.manifest_paths],
        "resolved_files": [str(path) for path in source.resolved_files],
        "resolved_file_count": len(source.resolved_files),
        "warnings": source.warnings,
        "filters": source.filters,
        "forbidden_uses": FORBIDDEN_USES,
    }


def _base_external_row(run: NormalizedExternalRun, source: ExternalStructureSource) -> dict[str, Any]:
    return {
        "character": run.character,
        "ascension": run.ascension,
        "victory": run.victory,
        "final_floor": run.final_floor,
        "score": run.score,
        "source_category": EXTERNAL_RUN_HISTORY,
        "source_reason": EXTERNAL_PRIOR_GRADE,
        "source_validation_grade": EXTERNAL_PRIOR_GRADE,
        "source_validation_flags": ["external", "not_mcp", "not_pristine"],
        "source_dataset": source.source_id,
        "source_uri": source.source_uri,
        "source_file": str(run.source_file),
        "source_index": run.source_index,
        "source_weight": source.source_weight,
        "transform_version": 1,
    }


def _default_rows_dir(source: ExternalStructureSource) -> Path:
    if source.manifest_paths:
        return source.manifest_paths[0].parent
    return ROOT / "data" / "external" / "sts_runs" / source.source_id


def _deck_feature_means(rows: list[dict[str, Any]]) -> dict[str, float]:
    features = _deck_numeric_features()
    return {feature: round(_mean([float(row.get(feature) or 0.0) for row in rows]), 4) for feature in features}


def _deck_feature_lifts(rows: list[dict[str, Any]]) -> dict[str, float]:
    features = _deck_numeric_features()
    winners = [row for row in rows if row.get("victory")]
    losses = [row for row in rows if not row.get("victory")]
    result: dict[str, float] = {}
    for feature in features:
        win_mean = _mean([float(row.get(feature) or 0.0) for row in winners])
        loss_mean = _mean([float(row.get(feature) or 0.0) for row in losses])
        result[feature] = round(win_mean - loss_mean, 4)
    return result


def _deck_numeric_features() -> list[str]:
    return [
        "deck_size",
        "unique_card_count",
        "starter_count",
        "strike_count",
        "defend_count",
        "curse_count",
        "draw_card_count",
        "exhaust_card_count",
        "zero_cost_count",
        "attack_count",
        "skill_count",
        "power_count",
        "purge_count",
        "card_reward_take_count",
        "card_reward_skip_count",
        "card_reward_skip_rate",
        "starter_density",
        "draw_density",
    ]


def _mean_scores(values: dict[str, list[float]]) -> dict[str, float]:
    return {
        key: round(_mean(scores) * (math.sqrt(len(scores)) / (math.sqrt(len(scores)) + 2.0)), 4)
        for key, scores in sorted(values.items())
        if scores
    }


def _outcome_reward(victory: bool, final_floor: int) -> float:
    if victory:
        return 1.0
    progress = max(0.0, min(1.0, final_floor / 57.0))
    if final_floor < 17:
        return -0.6 + progress * 0.3
    if final_floor < 34:
        return -0.25 + progress * 0.4
    return 0.15 + progress * 0.5


def _floor_bucket(floor: int) -> str:
    if floor <= 16:
        return "act1"
    if floor <= 33:
        return "act2"
    return "act3_plus"


def _card_key(card: str, character: str) -> str:
    return f"{character.upper()}::{normalize_card_name(_base_card_name(card))}"


def _base_card_name(card: Any) -> str:
    text = _card_text(card)
    if "+" in text:
        text = text.split("+", 1)[0]
    return text.strip()


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _normalize_character(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    aliases = {"THE_IRONCLAD": "IRONCLAD", "IRON_CLAD": "IRONCLAD", "THE_SILENT": "SILENT"}
    return aliases.get(text, text)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "win", "won"}:
        return True
    if text in {"false", "0", "no", "loss", "lost"}:
        return False
    return None


def _list_of_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_card_text(item) for item in value if _card_text(item)]
    text = _card_text(value)
    return [text] if text else []


def _list_of_ints(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        number = _int_or_none(item)
        if number is not None:
            result.append(number)
    return result


def _card_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("name")
    return str(value or "").strip()


def _dedupe_cards(cards: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for card in cards:
        text = _card_text(card)
        if not text:
            continue
        key = normalize_card_name(text).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _safe_artifact_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip())
    return cleaned.strip("_") or "external_structure_prior"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


if __name__ == "__main__":
    raise SystemExit(main())