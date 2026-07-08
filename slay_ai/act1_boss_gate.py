"""Aggregate Act 1 boss validation evidence from training manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .run_diagnosis import summarize_shadow_feature_coverage
from .shadow_feature_audit import audit_adjacent_shadow_features
from .training_manifest import CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED


DEFAULT_MIN_REACHED = 3
DEFAULT_MIN_CLEARED = 2
DEFAULT_MIN_PRISTINE_CLEARED = 2
UNKNOWN_ATTRIBUTIONS = {"", "none", "unknown", "unknown_clean_failure"}
PROGRESS_METRIC_LABELS = {
    "act1_boss_reached": ("Act 1 boss reach", "Act 1 boss reaches"),
    "act1_boss_cleared": ("Act 1 boss clear", "Act 1 boss clears"),
    "pristine_act1_boss_cleared": ("pristine Act 1 boss clear", "pristine Act 1 boss clears"),
}
REQUIRED_MANIFEST_SUMMARY_KEYS = (
    "input_count",
    "resolved_log_count",
    "warnings",
    "total_logs",
    CLEAN_TRAINABLE,
    DIAGNOSTIC_EXCLUDED,
    INFRA_BLOCKED,
    "shadow_examples",
    "shadow_feature_coverage",
    "shadow_label_quality",
    "classification_reasons",
    "failure_attributions",
    "failure_evidence",
    "action_recovery",
    "terminal_recovery",
    "validation",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize A0 Act 1 boss validation evidence from manifests.")
    parser.add_argument("manifests", nargs="+", type=Path, help="training_manifest JSON files.")
    parser.add_argument("--character", default="IRONCLAD")
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--min-reached", type=int, default=DEFAULT_MIN_REACHED)
    parser.add_argument("--min-cleared", type=int, default=DEFAULT_MIN_CLEARED)
    parser.add_argument("--min-pristine-cleared", type=int, default=DEFAULT_MIN_PRISTINE_CLEARED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-miss", action="store_true", help="Exit nonzero when the gate is not satisfied.")
    args = parser.parse_args(argv)

    report = build_report(
        args.manifests,
        character=args.character,
        ascension=args.ascension,
        min_reached=args.min_reached,
        min_cleared=args.min_cleared,
        min_pristine_cleared=args.min_pristine_cleared,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote Act 1 boss gate report: {args.output}")
    print(status_line(report))
    return 1 if args.fail_on_miss and not report["gate"]["passed"] else 0


def build_report(
    manifests: Iterable[Path],
    *,
    character: str = "IRONCLAD",
    ascension: int = 0,
    min_reached: int = DEFAULT_MIN_REACHED,
    min_cleared: int = DEFAULT_MIN_CLEARED,
    min_pristine_cleared: int = DEFAULT_MIN_PRISTINE_CLEARED,
) -> dict[str, Any]:
    manifest_paths = [Path(path) for path in manifests]
    runs = [
        _run_summary(manifest_path, category, item)
        for manifest_path in manifest_paths
        for category, item in _iter_manifest_items(manifest_path)
        if _matches_target(item, character=character, ascension=ascension)
    ]
    data_quality = _data_quality_summary(manifest_paths)
    execution_recovery = _execution_recovery_summary(manifest_paths)
    failure_evidence = _failure_evidence_summary(manifest_paths)
    counts = _counts(runs)
    counts.update(
        {
            "shadow_feature_rows": data_quality["shadow_feature_rows"],
            "shadow_feature_gap_manifests": data_quality["feature_gap_manifests"],
            "shadow_feature_zero_manifests": data_quality["feature_zero_manifests"],
            "shadow_feature_missing_manifests": data_quality["missing_coverage_manifests"],
            "shadow_unknown_static_manifests": data_quality["unknown_static_manifests"],
            "shadow_unknown_static_total": data_quality["unknown_static_total"],
            "shadow_label_rows": data_quality["shadow_label_rows"],
            "shadow_label_excluded": data_quality["shadow_label_excluded"],
            "shadow_label_exclusion_manifests": data_quality["label_exclusion_manifests"],
            "action_recovery_total": _safe_int(
                (execution_recovery.get("action_recovery") or {}).get("total")
            ),
            "action_recovery_unrecovered": _safe_int(
                (execution_recovery.get("action_recovery") or {}).get("unrecovered")
            ),
            "terminal_recovery_synthetic_terminals": _safe_int(
                (execution_recovery.get("terminal_recovery") or {}).get("synthetic_terminals")
            ),
            "terminal_recovery_failed": _safe_int(
                (execution_recovery.get("terminal_recovery") or {}).get("failed")
            ),
            "failure_evidence_runs": _safe_int(failure_evidence.get("runs_with_evidence")),
            "failure_evidence_screen_stalls": _safe_int(
                ((failure_evidence.get("by_type") or {}).get("screen_stall"))
            ),
            "failure_evidence_mcp_reads": _safe_int(
                ((failure_evidence.get("by_type") or {}).get("mcp_read"))
            ),
            "failure_evidence_action_errors": _safe_int(
                ((failure_evidence.get("by_type") or {}).get("action_error"))
            ),
            "manifest_schema_missing_manifests": data_quality["schema_missing_manifests"],
            "manifest_schema_missing_key_total": data_quality["schema_missing_key_total"],
        }
    )
    attribution_ok = counts["unknown_attribution_runs"] == 0
    reach_ok = counts["act1_boss_reached"] >= min_reached
    clear_ok = counts["act1_boss_cleared"] >= min_cleared
    pristine_ok = counts["pristine_act1_boss_cleared"] >= min_pristine_cleared
    no_infra = counts["infra_blocked"] == 0
    blocking_reasons = _blocking_reasons(
        runs,
        reach_ok=reach_ok,
        clear_ok=clear_ok,
        pristine_ok=pristine_ok,
        attribution_ok=attribution_ok,
        no_infra=no_infra,
    )
    deficits = {
        "runs": 0 if runs else 1,
        "act1_boss_reached": max(0, min_reached - counts["act1_boss_reached"]),
        "act1_boss_cleared": max(0, min_cleared - counts["act1_boss_cleared"]),
        "pristine_act1_boss_cleared": max(
            0, min_pristine_cleared - counts["pristine_act1_boss_cleared"]
        ),
        "unknown_attribution_runs": counts["unknown_attribution_runs"],
        "infra_blocked": counts["infra_blocked"],
    }
    progress = _gate_progress(
        counts,
        requirements={
            "act1_boss_reached": min_reached,
            "act1_boss_cleared": min_cleared,
            "pristine_act1_boss_cleared": min_pristine_cleared,
        },
        deficits=deficits,
        checks={
            "act1_boss_reached": reach_ok,
            "act1_boss_cleared": clear_ok,
            "pristine_act1_boss_cleared": pristine_ok,
        },
    )
    remaining_progress = _remaining_progress(progress)
    primary_remaining_progress = remaining_progress[0] if remaining_progress else None
    next_action = _next_action(counts, reach_ok, clear_ok, pristine_ok, attribution_ok, no_infra)
    next_probe_goal = _next_probe_goal(
        next_action,
        primary_remaining_progress,
        blocking_reasons[0] if blocking_reasons else None,
    )
    gate = {
        "passed": bool(runs) and reach_ok and clear_ok and pristine_ok and attribution_ok and no_infra,
        "target": {"character": character, "ascension": ascension},
        "requirements": {
            "min_reached": min_reached,
            "min_cleared": min_cleared,
            "min_pristine_cleared": min_pristine_cleared,
            "no_unknown_attribution": True,
            "no_infra_blocked": True,
        },
        "checks": {
            "has_runs": bool(runs),
            "reach_ok": reach_ok,
            "clear_ok": clear_ok,
            "pristine_clear_ok": pristine_ok,
            "attribution_ok": attribution_ok,
            "no_infra_blocked": no_infra,
        },
        "blocking_reasons": blocking_reasons,
        "primary_blocking_reason": blocking_reasons[0] if blocking_reasons else None,
        "deficits": deficits,
        "progress": progress,
        "remaining_progress": remaining_progress,
        "primary_remaining_progress": primary_remaining_progress,
        "focus": {
            "top_failure_attribution": _top_focus_count(
                counts.get("failure_attributions") or {},
                "attribution",
            ),
            "top_non_pristine_clear_blocker": _top_focus_count(
                counts.get("non_pristine_clear_blockers") or {},
                "blocker",
            ),
            "top_uncleared_boss": _top_uncleared_boss(
                counts.get("act1_boss_uncleared_failure_attributions_by_enemy") or {}
            ),
            "top_non_pristine_clear_boss": _top_non_pristine_clear_boss(
                counts.get("act1_boss_non_pristine_cleared_by_enemy") or {},
                counts.get("act1_boss_non_pristine_clear_blockers_by_enemy") or {}
            ),
        },
        "next_action": next_action,
        "next_probe_goal": next_probe_goal,
        "latest_run_acceptance": _latest_run_acceptance(runs[-1] if runs else None, next_probe_goal),
    }
    return {
        "version": 1,
        "gate": gate,
        "counts": counts,
        "data_quality": data_quality,
        "execution_recovery": execution_recovery,
        "failure_evidence": failure_evidence,
        "runs": runs,
    }


def status_line(report: dict[str, Any]) -> str:
    gate = report.get("gate") or {}
    target = gate.get("target") or {}
    counts = report.get("counts") or {}
    requirements = gate.get("requirements") or {}
    character = target.get("character") or "?"
    ascension = target.get("ascension")
    state = "PASS" if gate.get("passed") else "NEEDS_WORK"
    top = _top_count(counts.get("failure_attributions") or {})
    top_text = f"; top_failure={top[0]}:{top[1]}" if top else ""
    blocker = _top_count(counts.get("non_pristine_clear_blockers") or {})
    blocker_text = f"; top_non_pristine={blocker[0]}:{blocker[1]}" if blocker else ""
    uncleared_boss_text = _top_uncleared_boss_text(
        counts.get("act1_boss_uncleared_failure_attributions_by_enemy") or {}
    )
    non_pristine_boss_text = _top_non_pristine_clear_boss_text(
        counts.get("act1_boss_non_pristine_cleared_by_enemy") or {},
        counts.get("act1_boss_non_pristine_clear_blockers_by_enemy") or {},
    )
    deficits = gate.get("deficits") if isinstance(gate.get("deficits"), dict) else {}
    deficit_text = _deficit_text(deficits)
    latest_text = _latest_acceptance_text(gate)
    feature_text = _feature_quality_text(report.get("data_quality") or {})
    schema_text = _schema_quality_text(report.get("data_quality") or {})
    label_text = _label_quality_text(report.get("data_quality") or {})
    execution_text = _execution_recovery_text(report.get("execution_recovery") or {})
    evidence_text = _failure_evidence_text(report.get("failure_evidence") or {})
    prefix_text = ""
    if counts.get("explicit_prefix_evidence_runs") or counts.get("legacy_pristine_act1_boss_cleared"):
        prefix_text = (
            f", prefix_pristine={counts.get('prefix_pristine_act1_boss_cleared', 0)}, "
            f"legacy_pristine={counts.get('legacy_pristine_act1_boss_cleared', 0)}"
        )
    return (
        f"act1_boss_gate {state}: {character} A{ascension} "
        f"runs={counts.get('total_runs', 0)}, "
        f"reached={counts.get('act1_boss_reached', 0)}/{requirements.get('min_reached')}, "
        f"cleared={counts.get('act1_boss_cleared', 0)}/{requirements.get('min_cleared')}, "
        f"pristine_cleared={counts.get('pristine_act1_boss_cleared', 0)}/"
        f"{requirements.get('min_pristine_cleared')}{prefix_text}, "
        f"infra={counts.get('infra_blocked', 0)}, "
        f"unknown_attr={counts.get('unknown_attribution_runs', 0)}"
        f"{top_text}{blocker_text}{uncleared_boss_text}{non_pristine_boss_text}{schema_text}{feature_text}{label_text}{evidence_text}{execution_text}{deficit_text}{latest_text} -> {gate.get('next_action')}"
    )


def _iter_manifest_items(path: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}
    for category in (CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED):
        for item in categories.get(category) or []:
            if isinstance(item, dict):
                yield category, item


def _data_quality_summary(manifests: list[Path]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "manifests": [],
        "total_manifests": 0,
        "coverage_manifests": 0,
        "missing_coverage_manifests": 0,
        "shadow_feature_rows": 0,
        "feature_gap_manifests": 0,
        "feature_zero_manifests": 0,
        "feature_gaps": {},
        "feature_zero": {},
        "feature_issue_categories": {},
        "unknown_static_manifests": 0,
        "unknown_static_total": 0,
        "unknown_static_features": {},
        "shadow_label_rows": 0,
        "shadow_label_trainable": 0,
        "shadow_label_excluded": 0,
        "label_exclusion_manifests": 0,
        "label_exclusion_reasons": {},
        "direct_kill_available_labels": 0,
        "missed_single_card_search_labels": 0,
        "schema_complete_manifests": 0,
        "schema_missing_manifests": 0,
        "schema_missing_key_total": 0,
        "schema_missing_summary_keys": {},
    }
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
        schema = _manifest_schema_quality(manifest_summary)
        coverage = manifest_summary.get("shadow_feature_coverage") or {}
        coverage_source = "manifest_summary" if coverage else None
        if not coverage:
            coverage, coverage_source = audit_adjacent_shadow_features(path)
        gaps = summarize_shadow_feature_coverage(coverage)
        label_quality = _combat_label_quality(manifest_summary)
        entry = {
            "manifest_path": str(path),
            "available": bool(gaps.get("available")),
            "coverage_source": coverage_source,
            "total_rows": int(gaps.get("total_rows") or 0),
            "gap_count": int(gaps.get("gap_count") or 0),
            "zero_count": int(gaps.get("zero_count") or 0),
            "unknown_static_total": int(gaps.get("unknown_static_total") or 0),
            "label_total": int(label_quality.get("total") or 0),
            "label_excluded": int(label_quality.get("excluded_from_training") or 0),
            "schema_complete": schema["complete"],
            "missing_summary_keys": schema["missing_summary_keys"],
        }
        summary["total_manifests"] += 1
        _merge_label_quality(summary, label_quality)
        if schema["complete"]:
            summary["schema_complete_manifests"] += 1
        else:
            summary["schema_missing_manifests"] += 1
            summary["schema_missing_key_total"] += len(schema["missing_summary_keys"])
            _merge_counts(
                summary["schema_missing_summary_keys"],
                {key: 1 for key in schema["missing_summary_keys"]},
            )
        if not gaps.get("available"):
            summary["missing_coverage_manifests"] += 1
            entry["reason"] = coverage_source or gaps.get("reason")
            summary["manifests"].append(entry)
            continue
        summary["coverage_manifests"] += 1
        summary["shadow_feature_rows"] += int(gaps.get("total_rows") or 0)
        if int(gaps.get("gap_count") or 0) > 0:
            summary["feature_gap_manifests"] += 1
        if int(gaps.get("zero_count") or 0) > 0:
            summary["feature_zero_manifests"] += 1
        if int(gaps.get("unknown_static_total") or 0) > 0:
            summary["unknown_static_manifests"] += 1
            summary["unknown_static_total"] += int(gaps.get("unknown_static_total") or 0)
        _merge_feature_issue_counts(summary["feature_gaps"], gaps, "missing_prefixes")
        _merge_feature_issue_counts(summary["feature_zero"], gaps, "zero_prefixes")
        _merge_feature_issue_categories(summary["feature_issue_categories"], gaps)
        _merge_counts(summary["unknown_static_features"], gaps.get("unknown_static_features"))
        summary["manifests"].append(entry)
    summary["feature_gaps"] = dict(sorted(summary["feature_gaps"].items()))
    summary["feature_zero"] = dict(sorted(summary["feature_zero"].items()))
    summary["feature_issue_categories"] = dict(sorted(summary["feature_issue_categories"].items()))
    summary["unknown_static_features"] = dict(sorted(summary["unknown_static_features"].items()))
    summary["label_exclusion_reasons"] = dict(sorted(summary["label_exclusion_reasons"].items()))
    summary["schema_missing_summary_keys"] = dict(sorted(summary["schema_missing_summary_keys"].items()))
    return summary


def _manifest_schema_quality(manifest_summary: dict[str, Any]) -> dict[str, Any]:
    missing = [
        key
        for key in REQUIRED_MANIFEST_SUMMARY_KEYS
        if key not in manifest_summary
    ]
    return {
        "complete": not missing,
        "missing_summary_keys": missing,
    }


def _execution_recovery_summary(manifests: list[Path]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "manifests": [],
        "action_recovery": _empty_action_recovery(),
        "terminal_recovery": _empty_terminal_recovery(),
    }
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
        categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}

        action = _compact_action_recovery(manifest_summary.get("action_recovery"))
        action_source = "manifest_summary" if action else None
        if not action:
            action = _action_recovery_from_categories(categories)
            action_source = "category_fallback" if action else None

        terminal = _compact_terminal_recovery(manifest_summary.get("terminal_recovery"))
        terminal_source = "manifest_summary" if terminal else None
        if not terminal:
            terminal = _terminal_recovery_from_categories(categories)
            terminal_source = "category_fallback" if terminal else None

        _merge_action_recovery(summary["action_recovery"], action)
        _merge_terminal_recovery(summary["terminal_recovery"], terminal)
        summary["manifests"].append(
            {
                "manifest_path": str(path),
                "action_recovery_source": action_source,
                "action_recovery_total": _safe_int(action.get("total")),
                "terminal_recovery_source": terminal_source,
                "terminal_recovery_synthetic_terminals": _safe_int(terminal.get("synthetic_terminals")),
            }
        )
    summary["action_recovery"] = _compact_action_recovery(summary["action_recovery"])
    summary["terminal_recovery"] = _compact_terminal_recovery(summary["terminal_recovery"])
    summary["manifests"] = [
        entry
        for entry in summary["manifests"]
        if entry.get("action_recovery_source") or entry.get("terminal_recovery_source")
    ]
    return summary


def _failure_evidence_summary(manifests: list[Path]) -> dict[str, Any]:
    summary = _empty_failure_evidence()
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
        categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}

        evidence = _compact_failure_evidence_summary(manifest_summary.get("failure_evidence"))
        source = "manifest_summary" if evidence else None
        if not evidence:
            evidence = _failure_evidence_from_categories(categories)
            source = "category_fallback" if evidence else None

        _merge_failure_evidence(summary, evidence)
        summary["manifests"].append(
            {
                "manifest_path": str(path),
                "failure_evidence_source": source,
                "runs_with_evidence": _safe_int(evidence.get("runs_with_evidence")),
                "top_evidence_type": _top_count(evidence.get("by_type") or {}),
            }
        )
    compact = _compact_failure_evidence_summary(summary)
    compact["manifests"] = [
        entry for entry in summary["manifests"] if entry.get("failure_evidence_source")
    ]
    return compact


def _empty_failure_evidence() -> dict[str, Any]:
    return {
        "manifests": [],
        "runs_with_evidence": 0,
        "by_type": {},
        "screen_stalls": {"by_screen": {}, "by_reason": {}},
        "action_errors": {"by_kind": {}, "by_status": {}},
        "mcp_reads": {"by_event": {}, "by_diagnostics_status": {}},
        "synthetic_terminals": {"by_source": {}, "terminal_outcome_sources": {}},
    }


def _failure_evidence_from_categories(categories: dict[str, Any]) -> dict[str, Any]:
    summary = _empty_failure_evidence()
    for item in _category_items(categories):
        evidence = item.get("failure_evidence") if isinstance(item.get("failure_evidence"), dict) else {}
        if not evidence:
            continue
        row_summary = _failure_evidence_from_row(evidence, item)
        _merge_failure_evidence(summary, row_summary)
    return _compact_failure_evidence_summary(summary)


def _failure_evidence_from_row(evidence: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    summary = _empty_failure_evidence()
    summary["runs_with_evidence"] = 1
    matched_type = False
    for key in (
        "screen_stall",
        "mcp_read",
        "action_error",
        "synthetic_terminal",
        "terminal_outcome_source",
        "route",
        "pre_boss",
        "boss_combat",
        "json_error_line",
        "last_state",
    ):
        value = evidence.get(key)
        if value in (None, "", [], {}):
            continue
        matched_type = True
        summary["by_type"][key] = 1
    if not matched_type and evidence.get("reason"):
        summary["by_type"]["reason_only"] = 1

    stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
    if stall:
        screen = str(stall.get("screen_type") or "unknown_screen")
        reason = str(stall.get("reason") or evidence.get("reason") or item.get("reason") or "unknown")
        summary["screen_stalls"]["by_screen"][screen] = 1
        summary["screen_stalls"]["by_reason"][reason] = 1

    action = evidence.get("action_error") if isinstance(evidence.get("action_error"), dict) else {}
    if action:
        kind = str(action.get("kind") or "unknown_action_error")
        status = str(action.get("action_status") or "unknown_status")
        summary["action_errors"]["by_kind"][kind] = 1
        summary["action_errors"]["by_status"][status] = 1

    read = evidence.get("mcp_read") if isinstance(evidence.get("mcp_read"), dict) else {}
    if read:
        event = str(read.get("event") or "unknown_event")
        status = str(read.get("diagnostics_status") or "unknown_status")
        summary["mcp_reads"]["by_event"][event] = 1
        summary["mcp_reads"]["by_diagnostics_status"][status] = 1

    synthetic = (
        evidence.get("synthetic_terminal")
        if isinstance(evidence.get("synthetic_terminal"), dict)
        else {}
    )
    if synthetic:
        source = str(synthetic.get("source") or "unknown_source")
        summary["synthetic_terminals"]["by_source"][source] = 1
    terminal_source = evidence.get("terminal_outcome_source")
    if terminal_source:
        source = str(terminal_source)
        summary["synthetic_terminals"]["terminal_outcome_sources"][source] = 1
    return _compact_failure_evidence_summary(summary)


def _compact_failure_evidence_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    runs_with_evidence = _safe_int(raw.get("runs_with_evidence"))
    if runs_with_evidence > 0:
        result["runs_with_evidence"] = runs_with_evidence
    by_type = _sorted_counts(raw.get("by_type"))
    if by_type:
        result["by_type"] = by_type
    for key, count_fields in (
        ("screen_stalls", ("by_screen", "by_reason")),
        ("action_errors", ("by_kind", "by_status")),
        ("mcp_reads", ("by_event", "by_diagnostics_status")),
        ("synthetic_terminals", ("by_source", "terminal_outcome_sources")),
    ):
        nested = raw.get(key) if isinstance(raw.get(key), dict) else {}
        compact_nested: dict[str, Any] = {}
        for field in count_fields:
            counts = _sorted_counts(nested.get(field))
            if counts:
                compact_nested[field] = counts
        if compact_nested:
            result[key] = compact_nested
    manifests = raw.get("manifests") if isinstance(raw.get("manifests"), list) else []
    if manifests:
        result["manifests"] = list(manifests)
    return result


def _merge_failure_evidence(dest: dict[str, Any], raw: dict[str, Any]) -> None:
    if not raw:
        return
    dest["runs_with_evidence"] = _safe_int(dest.get("runs_with_evidence")) + _safe_int(
        raw.get("runs_with_evidence")
    )
    _merge_counts(dest.setdefault("by_type", {}), raw.get("by_type"))
    for key, fields in (
        ("screen_stalls", ("by_screen", "by_reason")),
        ("action_errors", ("by_kind", "by_status")),
        ("mcp_reads", ("by_event", "by_diagnostics_status")),
        ("synthetic_terminals", ("by_source", "terminal_outcome_sources")),
    ):
        dest_nested = dest.setdefault(key, {})
        raw_nested = raw.get(key) if isinstance(raw.get(key), dict) else {}
        for field in fields:
            _merge_counts(dest_nested.setdefault(field, {}), raw_nested.get(field))


def _empty_action_recovery() -> dict[str, Any]:
    return {
        "total": 0,
        "recovered": 0,
        "unrecovered": 0,
        "runs_with_action_recovery": 0,
        "runs_with_recovered_action": 0,
        "runs_with_unrecovered_action": 0,
        "by_status": {},
        "by_kind": {},
    }


def _empty_terminal_recovery() -> dict[str, int]:
    return {
        "synthetic_terminals": 0,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "not_attempted": 0,
        "unhealthy": 0,
    }


def _action_recovery_from_categories(categories: dict[str, Any]) -> dict[str, Any]:
    summary = _empty_action_recovery()
    for item in _category_items(categories):
        action = item.get("action_recovery_summary") if isinstance(item.get("action_recovery_summary"), dict) else {}
        _merge_action_recovery(summary, _compact_action_recovery(action))
    return _compact_action_recovery(summary)


def _terminal_recovery_from_categories(categories: dict[str, Any]) -> dict[str, int]:
    summary = _empty_terminal_recovery()
    for item in _category_items(categories):
        evidence = item.get("failure_evidence") if isinstance(item.get("failure_evidence"), dict) else {}
        synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
        has_synthetic = bool(synthetic or evidence.get("terminal_outcome_source"))
        if not has_synthetic:
            continue
        summary["synthetic_terminals"] += 1
        if synthetic.get("terminal_recovery_attempted"):
            summary["attempted"] += 1
            if synthetic.get("terminal_recovery_succeeded"):
                summary["succeeded"] += 1
            else:
                summary["failed"] += 1
        else:
            summary["not_attempted"] += 1
        post_status = synthetic.get("post_recovery_status")
        if post_status not in (None, "", "healthy"):
            summary["unhealthy"] += 1
    return _compact_terminal_recovery(summary)


def _category_items(categories: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for category in (CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED):
        items = categories.get(category)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield item


def _compact_action_recovery(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    total = _safe_int(raw.get("total"))
    if total <= 0:
        return {}
    result = {
        "total": total,
        "recovered": _safe_int(raw.get("recovered")),
        "unrecovered": _safe_int(raw.get("unrecovered")),
        "runs_with_action_recovery": _safe_int(raw.get("runs_with_action_recovery")),
        "runs_with_recovered_action": _safe_int(raw.get("runs_with_recovered_action")),
        "runs_with_unrecovered_action": _safe_int(raw.get("runs_with_unrecovered_action")),
        "by_status": _sorted_counts(raw.get("by_status")),
        "by_kind": _sorted_counts(raw.get("by_kind")),
    }
    examples = _compact_action_recovery_examples(raw.get("examples"))
    if examples:
        result["examples"] = examples
    return result


def _compact_terminal_recovery(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return _sorted_counts(raw)


def _merge_action_recovery(dest: dict[str, Any], raw: dict[str, Any]) -> None:
    if not raw:
        return
    for key in (
        "total",
        "recovered",
        "unrecovered",
        "runs_with_action_recovery",
        "runs_with_recovered_action",
        "runs_with_unrecovered_action",
    ):
        dest[key] = _safe_int(dest.get(key)) + _safe_int(raw.get(key))
    _merge_counts(dest.setdefault("by_status", {}), raw.get("by_status"))
    _merge_counts(dest.setdefault("by_kind", {}), raw.get("by_kind"))
    _merge_action_recovery_examples(dest, raw.get("examples"))


def _compact_action_recovery_examples(raw: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    examples: list[dict[str, Any]] = []
    for example in raw:
        if not isinstance(example, dict):
            continue
        compact = {
            key: value
            for key, value in example.items()
            if key
            in {
                "source_log",
                "source_category",
                "step",
                "action_status",
                "recovered",
                "kind",
                "actions",
                "executed_actions",
                "available_commands",
                "last_error",
                "rewrite_reason",
                "last_state",
            }
            and value not in (None, "", [], {})
        }
        if compact:
            examples.append(compact)
        if len(examples) >= limit:
            break
    return examples


def _merge_action_recovery_examples(dest: dict[str, Any], raw: Any, *, limit: int = 8) -> None:
    examples = _compact_action_recovery_examples(raw, limit=limit)
    if not examples:
        return
    existing = dest.setdefault("examples", [])
    if not isinstance(existing, list):
        dest["examples"] = examples[:limit]
        return
    existing.extend(examples)
    del existing[limit:]


def _merge_terminal_recovery(dest: dict[str, int], raw: dict[str, int]) -> None:
    if not raw:
        return
    for key in (
        "synthetic_terminals",
        "attempted",
        "succeeded",
        "failed",
        "not_attempted",
        "unhealthy",
    ):
        dest[key] = _safe_int(dest.get(key)) + _safe_int(raw.get(key))


def _merge_counts(dest: dict[str, int], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        count = _safe_int(value)
        if count <= 0:
            continue
        text = str(key)
        dest[text] = dest.get(text, 0) + count


def _sorted_counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    counts = {str(key): _safe_int(value) for key, value in raw.items() if _safe_int(value) > 0}
    return dict(sorted(counts.items()))


def _combat_label_quality(manifest_summary: dict[str, Any]) -> dict[str, Any]:
    label_quality = manifest_summary.get("shadow_label_quality")
    if not isinstance(label_quality, dict):
        return {}
    combat = label_quality.get("combat_search_labels")
    return combat if isinstance(combat, dict) else {}


def _merge_label_quality(summary: dict[str, Any], quality: dict[str, Any]) -> None:
    if not quality:
        return
    summary["shadow_label_rows"] += _safe_int(quality.get("total"))
    summary["shadow_label_trainable"] += _safe_int(quality.get("trainable"))
    excluded = _safe_int(quality.get("excluded_from_training"))
    summary["shadow_label_excluded"] += excluded
    summary["direct_kill_available_labels"] += _safe_int(quality.get("direct_kill_available"))
    summary["missed_single_card_search_labels"] += _safe_int(quality.get("missed_single_card_search"))
    if excluded > 0:
        summary["label_exclusion_manifests"] += 1
    reasons = quality.get("exclusion_reasons") if isinstance(quality.get("exclusion_reasons"), dict) else {}
    for reason, count in reasons.items():
        key = str(reason)
        summary["label_exclusion_reasons"][key] = summary["label_exclusion_reasons"].get(key, 0) + _safe_int(count)


def _merge_feature_issue_counts(counts: dict[str, int], gaps: dict[str, Any], field: str) -> None:
    categories = gaps.get("categories") if isinstance(gaps.get("categories"), dict) else {}
    for category, category_summary in categories.items():
        if not isinstance(category_summary, dict):
            continue
        prefixes = category_summary.get(field) if isinstance(category_summary.get(field), list) else []
        for prefix in prefixes:
            key = f"{category}:{prefix}"
            counts[key] = counts.get(key, 0) + 1


def _merge_feature_issue_categories(counts: dict[str, int], gaps: dict[str, Any]) -> None:
    categories = gaps.get("categories") if isinstance(gaps.get("categories"), dict) else {}
    for category, category_summary in categories.items():
        if not isinstance(category_summary, dict):
            continue
        unknown_static = (
            category_summary.get("unknown_static_features")
            if isinstance(category_summary.get("unknown_static_features"), dict)
            else {}
        )
        has_issue = bool(category_summary.get("missing_prefixes")) or bool(category_summary.get("zero_prefixes")) or bool(unknown_static)
        if has_issue:
            key = str(category)
            counts[key] = counts.get(key, 0) + 1


def _matches_target(item: dict[str, Any], *, character: str, ascension: int) -> bool:
    if character and str(item.get("character") or "").upper() != character.upper():
        return False
    return _safe_int(item.get("ascension")) == ascension


def _run_summary(manifest_path: Path, category: str, item: dict[str, Any]) -> dict[str, Any]:
    boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    attribution = str(item.get("failure_attribution") or "none")
    validation_grade = str(item.get("validation_grade") or "unknown")
    cleared = bool(boss.get("cleared"))
    validation_flags = item.get("validation_flags") or []
    recovered_actions = _safe_int(item.get("recovered_actions"))
    failed_actions = _safe_int(item.get("failed_actions"))
    pristine_cleared = _boss_pristine_clear(boss, cleared=cleared, validation_grade=validation_grade)
    prefix_blockers = list(boss.get("prefix_blockers") or [])
    prefix_pristine_clear = boss.get("prefix_pristine_clear") if "prefix_pristine_clear" in boss else None
    return {
        "manifest_path": str(manifest_path),
        "path": item.get("path"),
        "category": category,
        "reason": item.get("reason"),
        "character": item.get("character"),
        "ascension": item.get("ascension"),
        "floor": item.get("floor"),
        "victory": item.get("victory"),
        "validation_grade": validation_grade,
        "validation_flags": validation_flags,
        "failure_attribution": attribution,
        "failure_tags": item.get("failure_tags") or [],
        "recovered_actions": recovered_actions,
        "failed_actions": failed_actions,
        "act1_boss_reached": bool(boss.get("reached")),
        "act1_boss_cleared": cleared,
        "pristine_act1_boss_cleared": pristine_cleared,
        "act1_boss_prefix_pristine_clear": prefix_pristine_clear,
        "non_pristine_clear_blockers": _non_pristine_clear_blockers(
            cleared=cleared,
            pristine_cleared=pristine_cleared,
            validation_grade=validation_grade,
            validation_flags=validation_flags,
            recovered_actions=recovered_actions,
            failed_actions=failed_actions,
            prefix_blockers=prefix_blockers,
        ),
        "act1_boss": {
            "reached": bool(boss.get("reached")),
            "cleared": cleared,
            "enemy_ids": boss.get("enemy_ids") or [],
            "entry_step": boss.get("entry_step"),
            "entry_hp": boss.get("entry_hp"),
            "entry_potion_count": boss.get("entry_potion_count"),
            "clear_step": boss.get("clear_step"),
            "prefix_pristine_clear": prefix_pristine_clear,
            "prefix_blockers": prefix_blockers,
            "last_turn": boss.get("last_turn"),
            "last_hp": boss.get("last_hp"),
            "potion_use_steps": boss.get("potion_use_steps") or [],
        },
        "attribution_known": _attribution_known(category, item),
    }


def _counts(runs: list[dict[str, Any]]) -> dict[str, Any]:
    categories = _count_by(runs, "category")
    grades = _count_by(runs, "validation_grade")
    attributions = _count_by(runs, "failure_attribution", skip_values={"none"})
    recovery_kinds: dict[str, int] = {}
    blockers: dict[str, int] = {}
    reached_by_enemy: dict[str, int] = {}
    cleared_by_enemy: dict[str, int] = {}
    pristine_by_enemy: dict[str, int] = {}
    prefix_pristine_by_enemy: dict[str, int] = {}
    legacy_pristine_by_enemy: dict[str, int] = {}
    non_pristine_cleared_by_enemy: dict[str, int] = {}
    non_pristine_blockers_by_enemy: dict[str, dict[str, int]] = {}
    uncleared_by_enemy: dict[str, int] = {}
    uncleared_failures_by_enemy: dict[str, dict[str, int]] = {}
    for run in runs:
        if run["act1_boss_reached"]:
            enemy = _boss_enemy_id(run)
            reached_by_enemy[enemy] = reached_by_enemy.get(enemy, 0) + 1
            if run["act1_boss_cleared"]:
                cleared_by_enemy[enemy] = cleared_by_enemy.get(enemy, 0) + 1
                if not run["pristine_act1_boss_cleared"]:
                    non_pristine_cleared_by_enemy[enemy] = non_pristine_cleared_by_enemy.get(enemy, 0) + 1
                    enemy_blockers = non_pristine_blockers_by_enemy.setdefault(enemy, {})
                    for blocker in run.get("non_pristine_clear_blockers") or []:
                        blocker_text = str(blocker)
                        enemy_blockers[blocker_text] = enemy_blockers.get(blocker_text, 0) + 1
            else:
                uncleared_by_enemy[enemy] = uncleared_by_enemy.get(enemy, 0) + 1
                attribution = str(run.get("failure_attribution") or "unknown")
                failures = uncleared_failures_by_enemy.setdefault(enemy, {})
                failures[attribution] = failures.get(attribution, 0) + 1
            if run["pristine_act1_boss_cleared"]:
                pristine_by_enemy[enemy] = pristine_by_enemy.get(enemy, 0) + 1
                if run["act1_boss_cleared"] and run["act1_boss_prefix_pristine_clear"] is True:
                    prefix_pristine_by_enemy[enemy] = prefix_pristine_by_enemy.get(enemy, 0) + 1
                elif run["act1_boss_cleared"] and run["act1_boss_prefix_pristine_clear"] is None:
                    legacy_pristine_by_enemy[enemy] = legacy_pristine_by_enemy.get(enemy, 0) + 1
        for flag in run.get("validation_flags") or []:
            if flag == "recovered_action_race":
                recovery_kinds[flag] = recovery_kinds.get(flag, 0) + 1
        for blocker in run.get("non_pristine_clear_blockers") or []:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    return {
        "total_runs": len(runs),
        CLEAN_TRAINABLE: categories.get(CLEAN_TRAINABLE, 0),
        DIAGNOSTIC_EXCLUDED: categories.get(DIAGNOSTIC_EXCLUDED, 0),
        INFRA_BLOCKED: categories.get(INFRA_BLOCKED, 0),
        "act1_boss_reached": sum(1 for run in runs if run["act1_boss_reached"]),
        "act1_boss_cleared": sum(1 for run in runs if run["act1_boss_cleared"]),
        "pristine_act1_boss_cleared": sum(1 for run in runs if run["pristine_act1_boss_cleared"]),
        "prefix_pristine_act1_boss_cleared": sum(
            1 for run in runs if run["act1_boss_cleared"] and run["act1_boss_prefix_pristine_clear"] is True
        ),
        "legacy_pristine_act1_boss_cleared": sum(
            1
            for run in runs
            if run["act1_boss_cleared"]
            and run["act1_boss_prefix_pristine_clear"] is None
            and run["pristine_act1_boss_cleared"]
        ),
        "explicit_prefix_evidence_runs": sum(
            1 for run in runs if run["act1_boss_cleared"] and run["act1_boss_prefix_pristine_clear"] is not None
        ),
        "usable_act1_boss_cleared": sum(
            1
            for run in runs
            if run["act1_boss_cleared"] and run["validation_grade"] == "usable_with_recoveries"
        ),
        "diagnostic_act1_boss_cleared": sum(
            1 for run in runs if run["act1_boss_cleared"] and run["validation_grade"] == "diagnostic"
        ),
        "unknown_attribution_runs": sum(1 for run in runs if not run["attribution_known"]),
        "recovered_action_runs": sum(1 for run in runs if run["recovered_actions"] > 0),
        "failed_action_runs": sum(1 for run in runs if run["failed_actions"] > 0),
        "validation_grades": grades,
        "failure_attributions": attributions,
        "act1_boss_reached_by_enemy": dict(sorted(reached_by_enemy.items())),
        "act1_boss_cleared_by_enemy": dict(sorted(cleared_by_enemy.items())),
        "pristine_act1_boss_cleared_by_enemy": dict(sorted(pristine_by_enemy.items())),
        "prefix_pristine_act1_boss_cleared_by_enemy": dict(sorted(prefix_pristine_by_enemy.items())),
        "legacy_pristine_act1_boss_cleared_by_enemy": dict(sorted(legacy_pristine_by_enemy.items())),
        "act1_boss_non_pristine_cleared_by_enemy": dict(sorted(non_pristine_cleared_by_enemy.items())),
        "act1_boss_outcomes_by_enemy": _act1_boss_outcomes_by_enemy(
            reached_by_enemy,
            cleared_by_enemy,
            pristine_by_enemy,
            prefix_pristine_by_enemy,
            legacy_pristine_by_enemy,
            non_pristine_cleared_by_enemy,
            uncleared_by_enemy,
        ),
        "act1_boss_non_pristine_clear_blockers_by_enemy": _sort_nested_counts(
            non_pristine_blockers_by_enemy
        ),
        "act1_boss_uncleared_by_enemy": dict(sorted(uncleared_by_enemy.items())),
        "act1_boss_uncleared_failure_attributions_by_enemy": _sort_nested_counts(
            uncleared_failures_by_enemy
        ),
        "recovery_flags": dict(sorted(recovery_kinds.items())),
        "non_pristine_clear_blockers": dict(sorted(blockers.items())),
    }


def _attribution_known(category: str, item: dict[str, Any]) -> bool:
    if item.get("victory") is True:
        return True
    attribution = str(item.get("failure_attribution") or "").strip()
    if category == CLEAN_TRAINABLE and item.get("victory") in {None, True}:
        return True
    return attribution not in UNKNOWN_ATTRIBUTIONS


def _next_action(
    counts: dict[str, Any],
    reach_ok: bool,
    clear_ok: bool,
    pristine_ok: bool,
    attribution_ok: bool,
    no_infra: bool,
) -> str:
    if not counts["total_runs"]:
        return "collect_a0_manifest_batch"
    if not no_infra:
        return "fix_execution_layer"
    if not attribution_ok:
        return "fix_failure_attribution"
    if not reach_ok:
        return "improve_route_and_early_act1_survival"
    if not clear_ok:
        return "improve_act1_boss_combat"
    if not pristine_ok:
        return "collect_pristine_act1_boss_clears"
    return "promote_to_next_validation_batch"


def _blocking_reasons(
    runs: list[dict[str, Any]],
    *,
    reach_ok: bool,
    clear_ok: bool,
    pristine_ok: bool,
    attribution_ok: bool,
    no_infra: bool,
) -> list[str]:
    reasons: list[str] = []
    if not runs:
        reasons.append("no_matching_runs")
    if not no_infra:
        reasons.append("infra_blocked_runs")
    if not attribution_ok:
        reasons.append("unknown_failure_attribution")
    if not reach_ok:
        reasons.append("insufficient_act1_boss_reached")
    if not clear_ok:
        reasons.append("insufficient_act1_boss_cleared")
    if not pristine_ok:
        reasons.append("insufficient_pristine_act1_boss_cleared")
    return reasons


def _gate_progress(
    counts: dict[str, Any],
    *,
    requirements: dict[str, int],
    deficits: dict[str, int],
    checks: dict[str, bool],
) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "current": _safe_int(counts.get(key)),
            "required": _safe_int(requirements.get(key)),
            "deficit": _safe_int(deficits.get(key)),
            "ok": bool(checks.get(key)),
        }
        for key in (
            "act1_boss_reached",
            "act1_boss_cleared",
            "pristine_act1_boss_cleared",
        )
    }


def _remaining_progress(progress: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    remaining: list[dict[str, Any]] = []
    for metric, snapshot in progress.items():
        if snapshot.get("ok"):
            continue
        remaining.append({"metric": metric, **snapshot})
    return remaining


def _next_probe_goal(
    next_action: str,
    primary_remaining: dict[str, Any] | None,
    primary_blocking_reason: str | None,
) -> dict[str, Any]:
    goal: dict[str, Any] = {
        "action": next_action,
        "primary_blocking_reason": primary_blocking_reason,
        "metric": None,
        "deficit": 0,
    }
    if primary_remaining:
        metric = str(primary_remaining.get("metric") or "")
        deficit = _safe_int(primary_remaining.get("deficit"))
        labels = PROGRESS_METRIC_LABELS.get(metric)
        label = labels[0 if deficit == 1 else 1] if labels else (metric or "gate requirements")
        goal.update({
            "metric": metric,
            "deficit": deficit,
            "summary": f"collect {deficit} more {label}",
        })
        goal["acceptance_criteria"] = _next_probe_acceptance_criteria(next_action, metric)
        return goal
    summaries = {
        "collect_a0_manifest_batch": "collect matching A0 manifests",
        "fix_execution_layer": "fix execution-layer infra blockers before more training",
        "fix_failure_attribution": "classify unknown failure attribution before promotion",
        "promote_to_next_validation_batch": "gate passed; promote to the next validation batch",
    }
    goal["summary"] = summaries.get(next_action, next_action)
    goal["acceptance_criteria"] = _next_probe_acceptance_criteria(next_action, None)
    return goal


def _next_probe_acceptance_criteria(next_action: str, metric: str | None) -> dict[str, Any]:
    criteria: dict[str, Any] = {
        "scope": "next_matching_run",
        "counts_toward_metric": metric,
        "accepted_manifest_categories": [CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED],
        "rejected_manifest_categories": [INFRA_BLOCKED],
        "requires_known_failure_attribution_if_failed": True,
        "unknown_failure_attribution_values": sorted(UNKNOWN_ATTRIBUTIONS),
    }
    if next_action == "collect_pristine_act1_boss_clears":
        criteria.update({
            "act1_boss": {
                "reached": True,
                "cleared": True,
                "prefix_pristine_clear": True,
                "prefix_blockers": [],
            },
            "disallowed_prefix_blockers": [
                "recovered_action_race",
                "unrecovered_action_race",
                "failed_action",
                "synthetic_terminal_state",
                "synthetic_terminal",
                "synthetic_after_mcp_null",
            ],
        })
    elif next_action == "improve_act1_boss_combat":
        criteria["act1_boss"] = {"reached": True, "cleared": True}
    elif next_action == "improve_route_and_early_act1_survival":
        criteria["act1_boss"] = {"reached": True}
    elif next_action == "collect_a0_manifest_batch":
        criteria["act1_boss"] = {"reached": "preferred"}
    elif next_action == "fix_execution_layer":
        criteria.update({
            "scope": "offline_remediation_then_validation_run",
            "required_next_validation": {
                "infra_blocked": 0,
                "failed_actions": 0,
            },
        })
    elif next_action == "fix_failure_attribution":
        criteria["required_next_validation"] = {"unknown_attribution_runs": 0}
    elif next_action == "promote_to_next_validation_batch":
        criteria = {
            "scope": "gate_already_passed",
            "counts_toward_metric": None,
            "no_next_validation_required": True,
        }
    return criteria


def _latest_run_acceptance(run: dict[str, Any] | None, next_probe_goal: dict[str, Any]) -> dict[str, Any]:
    if run is None:
        return {"available": False, "accepted": False, "reason": "no_matching_runs"}
    criteria = next_probe_goal.get("acceptance_criteria")
    if not isinstance(criteria, dict):
        criteria = {}
    if criteria.get("no_next_validation_required"):
        return {
            "available": True,
            "accepted": True,
            "not_required": True,
            "metric": None,
            "action": next_probe_goal.get("action"),
            "summary": "gate already passed; latest run acceptance is not required",
            "run_path": run.get("path"),
            "manifest_path": run.get("manifest_path"),
        }
    metric = str(next_probe_goal.get("metric") or criteria.get("counts_toward_metric") or "")
    blockers = _run_acceptance_blockers(run, metric)
    metric_ok = _run_counts_toward_metric(run, metric)
    return {
        "available": True,
        "accepted": bool(metric_ok and not blockers),
        "metric": metric or None,
        "action": next_probe_goal.get("action"),
        "run_path": run.get("path"),
        "manifest_path": run.get("manifest_path"),
        "category": run.get("category"),
        "reason": run.get("reason"),
        "validation_grade": run.get("validation_grade"),
        "failure_attribution": run.get("failure_attribution"),
        "act1_boss": run.get("act1_boss"),
        "blockers": blockers,
    }


def _run_counts_toward_metric(run: dict[str, Any], metric: str) -> bool:
    if metric == "act1_boss_reached":
        return bool(run.get("act1_boss_reached"))
    if metric == "act1_boss_cleared":
        return bool(run.get("act1_boss_cleared"))
    if metric == "pristine_act1_boss_cleared":
        return bool(run.get("pristine_act1_boss_cleared"))
    return False


def _run_acceptance_blockers(run: dict[str, Any], metric: str) -> list[str]:
    blockers: list[str] = []
    if run.get("category") == INFRA_BLOCKED:
        blockers.append("infra_blocked")
    if not run.get("attribution_known"):
        blockers.append("unknown_failure_attribution")

    reached = bool(run.get("act1_boss_reached"))
    cleared = bool(run.get("act1_boss_cleared"))
    pristine = bool(run.get("pristine_act1_boss_cleared"))
    boss = run.get("act1_boss") if isinstance(run.get("act1_boss"), dict) else {}
    prefix_blockers = boss.get("prefix_blockers") if isinstance(boss.get("prefix_blockers"), list) else []

    if metric in {"act1_boss_reached", "act1_boss_cleared", "pristine_act1_boss_cleared"} and not reached:
        blockers.append("act1_boss_not_reached")
    if metric in {"act1_boss_cleared", "pristine_act1_boss_cleared"} and reached and not cleared:
        blockers.append("act1_boss_not_cleared")
    if metric == "pristine_act1_boss_cleared" and reached and cleared and not pristine:
        prefix_pristine = run.get("act1_boss_prefix_pristine_clear")
        if prefix_pristine is False:
            blockers.append("prefix_pristine_clear_false")
        elif prefix_pristine is None:
            blockers.append("legacy_not_pristine")
        for blocker in run.get("non_pristine_clear_blockers") or []:
            blockers.append(str(blocker))
    if metric == "pristine_act1_boss_cleared" and not pristine:
        for blocker in prefix_blockers:
            blockers.append(f"prefix:{blocker}")

    attribution = str(run.get("failure_attribution") or "")
    if blockers and attribution not in {"", "none"}:
        blockers.append(f"failure_attribution:{attribution}")
    return sorted(set(str(blocker) for blocker in blockers if str(blocker)))


def _count_by(rows: list[dict[str, Any]], key: str, *, skip_values: set[str] | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    skip_values = skip_values or set()
    for row in rows:
        value = str(row.get(key) or "unknown")
        if value in skip_values:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _non_pristine_clear_blockers(
    *,
    cleared: bool,
    pristine_cleared: bool,
    validation_grade: str,
    validation_flags: list[Any],
    recovered_actions: int,
    failed_actions: int,
    prefix_blockers: list[str],
) -> list[str]:
    if not cleared or pristine_cleared:
        return []
    if prefix_blockers:
        return [f"prefix:{blocker}" for blocker in sorted(set(prefix_blockers))]
    blockers: list[str] = []
    if validation_grade != "pristine":
        blockers.append(f"grade:{validation_grade}")
    for flag in validation_flags:
        blockers.append(f"flag:{flag}")
    if recovered_actions > 0:
        blockers.append("recovered_actions")
    if failed_actions > 0:
        blockers.append("failed_actions")
    return sorted(set(str(blocker) for blocker in blockers if str(blocker)))


def _boss_pristine_clear(boss: dict[str, Any], *, cleared: bool, validation_grade: str) -> bool:
    if not cleared:
        return False
    if "prefix_pristine_clear" in boss:
        return bool(boss.get("prefix_pristine_clear"))
    return validation_grade == "pristine"


def _top_count(counts: dict[str, int]) -> tuple[str, int] | None:
    if not counts:
        return None
    key, value = max(counts.items(), key=lambda item: (item[1], item[0]))
    return key, value


def _top_focus_count(counts: dict[str, int], name_field: str) -> dict[str, Any] | None:
    top = _top_count(counts)
    if not top:
        return None
    return {name_field: top[0], "count": top[1]}


def _boss_enemy_id(run: dict[str, Any]) -> str:
    boss = run.get("act1_boss") if isinstance(run.get("act1_boss"), dict) else {}
    enemies = boss.get("enemy_ids") if isinstance(boss.get("enemy_ids"), list) else []
    if enemies:
        return str(enemies[0] or "unknown")
    return "unknown"


def _sort_nested_counts(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    return {key: dict(sorted(value.items())) for key, value in sorted(counts.items())}


def _act1_boss_outcomes_by_enemy(
    reached: dict[str, int],
    cleared: dict[str, int],
    pristine: dict[str, int],
    prefix_pristine: dict[str, int],
    legacy_pristine: dict[str, int],
    non_pristine_cleared: dict[str, int],
    uncleared: dict[str, int],
) -> dict[str, dict[str, Any]]:
    enemies = sorted(
        set(reached)
        | set(cleared)
        | set(pristine)
        | set(prefix_pristine)
        | set(legacy_pristine)
        | set(non_pristine_cleared)
        | set(uncleared)
    )
    outcomes: dict[str, dict[str, Any]] = {}
    for enemy in enemies:
        reached_count = _safe_int(reached.get(enemy))
        cleared_count = _safe_int(cleared.get(enemy))
        pristine_count = _safe_int(pristine.get(enemy))
        outcomes[enemy] = {
            "reached": reached_count,
            "cleared": cleared_count,
            "pristine_cleared": pristine_count,
            "prefix_pristine_cleared": _safe_int(prefix_pristine.get(enemy)),
            "legacy_pristine_cleared": _safe_int(legacy_pristine.get(enemy)),
            "non_pristine_cleared": _safe_int(non_pristine_cleared.get(enemy)),
            "uncleared": _safe_int(uncleared.get(enemy)),
            "clear_rate": round(cleared_count / reached_count, 3) if reached_count else 0.0,
            "pristine_clear_rate": round(pristine_count / reached_count, 3) if reached_count else 0.0,
        }
    return outcomes


def _top_uncleared_boss_text(counts: dict[str, Any]) -> str:
    focus = _top_uncleared_boss(counts)
    if not focus:
        return ""
    attribution = focus.get("top_failure_attribution")
    attribution_count = focus.get("top_failure_attribution_count")
    attribution_text = f"/{attribution}:{attribution_count}" if attribution else ""
    return f"; top_uncleared_boss={focus.get('enemy')}:{focus.get('total')}{attribution_text}"


def _top_non_pristine_clear_boss_text(
    totals_by_enemy: dict[str, Any],
    blockers_by_enemy: dict[str, Any],
) -> str:
    focus = _top_non_pristine_clear_boss(totals_by_enemy, blockers_by_enemy)
    if not focus:
        return ""
    blocker = focus.get("top_blocker")
    blocker_count = focus.get("top_blocker_count")
    blocker_text = f"/{blocker}:{blocker_count}" if blocker else ""
    return f"; top_non_pristine_boss={focus.get('enemy')}:{focus.get('total')}{blocker_text}"


def _feature_quality_text(data_quality: dict[str, Any]) -> str:
    rows = _safe_int(data_quality.get("shadow_feature_rows"))
    missing = _safe_int(data_quality.get("missing_coverage_manifests"))
    gap_manifests = _safe_int(data_quality.get("feature_gap_manifests"))
    zero_manifests = _safe_int(data_quality.get("feature_zero_manifests"))
    unknown_total = _safe_int(data_quality.get("unknown_static_total"))
    if rows <= 0 and missing <= 0 and gap_manifests <= 0 and zero_manifests <= 0 and unknown_total <= 0:
        return ""
    parts = []
    if rows > 0:
        parts.append(f"feature_rows={rows}")
    if missing > 0:
        parts.append(f"feature_missing={missing}")
    top_gap = _top_count(data_quality.get("feature_gaps") or {})
    if top_gap:
        parts.append(f"feature_gaps={top_gap[0]}:{top_gap[1]}")
    top_zero = _top_count(data_quality.get("feature_zero") or {})
    if top_zero:
        parts.append(f"feature_zero={top_zero[0]}:{top_zero[1]}")
    top_unknown = _top_count(data_quality.get("unknown_static_features") or {})
    if top_unknown:
        parts.append(f"feature_unknown={top_unknown[0]}:{top_unknown[1]}")
    return "; " + ", ".join(parts) if parts else ""


def _schema_quality_text(data_quality: dict[str, Any]) -> str:
    missing = _safe_int(data_quality.get("schema_missing_manifests"))
    if missing <= 0:
        return ""
    top_missing = _top_count(data_quality.get("schema_missing_summary_keys") or {})
    if top_missing:
        return f"; schema_missing={missing}/{top_missing[0]}:{top_missing[1]}"
    return f"; schema_missing={missing}"


def _label_quality_text(data_quality: dict[str, Any]) -> str:
    rows = _safe_int(data_quality.get("shadow_label_rows"))
    excluded = _safe_int(data_quality.get("shadow_label_excluded"))
    if rows <= 0 and excluded <= 0:
        return ""
    parts = []
    if rows > 0:
        parts.append(f"labels={rows}")
    if excluded > 0:
        top_reason = _top_count(data_quality.get("label_exclusion_reasons") or {})
        if top_reason:
            parts.append(f"label_exclusions={excluded}/{top_reason[0]}:{top_reason[1]}")
        else:
            parts.append(f"label_exclusions={excluded}")
    return "; " + ", ".join(parts) if parts else ""


def _execution_recovery_text(execution_recovery: dict[str, Any]) -> str:
    action = (
        execution_recovery.get("action_recovery")
        if isinstance(execution_recovery.get("action_recovery"), dict)
        else {}
    )
    terminal = (
        execution_recovery.get("terminal_recovery")
        if isinstance(execution_recovery.get("terminal_recovery"), dict)
        else {}
    )
    parts: list[str] = []
    total_actions = _safe_int(action.get("total"))
    if total_actions > 0:
        top_kind = _top_count(action.get("by_kind") or {})
        if top_kind:
            parts.append(f"action_recovery={top_kind[0]}:{top_kind[1]}/{total_actions}")
        else:
            parts.append(f"action_recovery={total_actions}")
        unrecovered = _safe_int(action.get("unrecovered"))
        if unrecovered > 0:
            parts.append(f"unrecovered_actions={unrecovered}")

    synthetic = _safe_int(terminal.get("synthetic_terminals"))
    if synthetic > 0:
        terminal_parts = [f"synthetic:{synthetic}"]
        for key in ("attempted", "succeeded", "failed", "not_attempted", "unhealthy"):
            count = _safe_int(terminal.get(key))
            if count > 0:
                terminal_parts.append(f"{key}:{count}")
        parts.append("terminal_recovery=" + ",".join(terminal_parts))
    return "; " + ", ".join(parts) if parts else ""


def _failure_evidence_text(failure_evidence: dict[str, Any]) -> str:
    if not isinstance(failure_evidence, dict):
        return ""
    parts: list[str] = []
    top_type = _top_count(failure_evidence.get("by_type") or {})
    if top_type:
        parts.append(f"evidence={top_type[0]}:{top_type[1]}")

    screen_stalls = (
        failure_evidence.get("screen_stalls")
        if isinstance(failure_evidence.get("screen_stalls"), dict)
        else {}
    )
    top_screen = _top_count(screen_stalls.get("by_screen") or {})
    if top_screen:
        parts.append(f"screen_stalls={top_screen[0]}:{top_screen[1]}")

    action_errors = (
        failure_evidence.get("action_errors")
        if isinstance(failure_evidence.get("action_errors"), dict)
        else {}
    )
    top_action = _top_count(action_errors.get("by_kind") or {})
    if top_action:
        parts.append(f"action_errors={top_action[0]}:{top_action[1]}")

    mcp_reads = (
        failure_evidence.get("mcp_reads")
        if isinstance(failure_evidence.get("mcp_reads"), dict)
        else {}
    )
    top_mcp = _top_count(mcp_reads.get("by_diagnostics_status") or {})
    if top_mcp:
        parts.append(f"mcp_reads={top_mcp[0]}:{top_mcp[1]}")

    synthetic = (
        failure_evidence.get("synthetic_terminals")
        if isinstance(failure_evidence.get("synthetic_terminals"), dict)
        else {}
    )
    top_terminal_source = _top_count(synthetic.get("terminal_outcome_sources") or {})
    if top_terminal_source:
        parts.append(f"terminal_sources={top_terminal_source[0]}:{top_terminal_source[1]}")
    return "; " + ", ".join(parts) if parts else ""


def _top_uncleared_boss(counts: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(counts, dict) or not counts:
        return None
    totals: dict[str, int] = {}
    for enemy, attribution_counts in counts.items():
        if not isinstance(attribution_counts, dict):
            continue
        total = sum(_safe_int(value) for value in attribution_counts.values())
        if total > 0:
            totals[str(enemy)] = total
    top_enemy = _top_count(totals)
    if not top_enemy:
        return None
    enemy_name, enemy_total = top_enemy
    attribution_counts = counts.get(enemy_name)
    top_attribution = _top_count(attribution_counts if isinstance(attribution_counts, dict) else {})
    return {
        "enemy": enemy_name,
        "total": enemy_total,
        "top_failure_attribution": top_attribution[0] if top_attribution else None,
        "top_failure_attribution_count": top_attribution[1] if top_attribution else 0,
    }


def _top_non_pristine_clear_boss(
    totals_by_enemy: dict[str, Any],
    blockers_by_enemy: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(totals_by_enemy, dict) or not totals_by_enemy:
        return None
    totals = {
        str(enemy): _safe_int(total)
        for enemy, total in totals_by_enemy.items()
        if _safe_int(total) > 0
    }
    top_enemy = _top_count(totals)
    if not top_enemy:
        return None
    enemy_name, enemy_total = top_enemy
    blocker_counts = blockers_by_enemy.get(enemy_name) if isinstance(blockers_by_enemy, dict) else {}
    top_blocker = _top_count(blocker_counts if isinstance(blocker_counts, dict) else {})
    return {
        "enemy": enemy_name,
        "total": enemy_total,
        "top_blocker": top_blocker[0] if top_blocker else None,
        "top_blocker_count": top_blocker[1] if top_blocker else 0,
    }


def _deficit_text(deficits: dict[str, Any]) -> str:
    labels = [
        ("act1_boss_reached", "need_reached"),
        ("act1_boss_cleared", "need_cleared"),
        ("pristine_act1_boss_cleared", "need_pristine"),
    ]
    parts = [f"{label}={_safe_int(deficits.get(key))}" for key, label in labels if _safe_int(deficits.get(key)) > 0]
    if _safe_int(deficits.get("runs")) > 0:
        parts.insert(0, f"need_runs={_safe_int(deficits.get('runs'))}")
    if not parts:
        return ""
    return "; " + ", ".join(parts)


def _latest_acceptance_text(gate: dict[str, Any]) -> str:
    latest = gate.get("latest_run_acceptance")
    if not isinstance(latest, dict) or not latest.get("available") or latest.get("not_required"):
        return ""
    if latest.get("accepted"):
        return "; latest_accept=accepted"
    blockers = latest.get("blockers") if isinstance(latest.get("blockers"), list) else []
    if not blockers:
        return "; latest_reject=unknown"
    shown = [str(blocker) for blocker in blockers[:3]]
    extra = len(blockers) - len(shown)
    suffix = f",+{extra}" if extra > 0 else ""
    return "; latest_reject=" + "|".join(shown) + suffix


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
