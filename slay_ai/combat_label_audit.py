"""Audit excluded combat-search labels for offline model hygiene."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .shadow_inputs import iter_shadow_files
from .shadow_quality import SOURCE_QUALITY_CHOICES, source_quality_allowed, source_quality_summary


EXCLUSION_REASONS = ("missed_direct_kill", "missed_single_card_search")
DEFAULT_EXAMPLE_LIMIT = 20


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit excluded combat_search_labels rows.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="Shadow dirs or combat_search_labels.jsonl files.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON.")
    parser.add_argument(
        "--source-quality",
        choices=SOURCE_QUALITY_CHOICES,
        default="all",
        help="Filter rows before audit. Default 'all' audits every label-quality exclusion.",
    )
    parser.add_argument("--example-limit", type=int, default=DEFAULT_EXAMPLE_LIMIT)
    args = parser.parse_args(argv)

    report = build_audit_report(
        args.shadow_rows,
        source_quality=args.source_quality,
        example_limit=args.example_limit,
    )
    text = json.dumps(report, ensure_ascii=True, indent=None if args.compact else 2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote combat label audit: {args.output}")
    else:
        print(text, end="")
    print(report["status_line"])
    return 0


def build_audit_report(
    paths: Iterable[Path],
    *,
    source_quality: str = "all",
    example_limit: int = DEFAULT_EXAMPLE_LIMIT,
) -> dict[str, Any]:
    input_paths = [Path(path) for path in paths]
    resolved_files = list(iter_shadow_files(input_paths, "combat_search"))
    rows = 0
    filtered_rows = 0
    accepted_rows = 0
    excluded_rows = 0
    reasons: dict[str, int] = {}
    source_quality_counts: dict[str, int] = {}
    example_candidates: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    read_errors: list[dict[str, Any]] = []

    for path in resolved_files:
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    rows += 1
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        read_errors.append({"path": str(path), "line": line_number, "error": str(exc)})
                        continue
                    if not isinstance(row, dict):
                        _count(reasons, "non_object_row")
                        continue
                    _add_counts(source_quality_counts, source_quality_summary([row]))
                    if not source_quality_allowed(row, source_quality):
                        filtered_rows += 1
                        continue
                    row_reasons = row_exclusion_reasons(row)
                    if row_reasons:
                        excluded_rows += 1
                        for reason in row_reasons:
                            _count(reasons, reason)
                        example_candidates.append(
                            (
                                label_example_sort_key(row, rows),
                                compact_label_row(row, path=path, line_number=line_number, reasons=row_reasons),
                            )
                        )
                    else:
                        accepted_rows += 1
        except OSError as exc:
            read_errors.append({"path": str(path), "error": str(exc)})

    examples = [example for _, example in sorted(example_candidates, key=lambda item: item[0])[: max(0, example_limit)]]
    warnings: list[str] = []
    if not resolved_files:
        warnings.append("no_resolved_combat_search_label_files")
    if read_errors:
        warnings.append("read_errors")
    report = {
        "version": 1,
        "inputs": [str(path) for path in input_paths],
        "resolved_files": [str(path) for path in resolved_files],
        "resolved_file_count": len(resolved_files),
        "warnings": warnings,
        "read_errors": read_errors,
        "source_quality_policy": source_quality,
        "source_quality": dict(sorted(source_quality_counts.items())),
        "rows": rows,
        "filtered_rows": filtered_rows,
        "accepted_rows": accepted_rows,
        "excluded_rows": excluded_rows,
        "exclusion_reasons": dict(sorted(reasons.items())),
        "examples": examples,
        "example_count": len(examples),
        "example_limit": max(0, example_limit),
    }
    report["status_line"] = status_line(report)
    return report


def compact_audit_report(raw: Any, *, example_limit: int = 5) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    if raw.get("status_line"):
        result["status_line"] = str(raw["status_line"])
    for field in ("resolved_file_count", "rows", "filtered_rows", "accepted_rows", "excluded_rows"):
        result[field] = int(raw.get(field) or 0)
    reasons = raw.get("exclusion_reasons") if isinstance(raw.get("exclusion_reasons"), dict) else {}
    if reasons:
        result["exclusion_reasons"] = {str(key): int(value or 0) for key, value in sorted(reasons.items())}
    source_quality = raw.get("source_quality") if isinstance(raw.get("source_quality"), dict) else {}
    if source_quality:
        result["source_quality"] = {str(key): int(value or 0) for key, value in sorted(source_quality.items())}
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
        "combat_label_audit",
        f"files={int(report.get('resolved_file_count') or 0)}",
        f"rows={int(report.get('rows') or 0)}",
        f"excluded={int(report.get('excluded_rows') or 0)}",
    ]
    reasons = report.get("exclusion_reasons") if isinstance(report.get("exclusion_reasons"), dict) else {}
    for reason, count in sorted(reasons.items(), key=lambda item: (-int(item[1] or 0), str(item[0]))):
        if int(count or 0) > 0:
            parts.append(f"{reason}={int(count)}")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    if warnings:
        parts.append("warnings=" + ",".join(str(warning) for warning in warnings))
    return " ".join(parts)


def row_exclusion_reasons(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if row.get("label_missed_direct_kill"):
        reasons.append("missed_direct_kill")
    if row.get("label_missed_single_card_search"):
        reasons.append("missed_single_card_search")
    return reasons


def compact_excluded_label_examples(rows: Iterable[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    candidates: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for order, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        reasons = row_exclusion_reasons(row)
        if not reasons:
            continue
        candidates.append((label_example_sort_key(row, order), compact_label_row(row, reasons=reasons)))
    return [example for _, example in sorted(candidates, key=lambda item: item[0])[: max(0, limit)]]


def prioritize_label_examples(examples: Iterable[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    candidates: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    for order, example in enumerate(examples, start=1):
        if isinstance(example, dict):
            candidates.append((label_example_sort_key(example, order), example))
    return [example for _, example in sorted(candidates, key=lambda item: item[0])[: max(0, limit)]]


def compact_label_row(
    row: dict[str, Any],
    *,
    path: Path | None = None,
    line_number: int | None = None,
    reasons: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {"reasons": reasons}
    if path is not None:
        result["path"] = str(path)
    if line_number is not None:
        result["line"] = line_number
    for key in (
        "source_log",
        "source_validation_grade",
        "source_category",
        "source_reason",
        "floor",
        "act",
        "turn",
        "step",
        "current_hp",
        "max_hp",
        "current_energy",
        "incoming",
        "initial_loss",
        "projected_loss",
        "loss_delta",
        "attacks_removed",
        "kills",
        "avoided_lethal",
        "search_type",
        "label_first_card_key",
        "label_sequence_card_keys",
        "label_target_index",
        "direct_kill_available",
        "direct_kill_card_keys",
        "direct_kill_card_indices",
        "direct_kill_enemy_id",
        "direct_kill_enemy_hp",
        "enemy_ids",
        "enemy_hps",
        "enemy_intents",
        "hand_ids",
    ):
        if row.get(key) not in (None, "", [], {}):
            result[key] = row[key]
    return result


def label_example_sort_key(row: dict[str, Any], order: int) -> tuple[int, int, int, int]:
    boss_priority = 0 if _is_act1_boss_label_context(row) else 1
    floor = _safe_int(row.get("floor"))
    incoming = _safe_int(row.get("incoming"))
    return (boss_priority, -floor, -incoming, order)


def _is_act1_boss_label_context(row: dict[str, Any]) -> bool:
    enemy_ids = row.get("enemy_ids") if isinstance(row.get("enemy_ids"), list) else []
    if any(_is_act1_boss_name(enemy_id) for enemy_id in enemy_ids):
        return True
    if _is_act1_boss_name(row.get("direct_kill_enemy_id")):
        return True
    return _safe_int(row.get("act")) == 1 and _safe_int(row.get("floor")) == 16


def _is_act1_boss_name(value: Any) -> bool:
    text = "".join(ch for ch in str(value or "").lower() if ch.isalnum())
    return text in {"hexaghost", "slimeboss", "theguardian", "guardian"} or any(
        boss in text for boss in ("hexaghost", "slimeboss", "theguardian")
    )


def _safe_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return int(value)
        return int(value)
    except (TypeError, ValueError):
        return 0


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _add_counts(dest: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        count = int(value or 0)
        if count > 0:
            dest[str(key)] = dest.get(str(key), 0) + count


if __name__ == "__main__":
    raise SystemExit(main())
