"""Input provenance helpers for shadow-model row files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


SHADOW_ROW_FILES = {
    "route_risk": "route_risk.jsonl",
    "potion_tempo": "potion_tempo.jsonl",
    "pre_boss_deck_quality": "pre_boss_deck_quality.jsonl",
    "combat_search": "combat_search_labels.jsonl",
}


def iter_shadow_files(paths: Iterable[Path], category: str) -> Iterable[Path]:
    row_file = SHADOW_ROW_FILES[category]
    for path in paths:
        if path.is_dir():
            candidate = path / row_file
            if candidate.exists():
                yield candidate
            else:
                yield from sorted(path.glob("*.jsonl"))
        elif path.exists():
            yield path


def resolve_shadow_training_source(
    paths: Iterable[Path],
    *,
    categories: Iterable[str] = SHADOW_ROW_FILES.keys(),
) -> dict[str, Any]:
    input_paths = list(paths)
    missing_inputs = [str(path) for path in input_paths if not path.exists()]
    category_sources: dict[str, Any] = {}
    all_files: list[str] = []

    for category in categories:
        files = list(iter_shadow_files(input_paths, category))
        resolved_files = [str(path) for path in files]
        category_sources[category] = {
            "row_file": SHADOW_ROW_FILES[category],
            "resolved_files": resolved_files,
            "resolved_file_count": len(resolved_files),
            "warnings": [] if resolved_files else [f"no_resolved_{category}_shadow_files"],
        }
        all_files.extend(resolved_files)

    resolved_unique = list(dict.fromkeys(all_files))
    warnings: list[str] = []
    if missing_inputs:
        warnings.append("missing_shadow_inputs")
    if not resolved_unique:
        warnings.append("no_resolved_shadow_files")

    return {
        "mode": "shadow_rows",
        "inputs": [str(path) for path in input_paths],
        "missing_inputs": missing_inputs,
        "resolved_files": resolved_unique,
        "resolved_file_count": len(resolved_unique),
        "categories": category_sources,
        "warnings": warnings,
    }


def category_training_source(
    training_source: dict[str, Any],
    category: str,
    *,
    source_quality: str,
) -> dict[str, Any]:
    category_source = training_source.get("categories", {}).get(category, {})
    return {
        "mode": training_source.get("mode"),
        "category": category,
        "source_quality_policy": source_quality,
        "inputs": training_source.get("inputs", []),
        "missing_inputs": training_source.get("missing_inputs", []),
        "row_file": category_source.get("row_file"),
        "resolved_files": category_source.get("resolved_files", []),
        "resolved_file_count": category_source.get("resolved_file_count", 0),
        "warnings": list(training_source.get("warnings", [])) + list(category_source.get("warnings", [])),
    }
