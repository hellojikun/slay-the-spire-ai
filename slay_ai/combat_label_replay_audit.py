"""Replay excluded combat-search labels against the current heuristic policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .combat_label_audit import (
    compact_label_row,
    label_example_sort_key,
    row_exclusion_reasons,
)
from .memory import StrategyMemory
from .policy import HeuristicPolicy
from .shadow_inputs import iter_shadow_files
from .shadow_quality import SOURCE_QUALITY_CHOICES, source_quality_allowed


DEFAULT_EXAMPLE_LIMIT = 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay excluded combat_search_labels against current policy.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="Shadow dirs or combat_search_labels.jsonl files.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON.")
    parser.add_argument(
        "--source-quality",
        choices=SOURCE_QUALITY_CHOICES,
        default="all",
        help="Filter label rows before replay. Default 'all' replays every excluded label row.",
    )
    parser.add_argument("--example-limit", type=int, default=DEFAULT_EXAMPLE_LIMIT)
    args = parser.parse_args(argv)

    report = build_replay_report(
        args.shadow_rows,
        source_quality=args.source_quality,
        example_limit=args.example_limit,
    )
    text = json.dumps(report, ensure_ascii=True, indent=None if args.compact else 2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote combat label replay audit: {args.output}")
    else:
        print(text, end="")
    print(report["status_line"])
    return 0


def build_replay_report(
    paths: Iterable[Path],
    *,
    source_quality: str = "all",
    example_limit: int = DEFAULT_EXAMPLE_LIMIT,
    policy: HeuristicPolicy | None = None,
) -> dict[str, Any]:
    input_paths = [Path(path) for path in paths]
    resolved_files = list(iter_shadow_files(input_paths, "combat_search"))
    active_policy = policy or HeuristicPolicy(StrategyMemory.load())
    log_cache: dict[str, dict[int, dict[str, Any]]] = {}
    example_limit_value = max(0, example_limit)
    examples: list[tuple[tuple[int, int, int, int, int], dict[str, Any]]] = []
    counts = {
        "rows": 0,
        "excluded_rows": 0,
        "filtered_rows": 0,
        "replayed_rows": 0,
        "current_policy_matches_label": 0,
        "current_policy_matches_actual": 0,
        "current_policy_other": 0,
        "missing_source_log": 0,
        "missing_source_step": 0,
        "policy_errors": 0,
    }
    reasons: dict[str, int] = {}
    replay_outcomes: dict[str, int] = {}
    read_errors: list[dict[str, Any]] = []

    for label_file in resolved_files:
        try:
            with label_file.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    counts["rows"] += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        read_errors.append({"path": str(label_file), "line": line_number, "error": str(exc)})
                        continue
                    if not isinstance(row, dict):
                        continue
                    if not source_quality_allowed(row, source_quality):
                        counts["filtered_rows"] += 1
                        continue
                    row_reasons = row_exclusion_reasons(row)
                    if not row_reasons:
                        continue
                    counts["excluded_rows"] += 1
                    for reason in row_reasons:
                        _count(reasons, reason)
                    result = _replay_label_row(row, active_policy, log_cache)
                    outcome = str(result.get("outcome") or "unknown")
                    _count(replay_outcomes, outcome)
                    if outcome in counts:
                        counts[outcome] += 1
                    if outcome not in {"missing_source_log", "missing_source_step", "policy_errors"}:
                        counts["replayed_rows"] += 1
                    if example_limit_value > 0:
                        sort_key = _label_example_sort_key_with_outcome(row, outcome, len(examples) + 1)
                        if len(examples) < example_limit_value or sort_key < examples[-1][0]:
                            example = compact_label_row(row, path=label_file, line_number=line_number, reasons=row_reasons)
                            example.update(result)
                            examples.append((sort_key, example))
                            examples = sorted(examples, key=lambda item: item[0])[:example_limit_value]
        except OSError as exc:
            read_errors.append({"path": str(label_file), "error": str(exc)})

    warnings: list[str] = []
    if not resolved_files:
        warnings.append("no_resolved_combat_search_label_files")
    if read_errors:
        warnings.append("read_errors")
    report: dict[str, Any] = {
        "version": 1,
        "inputs": [str(path) for path in input_paths],
        "resolved_files": [str(path) for path in resolved_files],
        "resolved_file_count": len(resolved_files),
        "warnings": warnings,
        "read_errors": read_errors,
        "source_quality_policy": source_quality,
        "exclusion_reasons": dict(sorted(reasons.items())),
        "replay_outcomes": dict(sorted(replay_outcomes.items())),
        "examples": [example for _, example in sorted(examples, key=lambda item: item[0])],
        "example_count": len(examples),
        "example_limit": example_limit_value,
    }
    report.update(counts)
    report["status_line"] = status_line(report)
    return report


def compact_replay_report(raw: Any, *, example_limit: int = 5) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    if raw.get("status_line"):
        result["status_line"] = str(raw["status_line"])
    for field in (
        "resolved_file_count",
        "excluded_rows",
        "replayed_rows",
        "current_policy_matches_label",
        "current_policy_matches_actual",
        "current_policy_other",
        "missing_source_log",
        "missing_source_step",
        "policy_errors",
    ):
        result[field] = int(raw.get(field) or 0)
    replay_outcomes = raw.get("replay_outcomes") if isinstance(raw.get("replay_outcomes"), dict) else {}
    if replay_outcomes:
        result["replay_outcomes"] = {str(key): int(value or 0) for key, value in sorted(replay_outcomes.items())}
    exclusion_reasons = raw.get("exclusion_reasons") if isinstance(raw.get("exclusion_reasons"), dict) else {}
    if exclusion_reasons:
        result["exclusion_reasons"] = {str(key): int(value or 0) for key, value in sorted(exclusion_reasons.items())}
    warnings = [str(item) for item in raw.get("warnings") or [] if item] if isinstance(raw.get("warnings"), list) else []
    if warnings:
        result["warnings"] = warnings
    examples = raw.get("examples") if isinstance(raw.get("examples"), list) else []
    compact_examples: list[dict[str, Any]] = []
    for example in examples:
        if isinstance(example, dict):
            compact_examples.append(example)
        if len(compact_examples) >= max(0, example_limit):
            break
    if compact_examples:
        result["examples"] = compact_examples
    return result


def status_line(report: dict[str, Any]) -> str:
    parts = [
        "combat_label_replay_audit",
        f"files={int(report.get('resolved_file_count') or 0)}",
        f"excluded={int(report.get('excluded_rows') or 0)}",
        f"replayed={int(report.get('replayed_rows') or 0)}",
        f"matches_label={int(report.get('current_policy_matches_label') or 0)}",
        f"matches_actual={int(report.get('current_policy_matches_actual') or 0)}",
        f"other={int(report.get('current_policy_other') or 0)}",
    ]
    if int(report.get("missing_source_log") or 0) > 0:
        parts.append(f"missing_source_log={int(report.get('missing_source_log') or 0)}")
    if int(report.get("missing_source_step") or 0) > 0:
        parts.append(f"missing_source_step={int(report.get('missing_source_step') or 0)}")
    if int(report.get("policy_errors") or 0) > 0:
        parts.append(f"policy_errors={int(report.get('policy_errors') or 0)}")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    if warnings:
        parts.append("warnings=" + ",".join(str(warning) for warning in warnings))
    return " ".join(parts)


def _replay_label_row(
    row: dict[str, Any],
    policy: HeuristicPolicy,
    log_cache: dict[str, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    source_log = str(row.get("source_log") or "")
    if not source_log:
        return {"outcome": "missing_source_log"}
    source_path = Path(source_log)
    if not source_path.exists():
        return {"outcome": "missing_source_log", "source_log": source_log}
    step = _safe_int(row.get("step"))
    records = log_cache.get(str(source_path))
    if records is None:
        try:
            records = _load_log_records_by_step(source_path)
        except OSError as exc:
            return {"outcome": "missing_source_log", "source_log": source_log, "error": str(exc)}
        log_cache[str(source_path)] = records
    record = records.get(step)
    if record is None:
        return {"outcome": "missing_source_step", "source_log": source_log, "step": step}
    state = record.get("state") if isinstance(record.get("state"), dict) else {}
    if not state:
        return {"outcome": "missing_source_step", "source_log": source_log, "step": step}
    try:
        decision = policy.decide(_wrap_state_for_policy(state))
    except Exception as exc:  # pragma: no cover - surfaced in report for log hygiene.
        return {"outcome": "policy_errors", "source_log": source_log, "step": step, "error": str(exc)}
    policy_action = decision.actions[0] if decision.actions else {}
    label_action = _label_action(row)
    actual_action = _actual_action(row)
    matches_label = _same_action(policy_action, label_action)
    matches_actual = _same_action(policy_action, actual_action)
    if matches_label:
        outcome = "current_policy_matches_label"
    elif matches_actual:
        outcome = "current_policy_matches_actual"
    else:
        outcome = "current_policy_other"
    return {
        "outcome": outcome,
        "policy_action": _compact_action(policy_action),
        "policy_reason": decision.reason,
        "label_action": _compact_action(label_action),
        "actual_action": _compact_action(actual_action),
    }


def _load_log_records_by_step(path: Path) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict) and isinstance(record.get("state"), dict):
                records[_safe_int(record.get("step"))] = record
    return records


def _wrap_state_for_policy(state: dict[str, Any]) -> dict[str, Any]:
    if isinstance(state.get("game_state"), dict):
        return state
    game = dict(state)
    combat = game.get("combat_state") if isinstance(game.get("combat_state"), dict) else game.get("combat")
    if isinstance(combat, dict):
        combat_for_policy = dict(combat)
        if combat_for_policy.get("hand_cards"):
            combat_for_policy["hand"] = combat_for_policy["hand_cards"]
        game["combat_state"] = combat_for_policy
    return {"in_game": bool(state.get("in_game", True)), "game_state": game}


def _label_action(row: dict[str, Any]) -> dict[str, Any]:
    action = {"action": row.get("label_action") or "play_card"}
    if row.get("label_card_index") not in (None, "", [], {}):
        action["card_index"] = row.get("label_card_index")
    if row.get("label_target_index") not in (None, "", [], {}):
        action["target_index"] = row.get("label_target_index")
    return action


def _actual_action(row: dict[str, Any]) -> dict[str, Any]:
    action = {"action": row.get("actual_action") or "play_card"}
    if row.get("actual_card_index") not in (None, "", [], {}):
        action["card_index"] = row.get("actual_card_index")
    if row.get("actual_target_index") not in (None, "", [], {}):
        action["target_index"] = row.get("actual_target_index")
    return action


def _same_action(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not left or not right:
        return False
    if str(left.get("action") or "") != str(right.get("action") or ""):
        return False
    for key in ("card_index", "potion_slot", "potion_index", "choice_index"):
        if right.get(key) not in (None, "", [], {}) and _safe_int(left.get(key)) != _safe_int(right.get(key)):
            return False
    target = _safe_int(right.get("target_index"))
    if target > 0 and _safe_int(left.get("target_index")) != target:
        return False
    return True


def _compact_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        key: action[key]
        for key in ("action", "card_index", "target_index", "potion_slot", "potion_index", "choice_index")
        if action.get(key) not in (None, "", [], {})
    }


def _label_example_sort_key_with_outcome(row: dict[str, Any], outcome: str, order: int) -> tuple[int, int, int, int, int]:
    outcome_priority = {
        "current_policy_other": 0,
        "current_policy_matches_actual": 1,
        "policy_errors": 2,
        "missing_source_log": 3,
        "missing_source_step": 3,
        "current_policy_matches_label": 4,
    }.get(outcome, 5)
    return (outcome_priority, *label_example_sort_key(row, order))


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _safe_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
