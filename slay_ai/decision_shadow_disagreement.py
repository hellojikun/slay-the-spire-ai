"""Evaluate shadow decision-model disagreement against live run logs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import combat_card_key
from .train_decision_multitask_model import TASK_PURGE_REMOVE, TASK_TAKE_SKIP, score_row
from .train_external_structure_priors import _base_card_name


SHADOW_MODEL_AUTHORITY = {
    "level": "shadow",
    "runtime_authority": False,
    "runtime_default_enabled": False,
    "does_not_control_live_mcp": True,
    "direct_mcp_control": False,
    "requires_audited_promotion": True,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score live logs with a shadow decision model and write disagreements.")
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args(argv)

    summary = evaluate_live_shadow_disagreements(
        args.logs,
        model_path=args.model_path,
        output_path=args.output,
        summary_output=args.summary_output,
    )
    print(
        "decision_shadow_disagreement: "
        f"status={summary['status']} examples={summary.get('examples', 0)} "
        f"disagreements={summary.get('disagreements', 0)} output={summary.get('output_path')}"
    )
    return 0


def evaluate_live_shadow_disagreements(
    logs: Iterable[Path],
    *,
    model_path: Path,
    output_path: Path,
    summary_output: Path | None = None,
) -> dict[str, Any]:
    log_paths = [Path(path) for path in logs]
    examples = extract_live_shadow_examples(log_paths)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if examples:
        with output_path.open("w", encoding="utf-8") as handle:
            for index, row in enumerate(examples, start=1):
                task = str(row.get("task") or TASK_TAKE_SKIP)
                prediction = score_row(model_path, task, row)
                record = _prediction_record(index, row, prediction, model_path)
                records.append(record)
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        output_path.write_text("", encoding="utf-8")

    summary = _summary_payload(
        log_paths,
        model_path=model_path,
        output_path=output_path,
        summary_output=summary_output,
        examples=examples,
        records=records,
    )
    if summary_output:
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def extract_live_shadow_examples(logs: Iterable[Path]) -> list[dict[str, Any]]:
    log_paths = [Path(path) for path in logs]
    return extract_reward_take_skip_examples(log_paths) + extract_purge_remove_examples(log_paths)


def extract_reward_take_skip_examples(logs: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in logs:
        for source_index, record in enumerate(_read_jsonl(Path(path)), start=1):
            state = record.get("state") if isinstance(record.get("state"), dict) else {}
            if state.get("screen_type") != "CARD_REWARD":
                continue
            decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
            actual = _actual_reward_decision(decision)
            if actual is None:
                continue
            options = [_card_key(option) for option in state.get("card_reward_options") or []]
            options = [option for option in options if option]
            picked = _picked_card(decision, state)
            row = _base_live_row(path, source_index, record, state)
            row.update(
                {
                    "task": TASK_TAKE_SKIP,
                    "transform_target": "take_vs_skip",
                    "decision": actual,
                    "picked": picked,
                    "options": options,
                    "option_count": len(options),
                }
            )
            row.update(_deck_features(state))
            rows.append(row)
    return rows


def extract_purge_remove_examples(logs: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in logs:
        records = _read_jsonl(Path(path))
        for source_index, record in enumerate(records, start=1):
            state = record.get("state") if isinstance(record.get("state"), dict) else {}
            decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
            shop_row = _shop_purge_row(path, source_index, record, state, decision)
            if shop_row is not None:
                rows.append(shop_row)
            grid_row = _grid_purge_row(path, source_index, record, state, decision)
            if grid_row is not None:
                rows.append(grid_row)
    return rows


def _base_live_row(path: Path, source_index: int, record: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_dataset": "live_shadow_run_log",
        "source_file": str(path),
        "source_index": source_index,
        "source_validation_grade": "live_shadow_unpromoted",
        "character": state.get("class"),
        "ascension": state.get("ascension_level"),
        "floor": state.get("floor"),
        "act": state.get("act"),
        "step": record.get("step"),
    }


def _shop_purge_row(
    path: Path,
    source_index: int,
    record: dict[str, Any],
    state: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any] | None:
    if state.get("screen_type") != "SHOP_SCREEN":
        return None
    shop = state.get("shop") if isinstance(state.get("shop"), dict) else {}
    if not shop.get("purge_available"):
        return None
    candidate = _candidate_purge_card_from_deck(state)
    if not candidate:
        return None
    chooses_purge = _shop_action_chooses_purge(decision)
    declines_purge = _shop_action_declines_purge(decision)
    if not chooses_purge and not declines_purge:
        return None
    row = _base_live_row(path, source_index, record, state)
    row.update(_deck_features(state))
    row.update(
        {
            "task": TASK_PURGE_REMOVE,
            "transform_target": "remove_vs_keep",
            "decision": "remove" if chooses_purge else "keep_candidate",
            "candidate_card": candidate,
            "removed": candidate if chooses_purge else None,
            "removed_base": candidate if chooses_purge else None,
            "purge_cost": shop.get("purge_cost"),
            "purge_index": 1,
            "purchased_purge_count": 1 if chooses_purge else 0,
            "likely_shop_purge": True,
            "label_source": "live_shop_purge_selected" if chooses_purge else "live_shop_purge_not_selected",
        }
    )
    return row


def _grid_purge_row(
    path: Path,
    source_index: int,
    record: dict[str, Any],
    state: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any] | None:
    if state.get("screen_type") != "GRID":
        return None
    grid = state.get("grid") if isinstance(state.get("grid"), dict) else {}
    if not grid.get("for_purge"):
        return None
    if _choice_index(decision) is None:
        return None
    candidate = _grid_purge_candidate(decision, state)
    if not candidate:
        return None
    row = _base_live_row(path, source_index, record, state)
    row.update(_deck_features(state))
    row.update(
        {
            "task": TASK_PURGE_REMOVE,
            "transform_target": "remove_vs_keep",
            "decision": "remove",
            "candidate_card": candidate,
            "removed": candidate,
            "removed_base": candidate,
            "purge_index": 1,
            "purchased_purge_count": 1,
            "likely_shop_purge": False,
            "label_source": "live_grid_purge_selected",
        }
    )
    return row


def _prediction_record(index: int, row: dict[str, Any], prediction: float, model_path: Path) -> dict[str, Any]:
    task = str(row.get("task") or TASK_TAKE_SKIP)
    actual = str(row.get("decision") or "")
    if task == TASK_PURGE_REMOVE:
        predicted = "remove" if prediction >= 0.5 else "keep_candidate"
    else:
        predicted = "take" if prediction >= 0.5 else "skip"
    confidence = max(float(prediction), 1.0 - float(prediction))
    record = {
        "index": index,
        "task": task,
        "source_log": row.get("source_file"),
        "source_index": row.get("source_index"),
        "step": row.get("step"),
        "floor": row.get("floor"),
        "character": row.get("character"),
        "actual_decision": actual,
        "predicted_decision": predicted,
        "prediction": round(float(prediction), 4),
        "confidence": round(confidence, 4),
        "correct": predicted == actual,
        "model_path": str(model_path),
        "model_authority": dict(SHADOW_MODEL_AUTHORITY),
    }
    if task == TASK_PURGE_REMOVE:
        record.update(
            {
                "candidate_card": row.get("candidate_card"),
                "label_source": row.get("label_source"),
                "purge_cost": row.get("purge_cost"),
            }
        )
    else:
        record.update({"picked": row.get("picked"), "options": row.get("options") or []})
    return record


def _summary_payload(
    logs: list[Path],
    *,
    model_path: Path,
    output_path: Path,
    summary_output: Path | None,
    examples: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    disagreements = [record for record in records if not record.get("correct")]
    high_confidence_disagreements = [
        record for record in disagreements if float(record.get("confidence") or 0.0) >= 0.75
    ]
    actual_counts = Counter(str(record.get("actual_decision") or "unknown") for record in records)
    predicted_counts = Counter(str(record.get("predicted_decision") or "unknown") for record in records)
    examples_count = len(records)
    accuracy = None
    if examples_count:
        accuracy = round((examples_count - len(disagreements)) / examples_count, 4)
    return {
        "status": "evaluated" if examples else "skipped",
        "reason": None if examples else "no_supported_live_decision_examples",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_path": str(model_path),
        "output_path": str(output_path),
        "summary_output": str(summary_output) if summary_output else None,
        "logs": [str(path) for path in logs],
        "examples": examples_count,
        "extracted_examples": len(examples),
        "task_counts": _count_by(records, "task"),
        "disagreements": len(disagreements),
        "disagreements_by_task": _disagreements_by_task(disagreements),
        "accuracy": accuracy,
        "actual_counts": dict(sorted(actual_counts.items())),
        "predicted_counts": dict(sorted(predicted_counts.items())),
        "actual_counts_by_task": _decision_counts_by_task(records, "actual_decision"),
        "predicted_counts_by_task": _decision_counts_by_task(records, "predicted_decision"),
        "high_confidence_disagreements": len(high_confidence_disagreements),
        "sample_disagreements": high_confidence_disagreements[:5] or disagreements[:5],
        "supported_surfaces": _supported_surfaces(records),
        "unsupported_surfaces": {},
        "promotion_readiness": _promotion_readiness(records),
        "model_authority": dict(SHADOW_MODEL_AUTHORITY),
    }


def _promotion_readiness(records: list[dict[str, Any]]) -> dict[str, Any]:
    take_records = [record for record in records if record.get("task") == TASK_TAKE_SKIP]
    purge_records = [record for record in records if record.get("task") == TASK_PURGE_REMOVE]
    take_counts = Counter(str(record.get("actual_decision") or "unknown") for record in take_records)
    purge_counts = Counter(str(record.get("actual_decision") or "unknown") for record in purge_records)
    blockers = ["live_shadow_disagreement_audit_only"]
    if len(take_records) < 50:
        blockers.append("insufficient_live_reward_examples")
    if take_counts.get("skip", 0) < 10:
        blockers.append("insufficient_live_skip_examples")
    if len(purge_records) < 20:
        blockers.append("insufficient_live_purge_examples")
    if purge_counts.get("remove", 0) < 10:
        blockers.append("insufficient_live_purge_remove_examples")
    if purge_counts.get("keep_candidate", 0) < 10:
        blockers.append("insufficient_live_purge_keep_examples")
    if any(not record.get("correct") and float(record.get("confidence") or 0.0) >= 0.75 for record in records):
        blockers.append("high_confidence_live_disagreements")
    return {
        "status": "shadow_only",
        "can_promote_to_assist": False,
        "recommended_authority": "shadow",
        "blocking_reasons": _dedupe(blockers),
    }


def _supported_surfaces(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_task = _count_by(records, "task")
    return {
        "card_reward_take_skip": {"status": "evaluated", "examples": by_task.get(TASK_TAKE_SKIP, 0)},
        "purge_remove": {"status": "evaluated", "examples": by_task.get(TASK_PURGE_REMOVE, 0)},
    }


def _actual_reward_decision(decision: dict[str, Any]) -> str | None:
    if decision.get("learn_card_pick"):
        return "take"
    actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
    for action in actions:
        if not isinstance(action, dict):
            continue
        if str(action.get("action") or "").lower() == "skip":
            return "skip"
    return None


def _picked_card(decision: dict[str, Any], state: dict[str, Any]) -> str | None:
    pick = str(decision.get("learn_card_pick") or "").strip()
    if pick:
        return combat_card_key(_base_card_name(pick)) or pick
    choice_index = _choice_index(decision)
    if choice_index is None:
        return None
    options = state.get("card_reward_options") if isinstance(state.get("card_reward_options"), list) else []
    if 1 <= choice_index <= len(options):
        return _card_key(options[choice_index - 1])
    return None


def _choice_index(decision: dict[str, Any]) -> int | None:
    actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
    for action in actions:
        if not isinstance(action, dict) or action.get("action") != "choose":
            continue
        try:
            return int(action.get("choice_index"))
        except (TypeError, ValueError):
            return None
    return None


def _shop_action_chooses_purge(decision: dict[str, Any]) -> bool:
    reason = str(decision.get("reason") or "").lower()
    if "purge" in reason or "remove" in reason:
        return True
    return _choice_index(decision) == 1


def _shop_action_declines_purge(decision: dict[str, Any]) -> bool:
    actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
    if any(isinstance(action, dict) and str(action.get("action") or "").lower() in {"cancel", "skip"} for action in actions):
        return True
    choice = _choice_index(decision)
    return choice is not None and choice != 1


def _candidate_purge_card_from_deck(state: dict[str, Any]) -> str | None:
    cards = state.get("deck_cards") if isinstance(state.get("deck_cards"), list) else []
    keyed = [(_card_key(card), card) for card in cards if isinstance(card, dict)]
    for token in ("strike", "curse", "defend"):
        for key, _card in keyed:
            if token in key.replace("_", "").lower():
                return key
    return keyed[0][0] if keyed else None


def _grid_purge_candidate(decision: dict[str, Any], state: dict[str, Any]) -> str | None:
    grid = state.get("grid") if isinstance(state.get("grid"), dict) else {}
    selected = grid.get("selected_cards") if isinstance(grid.get("selected_cards"), list) else []
    if selected:
        return _card_key(selected[0])
    reason = str(decision.get("reason") or "")
    marker = "Grid choose "
    if marker in reason and " score " in reason:
        return _card_key(reason.split(marker, 1)[1].split(" score ", 1)[0])
    return None


def _deck_features(state: dict[str, Any]) -> dict[str, Any]:
    cards = state.get("deck_cards") if isinstance(state.get("deck_cards"), list) else []
    keys = [_card_key(card) for card in cards]
    keys = [key for key in keys if key]
    deck_size = len(keys)
    strike_count = sum(1 for key in keys if "strike" in key.replace("_", "").lower())
    defend_count = sum(1 for key in keys if "defend" in key.replace("_", "").lower())
    curse_count = sum(1 for card in cards if str(card.get("type") or "").upper() == "CURSE")
    draw_count = sum(1 for key in keys if key in {"battletrance", "pommelstrike", "shrugitoff", "burningpact"})
    exhaust_count = sum(1 for key in keys if key in {"burningpact", "truegrit", "secondwind", "fiendfire"})
    zero_cost_count = sum(1 for card in cards if _safe_int(card.get("cost"), default=99) == 0)
    attack_count = sum(1 for card in cards if str(card.get("type") or "").upper() == "ATTACK")
    skill_count = sum(1 for card in cards if str(card.get("type") or "").upper() == "SKILL")
    power_count = sum(1 for card in cards if str(card.get("type") or "").upper() == "POWER")
    starter_count = strike_count + defend_count + sum(1 for key in keys if key in {"bash", "neutralize", "survivor"})
    return {
        "deck_size": deck_size,
        "unique_card_count": len(set(keys)),
        "starter_count": starter_count,
        "strike_count": strike_count,
        "defend_count": defend_count,
        "curse_count": curse_count,
        "draw_card_count": draw_count,
        "draw_density": round(draw_count / deck_size, 4) if deck_size else 0.0,
        "exhaust_card_count": exhaust_count,
        "zero_cost_count": zero_cost_count,
        "attack_count": attack_count,
        "skill_count": skill_count,
        "power_count": power_count,
    }


def _card_key(card: Any) -> str:
    if isinstance(card, dict):
        raw = card.get("id") or card.get("card_id") or card.get("name")
    else:
        raw = card
    key = combat_card_key(_base_card_name(raw))
    return key or str(raw or "").strip()


def _count_by(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(record.get(field) or "unknown") for record in records).items()))


def _disagreements_by_task(records: list[dict[str, Any]]) -> dict[str, int]:
    return _count_by(records, "task")


def _decision_counts_by_task(records: list[dict[str, Any]], field: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for record in records:
        task = str(record.get("task") or "unknown")
        decision = str(record.get(field) or "unknown")
        result.setdefault(task, {})[decision] = result.setdefault(task, {}).get(decision, 0) + 1
    return {task: dict(sorted(counts.items())) for task, counts in sorted(result.items())}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


if __name__ == "__main__":
    raise SystemExit(main())