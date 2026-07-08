"""Audit feature coverage in offline shadow JSONL rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .shadow_advice import SHADOW_FILES
from .shadow_quality import source_validation_grade


DEFAULT_PREFIXES = ("deck_", "potion_", "relic_", "enemy_", "boss_")
EXPECTED_CATEGORY_PREFIXES = {
    "route_risk": ("deck_",),
    "potion_tempo": ("potion_", "enemy_"),
    "pre_boss_deck_quality": ("deck_", "boss_"),
    "combat_search": ("deck_", "enemy_"),
}
UNKNOWN_STATIC_FEATURES = (
    "deck_unknown_cards",
    "potion_unknown_count",
    "relic_unknown_count",
    "enemy_unknown_count",
    "boss_unknown_count",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit static feature coverage in shadow JSONL rows.")
    parser.add_argument("shadow_rows", nargs="+", type=Path, help="Shadow directories or JSONL files to audit.")
    parser.add_argument(
        "--prefix",
        action="append",
        dest="prefixes",
        help="Feature prefix to audit. Repeat for multiple prefixes. Defaults to static knowledge prefixes.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON.")
    args = parser.parse_args(argv)

    summary = audit_shadow_features(args.shadow_rows, prefixes=args.prefixes or DEFAULT_PREFIXES)
    indent = None if args.compact else 2
    text = json.dumps(summary, ensure_ascii=False, indent=indent, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote shadow feature audit: {args.output}")
    else:
        print(text, end="")
    return 0


def audit_shadow_features(
    paths: Iterable[Path],
    *,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
) -> dict[str, Any]:
    path_list = [Path(path) for path in paths]
    prefix_list = tuple(prefixes)
    summary = _new_summary([str(path) for path in path_list], prefix_list)
    seen_files: set[Path] = set()
    for input_path in path_list:
        if not input_path.exists():
            summary["missing_inputs"].append(str(input_path))
            continue
        for category, file_path in _iter_shadow_files(input_path):
            resolved = file_path.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            summary["total_files"] += 1
            category_summary = _category_summary(summary, category, prefix_list)
            category_summary["files"] += 1
            category_summary["paths"].append(str(file_path))
            for row in _read_jsonl(file_path):
                category_summary["rows"] += 1
                summary["total_rows"] += 1
                _count(category_summary["source_quality"], source_validation_grade(row))
                _audit_row_features(category_summary, row, prefix_list)

    for category_summary in summary["categories"].values():
        _finalize_category(category_summary)
    summary["missing_inputs"] = sorted(summary["missing_inputs"])
    summary["categories"] = dict(sorted(summary["categories"].items()))
    summary["feature_focus"] = _feature_focus_summary(summary, prefix_list)
    return summary


def audit_shadow_examples(
    examples: dict[str, list[dict[str, Any]]],
    *,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
) -> dict[str, Any]:
    prefix_list = tuple(prefixes)
    summary = _new_summary([], prefix_list)
    summary["source"] = "memory"
    for raw_category, rows in sorted(examples.items()):
        category = _category_from_example_key(raw_category)
        category_summary = _category_summary(summary, category, prefix_list)
        for row in rows:
            if not isinstance(row, dict):
                continue
            category_summary["rows"] += 1
            summary["total_rows"] += 1
            _count(category_summary["source_quality"], source_validation_grade(row))
            _audit_row_features(category_summary, row, prefix_list)

    for category_summary in summary["categories"].values():
        _finalize_category(category_summary)
    summary["categories"] = dict(sorted(summary["categories"].items()))
    summary["feature_focus"] = _feature_focus_summary(summary, prefix_list)
    return summary


def audit_adjacent_shadow_features(
    manifest_path: Path,
    *,
    prefixes: Iterable[str] = DEFAULT_PREFIXES,
) -> tuple[dict[str, Any], str | None]:
    saw_shadow_dir = False
    for shadow_dir in candidate_shadow_dirs_for_manifest(manifest_path):
        if not shadow_dir.exists():
            continue
        saw_shadow_dir = True
        coverage = audit_shadow_features([shadow_dir], prefixes=prefixes)
        if int(coverage.get("total_rows") or 0) > 0:
            coverage["source"] = "shadow_dir_fallback"
            return coverage, "shadow_dir_fallback"
        child_dirs = sorted(path for path in shadow_dir.iterdir() if path.is_dir())
        if child_dirs:
            coverage = audit_shadow_features(child_dirs, prefixes=prefixes)
            if int(coverage.get("total_rows") or 0) > 0:
                coverage["source"] = "shadow_dir_fallback"
                return coverage, "shadow_dir_fallback"
    return {}, "shadow_dir_empty" if saw_shadow_dir else None


def candidate_shadow_dirs_for_manifest(manifest_path: Path) -> list[Path]:
    stem = manifest_path.stem
    base_candidates: list[Path] = []
    if stem.startswith("training_manifest_"):
        base_candidates.append(manifest_path.with_name("shadow_" + stem[len("training_manifest_") :]))
    base_candidates.append(manifest_path.with_name(stem.replace("training_manifest", "shadow", 1)))
    candidates: list[Path] = []
    for candidate in base_candidates:
        candidates.append(candidate)
        if candidate.parent.exists():
            candidates.extend(sorted(path for path in candidate.parent.glob(candidate.name + "*") if path.is_dir()))
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(candidate)
    return deduped


def _new_summary(shadow_inputs: list[str], prefixes: tuple[str, ...]) -> dict[str, Any]:
    return {
        "version": 1,
        "shadow_inputs": shadow_inputs,
        "prefixes": list(prefixes),
        "missing_inputs": [],
        "total_files": 0,
        "total_rows": 0,
        "categories": {},
    }


def _category_summary(summary: dict[str, Any], category: str, prefixes: tuple[str, ...]) -> dict[str, Any]:
    categories = summary["categories"]
    if category not in categories:
        categories[category] = {
            "files": 0,
            "paths": [],
            "rows": 0,
            "source_quality": {},
            "feature_prefixes": {
                prefix: _prefix_summary()
                for prefix in prefixes
            },
            "unknown_static_features": {},
        }
    return categories[category]


def _prefix_summary() -> dict[str, Any]:
    return {
        "field_count": 0,
        "rows_with_any": 0,
        "rows_with_nonzero": 0,
        "present_values": 0,
        "nonzero_values": 0,
        "row_coverage": 0.0,
        "nonzero_row_rate": 0.0,
        "fields": {},
    }


def _field_summary() -> dict[str, Any]:
    return {
        "present": 0,
        "numeric": 0,
        "nonzero": 0,
        "coverage": 0.0,
        "nonzero_rate": 0.0,
    }


def _unknown_static_summary() -> dict[str, Any]:
    return {
        "present": 0,
        "nonzero": 0,
        "total": 0,
        "max": 0,
        "coverage": 0.0,
        "nonzero_rate": 0.0,
    }


def _audit_row_features(category_summary: dict[str, Any], row: dict[str, Any], prefixes: tuple[str, ...]) -> None:
    for prefix in prefixes:
        fields = {key: value for key, value in row.items() if key.startswith(prefix)}
        if not fields:
            continue
        prefix_summary = category_summary["feature_prefixes"][prefix]
        prefix_summary["rows_with_any"] += 1
        row_has_nonzero = False
        for key, value in fields.items():
            field_summary = prefix_summary["fields"].setdefault(key, _field_summary())
            field_summary["present"] += 1
            prefix_summary["present_values"] += 1
            if _is_numeric_like(value):
                field_summary["numeric"] += 1
            if _is_nonzero(value):
                field_summary["nonzero"] += 1
                prefix_summary["nonzero_values"] += 1
                row_has_nonzero = True
        if row_has_nonzero:
            prefix_summary["rows_with_nonzero"] += 1
    unknown_features = category_summary["unknown_static_features"]
    for key in UNKNOWN_STATIC_FEATURES:
        if key not in row:
            continue
        value = _numeric_value(row.get(key))
        field_summary = unknown_features.setdefault(key, _unknown_static_summary())
        field_summary["present"] += 1
        field_summary["total"] += value
        field_summary["max"] = max(field_summary["max"], value)
        if value > 0:
            field_summary["nonzero"] += 1


def _finalize_category(category_summary: dict[str, Any]) -> None:
    rows = int(category_summary["rows"])
    category_summary["paths"] = sorted(category_summary["paths"])
    category_summary["source_quality"] = dict(sorted(category_summary["source_quality"].items()))
    for prefix_summary in category_summary["feature_prefixes"].values():
        prefix_summary["field_count"] = len(prefix_summary["fields"])
        prefix_summary["row_coverage"] = _ratio(prefix_summary["rows_with_any"], rows)
        prefix_summary["nonzero_row_rate"] = _ratio(prefix_summary["rows_with_nonzero"], rows)
        for field_summary in prefix_summary["fields"].values():
            field_summary["coverage"] = _ratio(field_summary["present"], rows)
            field_summary["nonzero_rate"] = _ratio(field_summary["nonzero"], field_summary["present"])
        prefix_summary["fields"] = dict(sorted(prefix_summary["fields"].items()))
    for field_summary in category_summary["unknown_static_features"].values():
        field_summary["coverage"] = _ratio(field_summary["present"], rows)
        field_summary["nonzero_rate"] = _ratio(field_summary["nonzero"], field_summary["present"])
    category_summary["unknown_static_features"] = dict(sorted(category_summary["unknown_static_features"].items()))


def _feature_focus_summary(summary: dict[str, Any], prefixes: tuple[str, ...]) -> dict[str, Any]:
    audited_prefixes = set(prefixes)
    focus: dict[str, Any] = {
        "expected_prefixes": {
            category: [prefix for prefix in expected if prefix in audited_prefixes]
            for category, expected in sorted(EXPECTED_CATEGORY_PREFIXES.items())
        },
        "categories_with_issues": 0,
        "missing_prefix_count": 0,
        "zero_prefix_count": 0,
        "unknown_static_total": 0,
        "unknown_static_features": {},
        "categories": {},
    }
    categories = summary.get("categories") if isinstance(summary.get("categories"), dict) else {}
    for category, category_summary in sorted(categories.items()):
        rows = int(category_summary.get("rows") or 0) if isinstance(category_summary, dict) else 0
        expected_prefixes = focus["expected_prefixes"].get(category, [])
        if rows <= 0 or not expected_prefixes:
            continue
        prefix_summaries = (
            category_summary.get("feature_prefixes")
            if isinstance(category_summary.get("feature_prefixes"), dict)
            else {}
        )
        missing_prefixes: list[str] = []
        zero_prefixes: list[str] = []
        nonzero_prefixes: dict[str, int] = {}
        for prefix in expected_prefixes:
            prefix_summary = prefix_summaries.get(prefix) if isinstance(prefix_summaries.get(prefix), dict) else {}
            field_count = int(prefix_summary.get("field_count") or 0)
            rows_with_nonzero = int(prefix_summary.get("rows_with_nonzero") or 0)
            if field_count <= 0:
                missing_prefixes.append(prefix)
            elif rows_with_nonzero <= 0:
                zero_prefixes.append(prefix)
            else:
                nonzero_prefixes[prefix] = rows_with_nonzero
        unknown_static_features = _unknown_feature_totals(category_summary)
        unknown_static_total = sum(unknown_static_features.values())
        issues = []
        if missing_prefixes:
            issues.append("missing_prefixes")
        if zero_prefixes:
            issues.append("zero_prefixes")
        if unknown_static_total > 0:
            issues.append("unknown_static_features")
        if issues:
            focus["categories_with_issues"] += 1
        focus["missing_prefix_count"] += len(missing_prefixes)
        focus["zero_prefix_count"] += len(zero_prefixes)
        focus["unknown_static_total"] += unknown_static_total
        for field, total in unknown_static_features.items():
            key = f"{category}:{field}"
            focus["unknown_static_features"][key] = total
        focus["categories"][category] = {
            "rows": rows,
            "expected_prefixes": expected_prefixes,
            "missing_prefixes": missing_prefixes,
            "zero_prefixes": zero_prefixes,
            "nonzero_prefixes": nonzero_prefixes,
            "unknown_static_features": unknown_static_features,
            "attention_required": bool(issues),
            "issues": issues,
        }
    focus["unknown_static_features"] = dict(sorted(focus["unknown_static_features"].items()))
    return focus


def _unknown_feature_totals(category_summary: dict[str, Any]) -> dict[str, int]:
    raw_unknown = (
        category_summary.get("unknown_static_features")
        if isinstance(category_summary.get("unknown_static_features"), dict)
        else {}
    )
    totals: dict[str, int] = {}
    for field, stats in raw_unknown.items():
        total = 0
        if isinstance(stats, dict):
            total = _numeric_value(stats.get("total"))
        else:
            total = _numeric_value(stats)
        if total > 0:
            totals[str(field)] = total
    return dict(sorted(totals.items()))


def _iter_shadow_files(path: Path) -> Iterable[tuple[str, Path]]:
    if path.is_dir():
        for category, filename in SHADOW_FILES.items():
            candidate = path / filename
            if candidate.exists():
                yield category, candidate
        return
    if path.exists() and path.suffix.lower() == ".jsonl":
        yield _category_from_file(path), path


def _category_from_file(path: Path) -> str:
    for category, filename in SHADOW_FILES.items():
        if path.name == filename or path.stem == Path(filename).stem:
            return category
    return path.stem


def _category_from_example_key(key: str) -> str:
    if key == "combat_search_labels":
        return "combat_search"
    return key


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                yield row


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


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


def _is_nonzero(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    if value is None or isinstance(value, (list, dict)):
        return False
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return False


def _numeric_value(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if value is None or isinstance(value, (list, dict)):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


if __name__ == "__main__":
    raise SystemExit(main())
