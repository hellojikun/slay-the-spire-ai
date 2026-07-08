"""Source-quality gates for shadow-model training rows."""

from __future__ import annotations

import argparse
from typing import Any, Iterable


DEFAULT_SOURCE_QUALITY = "pristine"
SOURCE_QUALITY_CHOICES = ("pristine", "usable", "diagnostic", "external", "legacy", "all")
LEGACY_UNGRADED = "legacy_ungraded"
EXTERNAL_PRIOR = "external_prior"


def add_source_quality_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-quality",
        choices=SOURCE_QUALITY_CHOICES,
        default=DEFAULT_SOURCE_QUALITY,
        help=(
            "Shadow-row source-quality gate. Default 'pristine' only trains from validation_grade=pristine rows; "
            "'usable' also admits usable_with_recoveries; 'external' admits only isolated external_prior rows; "
            "'legacy' admits pristine plus ungraded legacy rows."
        ),
    )


def source_validation_grade(row: dict[str, Any]) -> str:
    grade = row.get("source_validation_grade")
    if grade is None or str(grade).strip() == "":
        return LEGACY_UNGRADED
    return str(grade)


def source_quality_allowed(row: dict[str, Any], source_quality: str = DEFAULT_SOURCE_QUALITY) -> bool:
    grade = source_validation_grade(row)
    if source_quality == "pristine":
        return grade == "pristine"
    if source_quality == "usable":
        return grade in {"pristine", "usable_with_recoveries"}
    if source_quality == "diagnostic":
        return grade in {"pristine", "usable_with_recoveries", "diagnostic"}
    if source_quality == "external":
        return grade == EXTERNAL_PRIOR
    if source_quality == "legacy":
        return grade in {"pristine", LEGACY_UNGRADED}
    if source_quality == "all":
        return True
    raise ValueError(f"Unknown source-quality mode: {source_quality}")


def source_quality_summary(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        grade = source_validation_grade(row)
        counts[grade] = counts.get(grade, 0) + 1
    return dict(sorted(counts.items()))
