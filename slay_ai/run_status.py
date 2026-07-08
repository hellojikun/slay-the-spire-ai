"""Read JSONL run logs and print compact live/terminal status."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .combat_label_audit import label_example_sort_key, prioritize_label_examples
from .static_knowledge import StaticKnowledge
from .static_knowledge_gaps import build_gap_report, compact_gap_report
from .training_manifest import (
    CLEAN_TRAINABLE,
    DIAGNOSTIC_EXCLUDED,
    INFRA_BLOCKED,
    classify_records,
    iter_log_files,
    shadow_quality_for_records,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize live or completed Slay the Spire JSONL runs.")
    parser.add_argument("logs", nargs="+", type=Path, help="JSONL files or directories containing JSONL logs.")
    parser.add_argument("--knowledge-dir", type=Path, default=Path("data") / "static_knowledge")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--static-knowledge-gaps-output",
        type=Path,
        help="Write concrete missing static-knowledge entities from the same resolved logs.",
    )
    parser.add_argument(
        "--assignment-output",
        type=Path,
        help="Write the recommended assignment prompt to this text file.",
    )
    args = parser.parse_args(argv)

    knowledge = StaticKnowledge.load(args.knowledge_dir) if args.knowledge_dir.exists() else None
    static_knowledge_gap_report = (
        build_gap_report(args.logs, knowledge=knowledge, knowledge_dir=args.knowledge_dir)
        if knowledge is not None
        else None
    )
    payload = build_status_payload(
        args.logs,
        knowledge=knowledge,
        knowledge_dir=args.knowledge_dir,
        static_knowledge_gap_report=static_knowledge_gap_report,
        static_knowledge_gap_report_path=args.static_knowledge_gaps_output,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote run status: {args.output}")
    if args.static_knowledge_gaps_output and static_knowledge_gap_report is not None:
        _write_ascii_json(args.static_knowledge_gaps_output, static_knowledge_gap_report)
        print(f"Wrote static knowledge gap report: {args.static_knowledge_gaps_output}")
        print(static_knowledge_gap_report["status_line"])
    if args.assignment_output:
        assignment_prompt = str(
            ((payload.get("summary") or {}).get("recommended_assignment") or {}).get("assignment_prompt") or ""
        )
        args.assignment_output.parent.mkdir(parents=True, exist_ok=True)
        args.assignment_output.write_text(assignment_prompt.rstrip() + "\n", encoding="utf-8")
        print(f"Wrote run status assignment: {args.assignment_output}")
    for status in payload["runs"]:
        print(status["status_line"])
    print(payload["summary"]["status_line"])
    if payload["summary"].get("run_status_next_line"):
        print(payload["summary"]["run_status_next_line"])
    return 0


def build_status_payload(
    logs: list[Path],
    *,
    knowledge: StaticKnowledge | None = None,
    knowledge_dir: Path | None = None,
    static_knowledge_gap_report: dict[str, Any] | None = None,
    static_knowledge_gap_report_path: Path | None = None,
) -> dict[str, Any]:
    input_paths = [Path(path) for path in logs]
    resolved_log_paths = list(iter_log_files(input_paths))
    statuses = [summarize_log(path, knowledge=knowledge) for path in resolved_log_paths]
    warnings = _status_warnings(resolved_log_paths)
    if static_knowledge_gap_report is None and knowledge is not None:
        static_knowledge_gap_report = build_gap_report(
            input_paths,
            knowledge=knowledge,
            knowledge_dir=knowledge_dir,
        )
    summary = _aggregate_status_summary(
        statuses,
        input_paths=input_paths,
        resolved_log_paths=resolved_log_paths,
        warnings=warnings,
        static_knowledge_gaps=compact_gap_report(static_knowledge_gap_report),
        static_knowledge_gap_report_path=static_knowledge_gap_report_path,
    )
    return {
        "version": 1,
        "inputs": [str(path) for path in input_paths],
        "resolved_logs": [str(path) for path in resolved_log_paths],
        "warnings": warnings,
        "summary": summary,
        "runs": statuses,
    }


def _status_warnings(resolved_log_paths: list[Path]) -> list[str]:
    if not resolved_log_paths:
        return ["no_resolved_logs"]
    return []


def _aggregate_status_summary(
    statuses: list[dict[str, Any]],
    *,
    input_paths: list[Path],
    resolved_log_paths: list[Path],
    warnings: list[str],
    static_knowledge_gaps: dict[str, Any] | None = None,
    static_knowledge_gap_report_path: Path | None = None,
) -> dict[str, Any]:
    classification_counts = {CLEAN_TRAINABLE: 0, DIAGNOSTIC_EXCLUDED: 0, INFRA_BLOCKED: 0}
    classification_reasons: dict[str, dict[str, int]] = {
        CLEAN_TRAINABLE: {},
        DIAGNOSTIC_EXCLUDED: {},
        INFRA_BLOCKED: {},
    }
    state_counts: dict[str, int] = {}
    failure_attributions: dict[str, int] = {}
    validation_by_grade: dict[str, int] = {}
    validation_by_flag: dict[str, int] = {}
    prefix_blockers: dict[str, int] = {}
    clear_blockers: dict[str, int] = {}
    evidence_kinds: dict[str, int] = {}
    screen_stall_by_screen: dict[str, int] = {}
    screen_stall_by_reason: dict[str, int] = {}
    terminal_recovery = {
        "synthetic_terminals": 0,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "not_attempted": 0,
        "unhealthy": 0,
    }
    action_recovery_by_status: dict[str, int] = {}
    action_recovery_by_kind: dict[str, int] = {}
    action_recovery_total = 0
    action_recovery_recovered = 0
    action_recovery_unrecovered = 0
    runs_with_action_recovery = 0
    runs_with_recovered_action = 0
    runs_with_unrecovered_action = 0
    combat_label_total = 0
    combat_label_trainable = 0
    combat_label_excluded = 0
    combat_label_direct_kill_available = 0
    combat_label_missed_single_card_search = 0
    combat_label_exclusion_reasons: dict[str, int] = {}
    combat_label_exclusion_examples: list[dict[str, Any]] = []
    shadow_feature_rows = 0
    shadow_feature_unknown_total = 0
    shadow_feature_unknown_runs = 0
    shadow_feature_unknown_static: dict[str, int] = {}
    act1_boss_reached = 0
    act1_boss_cleared = 0
    pristine_act1_boss_cleared = 0

    for status in statuses:
        state = str(status.get("state") or "unknown")
        state_counts[state] = state_counts.get(state, 0) + 1
        classification = status.get("classification") if isinstance(status.get("classification"), dict) else {}
        category = str(classification.get("category") or "unknown")
        classification_counts[category] = classification_counts.get(category, 0) + 1
        reason = str(classification.get("reason") or "unknown")
        reason_counts = classification_reasons.setdefault(category, {})
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        attribution = classification.get("failure_attribution")
        if attribution:
            attr = str(attribution)
            failure_attributions[attr] = failure_attributions.get(attr, 0) + 1
        grade = str(classification.get("validation_grade") or "unknown")
        validation_by_grade[grade] = validation_by_grade.get(grade, 0) + 1
        for flag in classification.get("validation_flags") or []:
            flag_text = str(flag)
            validation_by_flag[flag_text] = validation_by_flag.get(flag_text, 0) + 1
        evidence = classification.get("failure_evidence") if isinstance(classification.get("failure_evidence"), dict) else {}
        for kind in ("mcp_read", "synthetic_terminal", "action_error", "screen_stall"):
            if evidence.get(kind):
                evidence_kinds[kind] = evidence_kinds.get(kind, 0) + 1
        screen_stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
        if screen_stall:
            screen = str(screen_stall.get("screen_type") or "unknown_screen")
            screen_stall_by_screen[screen] = screen_stall_by_screen.get(screen, 0) + 1
            stall_reason = str(screen_stall.get("reason") or evidence.get("reason") or "unknown")
            screen_stall_by_reason[stall_reason] = screen_stall_by_reason.get(stall_reason, 0) + 1
        if evidence.get("terminal_outcome_source"):
            evidence_kinds["terminal_outcome_source"] = evidence_kinds.get("terminal_outcome_source", 0) + 1
        _add_terminal_recovery_counts(terminal_recovery, classification, evidence)
        action_recovery = (
            classification.get("action_recovery_summary")
            if isinstance(classification.get("action_recovery_summary"), dict)
            else {}
        )
        recovery_total = int(action_recovery.get("total") or 0)
        recovery_recovered = int(action_recovery.get("recovered") or 0)
        recovery_unrecovered = int(action_recovery.get("unrecovered") or 0)
        action_recovery_total += recovery_total
        action_recovery_recovered += recovery_recovered
        action_recovery_unrecovered += recovery_unrecovered
        if recovery_total > 0:
            runs_with_action_recovery += 1
        if recovery_recovered > 0:
            runs_with_recovered_action += 1
        if recovery_unrecovered > 0:
            runs_with_unrecovered_action += 1
        _add_counts(action_recovery_by_status, action_recovery.get("by_status"))
        _add_counts(action_recovery_by_kind, action_recovery.get("by_kind"))
        label_quality = status.get("shadow_label_quality") if isinstance(status.get("shadow_label_quality"), dict) else {}
        combat_label_quality = (
            label_quality.get("combat_search_labels")
            if isinstance(label_quality.get("combat_search_labels"), dict)
            else {}
        )
        combat_label_total += int(combat_label_quality.get("total") or 0)
        combat_label_trainable += int(combat_label_quality.get("trainable") or 0)
        combat_label_excluded += int(combat_label_quality.get("excluded_from_training") or 0)
        combat_label_direct_kill_available += int(combat_label_quality.get("direct_kill_available") or 0)
        combat_label_missed_single_card_search += int(combat_label_quality.get("missed_single_card_search") or 0)
        _add_counts(combat_label_exclusion_reasons, combat_label_quality.get("exclusion_reasons"))
        for example in combat_label_quality.get("exclusion_examples") or []:
            if not isinstance(example, dict):
                continue
            compact_example = dict(example)
            if compact_example.get("source_log") in (None, "", [], {}) and status.get("path"):
                compact_example["source_log"] = str(status.get("path"))
            combat_label_exclusion_examples.append(compact_example)
        feature_coverage = (
            status.get("shadow_feature_coverage")
            if isinstance(status.get("shadow_feature_coverage"), dict)
            else {}
        )
        shadow_feature_rows += int(feature_coverage.get("total_rows") or 0)
        unknown_static = _unknown_static_counts(feature_coverage)
        run_unknown_total = sum(unknown_static.values())
        if run_unknown_total > 0:
            shadow_feature_unknown_runs += 1
            shadow_feature_unknown_total += run_unknown_total
            _add_counts(shadow_feature_unknown_static, unknown_static)

        boss = status.get("act1_boss") if isinstance(status.get("act1_boss"), dict) else {}
        if boss.get("reached"):
            act1_boss_reached += 1
        if boss.get("cleared"):
            act1_boss_cleared += 1
            blockers = boss.get("prefix_blockers") if isinstance(boss.get("prefix_blockers"), list) else []
            for blocker in blockers:
                blocker_text = str(blocker)
                prefix_blockers[blocker_text] = prefix_blockers.get(blocker_text, 0) + 1
            if _act1_boss_pristine_clear(boss, grade):
                pristine_act1_boss_cleared += 1
            else:
                for blocker in _non_pristine_clear_blockers(classification, boss):
                    clear_blockers[blocker] = clear_blockers.get(blocker, 0) + 1

    summary = {
        "input_count": len(input_paths),
        "resolved_log_count": len(resolved_log_paths),
        "input_paths": [str(path) for path in input_paths],
        "resolved_log_paths": [str(path) for path in resolved_log_paths],
        "run_count": len(statuses),
        "warnings": warnings,
        "state_counts": _sorted_counts(state_counts),
        "classification_counts": _sorted_counts(classification_counts),
        "classification_reasons": {
            category: _sorted_counts(counts)
            for category, counts in classification_reasons.items()
        },
        "action_recovery": {
            "total": action_recovery_total,
            "recovered": action_recovery_recovered,
            "unrecovered": action_recovery_unrecovered,
            "runs_with_action_recovery": runs_with_action_recovery,
            "runs_with_recovered_action": runs_with_recovered_action,
            "runs_with_unrecovered_action": runs_with_unrecovered_action,
            "by_status": _sorted_counts(action_recovery_by_status),
            "by_kind": _sorted_counts(action_recovery_by_kind),
        },
        "shadow_label_quality": {
            "combat_search_labels": {
                "total": combat_label_total,
                "trainable": combat_label_trainable,
                "excluded_from_training": combat_label_excluded,
                "direct_kill_available": combat_label_direct_kill_available,
                "missed_single_card_search": combat_label_missed_single_card_search,
                "exclusion_reasons": _sorted_counts(combat_label_exclusion_reasons),
            }
        },
        "shadow_feature_coverage": {
            "total_rows": shadow_feature_rows,
            "unknown_static_runs": shadow_feature_unknown_runs,
            "unknown_static_total": shadow_feature_unknown_total,
            "unknown_static_features": _sorted_counts(shadow_feature_unknown_static),
        },
        "static_knowledge_gaps": static_knowledge_gaps or {},
        "failure_attributions": _sorted_counts(failure_attributions),
        "failure_evidence_kinds": _sorted_counts(evidence_kinds),
        "screen_stalls": {
            "by_screen": _sorted_counts(screen_stall_by_screen),
            "by_reason": _sorted_counts(screen_stall_by_reason),
        },
        "terminal_recovery": _sorted_counts(terminal_recovery),
        "validation": {
            "by_grade": _sorted_counts(validation_by_grade),
            "by_flag": _sorted_counts(validation_by_flag),
            "act1_boss_reached": act1_boss_reached,
            "act1_boss_cleared": act1_boss_cleared,
            "pristine_act1_boss_cleared": pristine_act1_boss_cleared,
            "act1_boss_prefix_blockers": _sorted_counts(prefix_blockers),
            "act1_boss_clear_blockers": _sorted_counts(clear_blockers),
        },
    }
    if combat_label_exclusion_examples:
        summary["shadow_label_quality"]["combat_search_labels"]["exclusion_examples"] = prioritize_label_examples(
            combat_label_exclusion_examples,
            limit=5,
        )
    if static_knowledge_gap_report_path and static_knowledge_gaps:
        summary["static_knowledge_gap_report_path"] = str(static_knowledge_gap_report_path)
    summary["status_line"] = summary_status_line(summary)
    recommendation = _summary_recommendation(summary)
    recommendation_sources = _recommendation_source_runs(statuses, recommendation)
    assignment = _recommended_assignment(recommendation, recommendation_sources, summary=summary)
    summary["recommended_next_action"] = recommendation["action"]
    summary["recommended_owner"] = recommendation["owner"]
    summary["recommended_reason"] = recommendation["reason"]
    summary["recommended_live_mcp_required"] = recommendation["requires_live_mcp_ownership"]
    summary["recommended_source_count"] = assignment["source_count"]
    summary["recommended_source_paths"] = assignment["source_paths"]
    summary["recommended_source_runs"] = assignment["source_runs"]
    summary["recommended_assignment"] = assignment
    summary["run_status_next_line"] = run_status_next_line(summary)
    return summary


def summary_status_line(summary: dict[str, Any]) -> str:
    validation = summary.get("validation") if isinstance(summary.get("validation"), dict) else {}
    parts = [
        "run_status_summary:",
        f"runs={summary.get('run_count', 0)}",
        f"resolved={summary.get('resolved_log_count', 0)}",
    ]
    for key, label in (
        ("state_counts", "states"),
        ("classification_counts", "classes"),
        ("failure_attributions", "failures"),
    ):
        text = _counts_text(summary.get(key))
        if text:
            parts.append(f"{label}={text}")
        if key == "classification_counts":
            reason_text = _classification_reasons_text(summary.get("classification_reasons"))
            if reason_text:
                parts.append(f"class_reasons={reason_text}")
    grade_text = _counts_text(validation.get("by_grade"))
    if grade_text:
        parts.append(f"validation={grade_text}")
    flag_text = _counts_text(validation.get("by_flag"))
    if flag_text:
        parts.append(f"flags={flag_text}")
    recovery_text = _action_recovery_text(summary.get("action_recovery"))
    if recovery_text:
        parts.append(f"recovery={recovery_text}")
    label_exclusion_text = _label_exclusion_text(summary.get("shadow_label_quality"))
    if label_exclusion_text:
        parts.append(f"label_exclusions={label_exclusion_text}")
    feature_unknown_text = _feature_unknown_text(summary.get("shadow_feature_coverage"))
    if feature_unknown_text:
        parts.append(f"feature_unknown={feature_unknown_text}")
    terminal_recovery_text = _terminal_recovery_text(summary.get("terminal_recovery"))
    if terminal_recovery_text:
        parts.append(f"terminal_recovery={terminal_recovery_text}")
    parts.append(
        "act1_boss="
        f"reached:{validation.get('act1_boss_reached', 0)},"
        f"cleared:{validation.get('act1_boss_cleared', 0)},"
        f"pristine:{validation.get('pristine_act1_boss_cleared', 0)}"
    )
    evidence_text = _counts_text(summary.get("failure_evidence_kinds"))
    if evidence_text:
        parts.append(f"evidence={evidence_text}")
    screen_stall_text = _screen_stall_summary_text(summary.get("screen_stalls"))
    if screen_stall_text:
        parts.extend(screen_stall_text)
    blocker_text = _counts_text(validation.get("act1_boss_clear_blockers"))
    if blocker_text:
        parts.append(f"clear_blockers={blocker_text}")
    warning_text = ",".join(str(warning) for warning in summary.get("warnings") or [])
    if warning_text:
        parts.append(f"warnings={warning_text}")
    return " ".join(parts)


def run_status_next_line(summary: dict[str, Any]) -> str:
    assignment = summary.get("recommended_assignment") if isinstance(summary.get("recommended_assignment"), dict) else {}
    contract = (
        assignment.get("execution_contract") if isinstance(assignment.get("execution_contract"), dict) else {}
    )
    return " ".join(
        [
            "run_status_next:",
            f"recommended={_cli_token(summary.get('recommended_next_action') or 'none')}",
            f"owner={_cli_token(summary.get('recommended_owner') or 'unknown')}",
            f"reason={_cli_token(summary.get('recommended_reason') or 'none')}",
            f"mode={_cli_token(contract.get('mode') or 'unknown')}",
            f"live_mcp_required={str(bool(summary.get('recommended_live_mcp_required'))).lower()}",
            f"source_count={int(summary.get('recommended_source_count') or 0)}",
            f"first_source={_cli_token(_first_source_name(summary))}",
        ]
    )


def _summary_recommendation(summary: dict[str, Any]) -> dict[str, Any]:
    warnings = summary.get("warnings") if isinstance(summary.get("warnings"), list) else []
    if "no_resolved_logs" in warnings:
        return _recommendation("collect_a0_manifest_batch", "runner_agent", "no_resolved_logs", live=True)

    classifications = summary.get("classification_counts") if isinstance(summary.get("classification_counts"), dict) else {}
    action_recovery = summary.get("action_recovery") if isinstance(summary.get("action_recovery"), dict) else {}
    terminal_recovery = summary.get("terminal_recovery") if isinstance(summary.get("terminal_recovery"), dict) else {}
    if int(classifications.get(INFRA_BLOCKED) or 0) > 0:
        return _recommendation("fix_execution_layer", "engineering_agent", "infra_blocked_runs")
    if int(action_recovery.get("unrecovered") or 0) > 0:
        return _recommendation("fix_execution_layer", "engineering_agent", "unrecovered_action_errors")
    if int(terminal_recovery.get("failed") or 0) > 0 or int(terminal_recovery.get("unhealthy") or 0) > 0:
        return _recommendation("fix_terminal_recovery", "engineering_agent", "terminal_recovery_unhealthy")

    label_quality = summary.get("shadow_label_quality") if isinstance(summary.get("shadow_label_quality"), dict) else {}
    combat_quality = (
        label_quality.get("combat_search_labels")
        if isinstance(label_quality.get("combat_search_labels"), dict)
        else {}
    )
    if int(combat_quality.get("excluded_from_training") or 0) > 0:
        return _recommendation("review_excluded_combat_search_labels", "ai_agent", "label_exclusions")

    feature_coverage = (
        summary.get("shadow_feature_coverage")
        if isinstance(summary.get("shadow_feature_coverage"), dict)
        else {}
    )
    if int(feature_coverage.get("unknown_static_total") or 0) > 0:
        return _recommendation("update_static_knowledge", "ai_agent", "unknown_static_features")

    if int(classifications.get(DIAGNOSTIC_EXCLUDED) or 0) > 0:
        return _recommendation("review_diagnostic_exclusions", "engineering_agent", "diagnostic_excluded_runs")

    failure_attributions = (
        summary.get("failure_attributions") if isinstance(summary.get("failure_attributions"), dict) else {}
    )
    attribution = _top_count_key(failure_attributions)
    if attribution:
        return _recommendation(*_recommendation_for_attribution(attribution))
    if int(classifications.get(CLEAN_TRAINABLE) or 0) > 0:
        return _recommendation("review_clean_trainable_failures", "ai_agent", "clean_trainable_runs")
    return _recommendation("monitor_runs", "main_agent", "no_actionable_runs")


def _recommendation_source_runs(statuses: list[dict[str, Any]], recommendation: dict[str, Any]) -> list[dict[str, Any]]:
    reason = str(recommendation.get("reason") or "")
    action = str(recommendation.get("action") or "")
    sources: list[dict[str, Any]] = []
    for status in statuses:
        if _status_matches_recommendation(status, reason, action):
            sources.append(_compact_recommendation_source_run(status))
    return sorted(sources, key=_source_run_sort_key)


def _recommended_assignment(
    recommendation: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    summary: dict[str, Any],
) -> dict[str, Any]:
    source_runs = sources[:5]
    source_paths = [str(source["path"]) for source in source_runs]
    action = str(recommendation.get("action") or "none")
    owner = str(recommendation.get("owner") or "unknown")
    resolved_paths = summary.get("resolved_log_paths") if isinstance(summary.get("resolved_log_paths"), list) else []
    assignment = {
        "owner": owner,
        "action": action,
        "reason": str(recommendation.get("reason") or "none"),
        "execution_contract": _execution_contract_for_recommendation(owner, action, recommendation),
        "status_line": summary.get("status_line"),
        "input_count": int(summary.get("input_count") or 0),
        "input_paths": [str(path) for path in summary.get("input_paths") or []],
        "resolved_log_count": int(summary.get("resolved_log_count") or 0),
        "resolved_log_paths": [str(path) for path in resolved_paths[:10]],
        "resolved_log_paths_truncated": max(0, len(resolved_paths) - 10),
        "warnings": [str(warning) for warning in summary.get("warnings") or []],
        "static_knowledge_gaps": summary.get("static_knowledge_gaps") or {},
        "static_knowledge_gap_report_path": summary.get("static_knowledge_gap_report_path"),
        "source_count": len(sources),
        "source_paths": source_paths,
        "source_runs": source_runs,
    }
    assignment["assignment_prompt"] = _recommended_assignment_prompt(assignment)
    return assignment


def _recommended_assignment_prompt(assignment: dict[str, Any]) -> str:
    contract = (
        assignment.get("execution_contract")
        if isinstance(assignment.get("execution_contract"), dict)
        else {}
    )
    source_runs = assignment.get("source_runs") if isinstance(assignment.get("source_runs"), list) else []
    lines = [
        "Run status assignment",
        f"Agent: {assignment.get('owner') or 'unknown'}",
        f"Action: {assignment.get('action') or 'none'}",
        f"Reason: {assignment.get('reason') or 'none'}",
        f"Run status summary: {assignment.get('status_line') or 'missing'}",
        f"Input count: {int(assignment.get('input_count') or 0)}",
        f"Resolved log count: {int(assignment.get('resolved_log_count') or 0)}",
        f"Execution mode: {contract.get('mode') or 'unknown'}",
        f"Requires live MCP ownership: {bool(contract.get('requires_live_mcp_ownership'))}",
        f"Source count: {int(assignment.get('source_count') or 0)}",
    ]
    input_paths = assignment.get("input_paths") if isinstance(assignment.get("input_paths"), list) else []
    if input_paths:
        lines.append("Input paths: " + ", ".join(str(path) for path in input_paths[:5]))
    resolved_paths = (
        assignment.get("resolved_log_paths")
        if isinstance(assignment.get("resolved_log_paths"), list)
        else []
    )
    if resolved_paths:
        resolved_line = ", ".join(str(path) for path in resolved_paths)
        truncated = int(assignment.get("resolved_log_paths_truncated") or 0)
        if truncated > 0:
            resolved_line += f", +{truncated} more"
        lines.append("Resolved logs: " + resolved_line)
    warnings = assignment.get("warnings") if isinstance(assignment.get("warnings"), list) else []
    if warnings:
        lines.append("Warnings: " + ", ".join(str(warning) for warning in warnings))
    gap_path = assignment.get("static_knowledge_gap_report_path")
    if gap_path:
        lines.append(f"Static knowledge gap report: {gap_path}")
    static_knowledge_gaps = (
        assignment.get("static_knowledge_gaps")
        if isinstance(assignment.get("static_knowledge_gaps"), dict)
        else {}
    )
    if static_knowledge_gaps:
        lines.append("Static knowledge gaps: " + _prompt_json(static_knowledge_gaps))
    allowed = contract.get("allowed_operations") if isinstance(contract.get("allowed_operations"), list) else []
    if allowed:
        lines.append("Allowed operations: " + ", ".join(str(item) for item in allowed))
    forbidden = contract.get("forbidden_operations") if isinstance(contract.get("forbidden_operations"), list) else []
    if forbidden:
        lines.append("Forbidden operations: " + ", ".join(str(item) for item in forbidden))
    if source_runs:
        lines.append("Source runs:")
        for source in source_runs:
            lines.append(f"- {source.get('path') or '<missing path>'}")
            status = source.get("status_line")
            if status:
                lines.append(f"  status: {status}")
            evidence = _source_run_prompt_evidence(source)
            if evidence:
                lines.append(f"  evidence: {evidence}")
    else:
        lines.append("Source runs: none")
    return "\n".join(lines)


def _source_run_prompt_evidence(source: dict[str, Any]) -> str:
    parts = []
    for key in ("category", "reason", "failure_attribution", "validation_grade"):
        value = source.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}={value}")
    for key in ("recovered_actions", "failed_actions", "top_action_recovery_kind"):
        value = source.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}={value}")
    for key in ("label_exclusions", "feature_unknown"):
        value = source.get(key)
        if value:
            parts.append(f"{key}={value}")
    examples = source.get("label_exclusion_examples") if isinstance(source.get("label_exclusion_examples"), list) else []
    if examples:
        parts.append("label_exclusion_examples=" + _prompt_json(examples[:3]))
    failure_evidence = source.get("failure_evidence") if isinstance(source.get("failure_evidence"), dict) else {}
    if failure_evidence:
        parts.append("failure_evidence=" + _prompt_json(failure_evidence))
    boss = source.get("act1_boss") if isinstance(source.get("act1_boss"), dict) else {}
    if boss:
        parts.append("act1_boss=" + _prompt_json(boss))
    return " ".join(str(part) for part in parts)


def _execution_contract_for_recommendation(
    owner: str,
    action: str,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    live_required = bool(recommendation.get("requires_live_mcp_ownership"))
    if owner == "runner_agent":
        return {
            "mode": "live_mcp_gated_collection" if live_required else "read_only_flow_monitoring",
            "requires_live_mcp_ownership": live_required,
            "allowed_operations": ["read_logs", "refresh_manifest_gate", "prepare_probe_command"],
            "forbidden_operations": ["edit_code", "train_models", "control_live_mcp_without_explicit_ownership"],
        }
    if owner == "engineering_agent":
        return {
            "mode": "offline_code_or_data_infra",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["inspect_logs", "edit_runner_or_data_layer", "add_tests", "run_offline_replay"],
            "forbidden_operations": ["control_live_mcp_without_explicit_ownership", "train_strategy_models"],
        }
    if owner == "ai_agent":
        return {
            "mode": "offline_strategy_or_shadow_model",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["inspect_logs", "score_shadow_advice", "train_shadow_models_to_scratch_dir"],
            "forbidden_operations": ["control_live_mcp", "replace_live_policy_with_model"],
        }
    if owner == "main_agent":
        return {
            "mode": "coordination_only",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["choose_next_handoff", "merge_evidence", "decide_acceptance"],
            "forbidden_operations": ["control_live_mcp_unless_ownership_changes"],
        }
    return {
        "mode": "unknown_owner",
        "requires_live_mcp_ownership": live_required,
        "allowed_operations": ["read_logs"],
        "forbidden_operations": ["control_live_mcp_without_explicit_ownership"],
    }


def _compact_recommendation_source_run(status: dict[str, Any]) -> dict[str, Any]:
    classification = status.get("classification") if isinstance(status.get("classification"), dict) else {}
    action_recovery = (
        classification.get("action_recovery_summary")
        if isinstance(classification.get("action_recovery_summary"), dict)
        else {}
    )
    source = {
        "path": str(status.get("path") or ""),
        "state": status.get("state"),
        "last_step": status.get("last_step"),
        "category": classification.get("category"),
        "reason": classification.get("reason"),
        "failure_attribution": classification.get("failure_attribution"),
        "validation_grade": classification.get("validation_grade"),
        "recovered_actions": int(classification.get("recovered_actions") or 0),
        "failed_actions": int(classification.get("failed_actions") or 0),
        "status_line": status.get("status_line"),
    }
    top_recovery_kind = _top_count_key(
        action_recovery.get("by_kind") if isinstance(action_recovery.get("by_kind"), dict) else {}
    )
    if top_recovery_kind:
        source["top_action_recovery_kind"] = top_recovery_kind
    label_exclusions = _label_exclusion_text(status.get("shadow_label_quality"))
    if label_exclusions:
        source["label_exclusions"] = label_exclusions
        label_quality = status.get("shadow_label_quality") if isinstance(status.get("shadow_label_quality"), dict) else {}
        combat = (
            label_quality.get("combat_search_labels")
            if isinstance(label_quality.get("combat_search_labels"), dict)
            else {}
        )
        examples = combat.get("exclusion_examples") if isinstance(combat.get("exclusion_examples"), list) else []
        if examples:
            source["label_exclusion_examples"] = prioritize_label_examples(examples, limit=3)
    feature_unknown = _feature_unknown_text(status.get("shadow_feature_coverage"))
    if feature_unknown:
        source["feature_unknown"] = feature_unknown
    failure_evidence = _compact_source_failure_evidence(classification.get("failure_evidence"))
    if failure_evidence:
        source["failure_evidence"] = failure_evidence
    boss = status.get("act1_boss") if isinstance(status.get("act1_boss"), dict) else {}
    if boss:
        source["act1_boss"] = _compact_source_boss(boss)
    return source


def _source_run_sort_key(source: dict[str, Any]) -> tuple[int, int, int, int, str]:
    examples = source.get("label_exclusion_examples") if isinstance(source.get("label_exclusion_examples"), list) else []
    if examples:
        best = min(
            (label_example_sort_key(example, order) for order, example in enumerate(examples, start=1) if isinstance(example, dict)),
            default=(9, 0, 0, 0),
        )
        return (*best, str(source.get("path") or ""))
    return (9, 0, 0, 0, str(source.get("path") or ""))


def _compact_source_failure_evidence(raw: Any) -> dict[str, Any]:
    evidence = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {}
    stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
    if stall:
        result["screen_stall"] = _compact_screen_stall_evidence(stall)
    read = evidence.get("mcp_read") if isinstance(evidence.get("mcp_read"), dict) else {}
    if read:
        result["mcp_read"] = _compact_mcp_read_evidence(read)
    action = evidence.get("action_error") if isinstance(evidence.get("action_error"), dict) else {}
    if action:
        result["action_error"] = _compact_action_error_evidence(action)
    synthetic = (
        evidence.get("synthetic_terminal")
        if isinstance(evidence.get("synthetic_terminal"), dict)
        else {}
    )
    if synthetic or evidence.get("terminal_outcome_source"):
        result["synthetic_terminal"] = _compact_synthetic_terminal_evidence(
            synthetic,
            evidence.get("terminal_outcome_source"),
        )
    if evidence.get("json_error_line") is not None:
        result["json_error_line"] = evidence.get("json_error_line")
    return result


def _compact_screen_stall_evidence(stall: dict[str, Any]) -> dict[str, Any]:
    result = _copy_present(
        stall,
        (
            "reason",
            "screen_type",
            "floor",
            "repeat_count",
            "first_step",
            "last_step",
            "room_phase",
            "initial_reward_count",
            "last_reward_count",
            "initial_card_count",
            "last_card_count",
            "initial_relic_count",
            "last_relic_count",
            "chest_open",
        ),
    )
    action_text = _screen_stall_action_text(stall.get("last_actions"))
    if action_text:
        result["last_action"] = action_text
    return result


def _compact_mcp_read_evidence(read: dict[str, Any]) -> dict[str, Any]:
    result = _copy_present(read, ("step", "event", "diagnostics_status"))
    last_state = read.get("last_state") if isinstance(read.get("last_state"), dict) else {}
    if last_state:
        result["last_state"] = _copy_present(
            last_state,
            ("screen_type", "room_phase", "floor", "act", "in_game"),
        )
    return result


def _compact_action_error_evidence(action: dict[str, Any]) -> dict[str, Any]:
    result = _copy_present(action, ("step", "kind", "action_status", "rewrite_reason"))
    attempted = action.get("action") if isinstance(action.get("action"), dict) else {}
    if attempted:
        result["action"] = _copy_present(
            attempted,
            ("action", "card_index", "target_index", "potion_index", "choice_index"),
        )
    return result


def _compact_synthetic_terminal_evidence(synthetic: dict[str, Any], outcome_source: Any) -> dict[str, Any]:
    result = _copy_present(
        synthetic,
        (
            "step",
            "source",
            "terminal_recovery_attempted",
            "terminal_recovery_succeeded",
            "post_recovery_status",
        ),
    )
    if outcome_source not in (None, "", [], {}):
        result["terminal_outcome_source"] = outcome_source
    return result


def _copy_present(raw: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: raw[key] for key in keys if raw.get(key) not in (None, "", [], {})}


def _compact_source_boss(boss: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "reached": bool(boss.get("reached")),
        "cleared": bool(boss.get("cleared")),
    }
    for key in ("enemy_ids", "last_turn", "last_hp", "clear_step", "prefix_pristine_clear", "prefix_blockers"):
        if boss.get(key) is not None:
            result[key] = boss[key]
    return result


def _status_matches_recommendation(status: dict[str, Any], reason: str, action: str) -> bool:
    classification = status.get("classification") if isinstance(status.get("classification"), dict) else {}
    category = str(classification.get("category") or "")
    if reason == "infra_blocked_runs":
        return category == INFRA_BLOCKED
    if reason == "unrecovered_action_errors":
        recovery = (
            classification.get("action_recovery_summary")
            if isinstance(classification.get("action_recovery_summary"), dict)
            else {}
        )
        return int(recovery.get("unrecovered") or 0) > 0
    if reason == "terminal_recovery_unhealthy":
        flags = classification.get("validation_flags") if isinstance(classification.get("validation_flags"), list) else []
        evidence = classification.get("failure_evidence") if isinstance(classification.get("failure_evidence"), dict) else {}
        synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
        return "terminal_recovery_unhealthy" in flags or bool(
            synthetic.get("terminal_recovery_attempted") and not synthetic.get("terminal_recovery_succeeded")
        )
    if reason == "label_exclusions":
        label_quality = status.get("shadow_label_quality") if isinstance(status.get("shadow_label_quality"), dict) else {}
        combat = (
            label_quality.get("combat_search_labels")
            if isinstance(label_quality.get("combat_search_labels"), dict)
            else {}
        )
        return int(combat.get("excluded_from_training") or 0) > 0
    if reason == "unknown_static_features":
        feature_coverage = (
            status.get("shadow_feature_coverage")
            if isinstance(status.get("shadow_feature_coverage"), dict)
            else {}
        )
        return sum(_unknown_static_counts(feature_coverage).values()) > 0
    if reason == "diagnostic_excluded_runs":
        return category == DIAGNOSTIC_EXCLUDED
    if reason in {"mcp_execution", "logging_infra", "diagnostic_incomplete", "unknown_clean_failure"}:
        return str(classification.get("failure_attribution") or "") == reason
    if action.startswith("inspect_"):
        return str(classification.get("failure_attribution") or "") == reason
    if reason == "clean_trainable_runs":
        return category == CLEAN_TRAINABLE
    return False


def _recommendation(action: str, owner: str, reason: str, *, live: bool = False) -> dict[str, Any]:
    return {
        "action": action,
        "owner": owner,
        "reason": reason,
        "requires_live_mcp_ownership": live,
    }


def _recommendation_for_attribution(attribution: str) -> tuple[str, str, str]:
    if attribution in {"mcp_execution", "logging_infra"}:
        return ("fix_execution_layer", "engineering_agent", attribution)
    if attribution == "diagnostic_incomplete":
        return ("review_diagnostic_exclusions", "engineering_agent", attribution)
    if attribution in {"card_selection", "combat_planning", "deck_quality", "potion_planning", "route_risk"}:
        return (f"inspect_{attribution}", "ai_agent", attribution)
    if attribution == "unknown_clean_failure":
        return ("inspect_clean_failure", "ai_agent", attribution)
    return (f"inspect_{attribution}", "main_agent", attribution)


def _top_count_key(raw: dict[str, Any]) -> str | None:
    pairs = [(str(key), int(value or 0)) for key, value in raw.items() if int(value or 0) > 0]
    if not pairs:
        return None
    pairs.sort(key=lambda item: (-item[1], item[0]))
    return pairs[0][0]


def _cli_token(value: Any) -> str:
    text = str(value or "none").strip()
    if not text:
        return "none"
    return "_".join(text.split())


def _prompt_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first_source_name(summary: dict[str, Any]) -> str:
    paths = summary.get("recommended_source_paths") if isinstance(summary.get("recommended_source_paths"), list) else []
    if not paths:
        return "none"
    return Path(str(paths[0])).name


def _counts_text(raw: Any, *, limit: int = 4) -> str:
    if not isinstance(raw, dict) or not raw:
        return ""
    pairs = [(str(key), int(value or 0)) for key, value in raw.items() if int(value or 0) > 0]
    if not pairs:
        return ""
    pairs.sort(key=lambda item: (-item[1], item[0]))
    shown = [f"{key}:{value}" for key, value in pairs[:limit]]
    if len(pairs) > len(shown):
        shown.append(f"+{len(pairs) - len(shown)}")
    return ",".join(shown)


def _classification_reasons_text(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    flat: dict[str, int] = {}
    for category, reasons in raw.items():
        if not isinstance(reasons, dict):
            continue
        category_text = str(category)
        for reason, value in reasons.items():
            count = int(value or 0)
            if count <= 0:
                continue
            key = f"{category_text}/{reason}"
            flat[key] = flat.get(key, 0) + count
    return _counts_text(flat)


def _add_counts(dest: dict[str, int], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        count = int(value or 0)
        if count > 0:
            key_text = str(key)
            dest[key_text] = dest.get(key_text, 0) + count


def _label_exclusion_text(label_quality: Any) -> str:
    if not isinstance(label_quality, dict):
        return ""
    combat = label_quality.get("combat_search_labels")
    if not isinstance(combat, dict):
        return ""
    excluded = int(combat.get("excluded_from_training") or 0)
    if excluded <= 0:
        return ""
    reason_text = _counts_text(combat.get("exclusion_reasons"), limit=1)
    if reason_text:
        return f"combat_search:{excluded}/{reason_text}"
    return f"combat_search:{excluded}"


def _feature_unknown_text(feature_coverage: Any) -> str:
    return _counts_text(_unknown_static_counts(feature_coverage))


def _unknown_static_counts(feature_coverage: Any) -> dict[str, int]:
    if not isinstance(feature_coverage, dict):
        return {}
    categories = feature_coverage.get("categories")
    if not isinstance(categories, dict):
        features = feature_coverage.get("unknown_static_features")
        return _sorted_counts(features) if isinstance(features, dict) else {}
    counts: dict[str, int] = {}
    for category, category_summary in categories.items():
        if not isinstance(category_summary, dict):
            continue
        unknown_features = category_summary.get("unknown_static_features")
        if not isinstance(unknown_features, dict):
            continue
        for field, field_summary in unknown_features.items():
            if isinstance(field_summary, dict):
                total = int((field_summary or {}).get("total") or 0)
            else:
                total = int(field_summary or 0)
            if total > 0:
                key = f"{category}:{field}"
                counts[key] = counts.get(key, 0) + total
    return _sorted_counts(counts)


def _add_terminal_recovery_counts(dest: dict[str, int], classification: dict[str, Any], evidence: dict[str, Any]) -> None:
    synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
    has_synthetic = bool(synthetic or evidence.get("terminal_outcome_source"))
    if not has_synthetic:
        return
    dest["synthetic_terminals"] = dest.get("synthetic_terminals", 0) + 1
    if synthetic.get("terminal_recovery_attempted"):
        dest["attempted"] = dest.get("attempted", 0) + 1
        if synthetic.get("terminal_recovery_succeeded"):
            dest["succeeded"] = dest.get("succeeded", 0) + 1
        else:
            dest["failed"] = dest.get("failed", 0) + 1
    else:
        dest["not_attempted"] = dest.get("not_attempted", 0) + 1
    flags = classification.get("validation_flags") if isinstance(classification.get("validation_flags"), list) else []
    if "terminal_recovery_unhealthy" in flags:
        dest["unhealthy"] = dest.get("unhealthy", 0) + 1


def _terminal_recovery_text(raw: Any) -> str:
    if not isinstance(raw, dict) or int(raw.get("synthetic_terminals") or 0) <= 0:
        return ""
    parts = [f"synthetic:{int(raw.get('synthetic_terminals') or 0)}"]
    for key in ("attempted", "succeeded", "failed", "not_attempted", "unhealthy"):
        value = int(raw.get(key) or 0)
        if value > 0:
            parts.append(f"{key}:{value}")
    return ",".join(parts)


def _screen_stall_summary_text(raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        return []
    parts: list[str] = []
    by_screen = _counts_text(raw.get("by_screen"))
    if by_screen:
        parts.append(f"screen_stalls={by_screen}")
    by_reason = _counts_text(raw.get("by_reason"))
    if by_reason:
        parts.append(f"screen_reasons={by_reason}")
    return parts


def _act1_boss_pristine_clear(boss: dict[str, Any], grade: str) -> bool:
    if not boss.get("cleared"):
        return False
    if "prefix_pristine_clear" in boss:
        return bool(boss.get("prefix_pristine_clear"))
    return grade == "pristine"


def _non_pristine_clear_blockers(classification: dict[str, Any], boss: dict[str, Any]) -> list[str]:
    blockers = boss.get("prefix_blockers") if isinstance(boss.get("prefix_blockers"), list) else []
    if blockers:
        return [f"prefix:{blocker}" for blocker in sorted(set(str(blocker) for blocker in blockers))]
    result = []
    grade = classification.get("validation_grade")
    if grade and grade != "pristine":
        result.append(f"grade:{grade}")
    for flag in classification.get("validation_flags") or []:
        result.append(f"flag:{flag}")
    if int(classification.get("recovered_actions") or 0) > 0:
        result.append("recovered_actions")
    if int(classification.get("failed_actions") or 0) > 0:
        result.append("failed_actions")
    return sorted(set(str(item) for item in result if str(item)))


def _sorted_counts(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(((str(key), int(value)) for key, value in counts.items() if int(value) > 0), key=lambda item: (-item[1], item[0])))


def summarize_log(path: Path, *, knowledge: StaticKnowledge | None = None) -> dict[str, Any]:
    records, skipped_malformed_tail = _read_live_records(path)
    classification, records = classify_records(path, records, knowledge=knowledge)
    shadow_quality = shadow_quality_for_records(path, records, classification, knowledge=knowledge)
    label_quality = shadow_quality["shadow_label_quality"]
    feature_coverage = shadow_quality["shadow_feature_coverage"]
    latest_state = _latest_state(records)
    latest_combat = _combat_summary(latest_state)
    terminal = classification.victory is not None or latest_state.get("screen_type") == "GAME_OVER"
    state = "terminal" if terminal else "running" if latest_state.get("in_game") else "incomplete"
    boss = ((classification.validation_evidence or {}).get("act1_boss") or {})
    status = {
        "path": str(path),
        "state": state,
        "classification": asdict(classification),
        "records": len(records),
        "skipped_malformed_tail_lines": skipped_malformed_tail,
        "last_step": records[-1].get("step") if records else None,
        "last_event": records[-1].get("event") if records else None,
        "latest": {
            "in_game": latest_state.get("in_game"),
            "screen_type": latest_state.get("screen_type"),
            "room_phase": latest_state.get("room_phase"),
            "floor": latest_state.get("floor"),
            "act": latest_state.get("act"),
            "character": latest_state.get("class"),
            "ascension": latest_state.get("ascension_level"),
            "current_hp": latest_state.get("current_hp"),
            "max_hp": latest_state.get("max_hp"),
            "gold": latest_state.get("gold"),
            "combat": latest_combat,
        },
        "act1_boss": boss,
        "shadow_feature_coverage": feature_coverage,
        "shadow_label_quality": label_quality,
        "terminal": {
            "is_terminal": terminal,
            "victory": classification.victory,
            "score": classification.score,
        },
    }
    status["status_line"] = status_line(status)
    return status


def status_line(status: dict[str, Any]) -> str:
    latest = status.get("latest") or {}
    classification = status.get("classification") or {}
    boss = status.get("act1_boss") if isinstance(status.get("act1_boss"), dict) else {}
    combat = latest.get("combat") if isinstance(latest.get("combat"), dict) else {}
    hp = _hp_text(latest)
    boss_text = _boss_text(boss)
    combat_text = _combat_text(combat)
    failure_text = _failure_text(classification)
    flags_text = _flags_text(classification)
    evidence_text = _failure_evidence_text(classification)
    classification_text = _classification_text(classification)
    classification_suffix = f" category={classification_text}" if classification_text else ""
    recovery_text = _action_recovery_text(classification.get("action_recovery_summary"))
    recovery_suffix = f" recovery={recovery_text}" if recovery_text else ""
    label_exclusion_text = _label_exclusion_text(status.get("shadow_label_quality"))
    label_text = f" label_exclusions={label_exclusion_text}" if label_exclusion_text else ""
    feature_unknown_text = _feature_unknown_text(status.get("shadow_feature_coverage"))
    feature_text = f" feature_unknown={feature_unknown_text}" if feature_unknown_text else ""
    tail_text = f" skipped_tail={status.get('skipped_malformed_tail_lines')}" if status.get("skipped_malformed_tail_lines") else ""
    return (
        f"run_status {status.get('state')}: "
        f"floor={latest.get('floor')} act={latest.get('act')} "
        f"screen={latest.get('screen_type')} phase={latest.get('room_phase')} "
        f"step={status.get('last_step')} hp={hp} "
        f"actions={classification.get('action_records', 0)} "
        f"recovered={classification.get('recovered_actions', 0)} "
        f"failed={classification.get('failed_actions', 0)} "
        f"validation={classification.get('validation_grade')}"
        f"{classification_suffix}"
        f"{failure_text}{flags_text}"
        f"{evidence_text}"
        f"{recovery_suffix}"
        f"{label_text}{feature_text}{combat_text}{boss_text}{tail_text}"
    )


def _classification_text(classification: dict[str, Any]) -> str:
    category = str(classification.get("category") or "")
    reason = str(classification.get("reason") or "")
    if category and reason:
        return f"{category}/{reason}"
    return category or reason


def _action_recovery_text(action_recovery: Any) -> str:
    if not isinstance(action_recovery, dict):
        return ""
    recovery_text = _counts_text(action_recovery.get("by_kind"))
    unrecovered = int(action_recovery.get("unrecovered") or 0)
    if not recovery_text and unrecovered <= 0:
        return ""
    parts = []
    if recovery_text:
        parts.append(recovery_text)
    if unrecovered > 0:
        parts.append(f"unrecovered:{unrecovered}")
    return ",".join(parts)


def _latest_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for record in records:
        state = record.get("state")
        if isinstance(state, dict):
            latest = state
    return latest


def _read_live_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    skipped_tail = 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                skipped_tail += 1
                continue
            raise
        if isinstance(record, dict):
            records.append(record)
    return records, skipped_tail


def _combat_summary(state: dict[str, Any]) -> dict[str, Any]:
    combat = state.get("combat") or state.get("combat_state") or {}
    if not isinstance(combat, dict) or not combat:
        return {}
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    return {
        "turn": combat.get("turn"),
        "incoming_damage": combat.get("incoming_damage"),
        "energy": ((combat.get("player") or {}).get("current_energy") if isinstance(combat.get("player"), dict) else None),
        "hand_size": len(combat.get("hand_cards") or combat.get("hand") or []),
        "enemies": [
            {
                "id": monster.get("id") or monster.get("name"),
                "hp": monster.get("hp"),
                "max_hp": monster.get("max_hp"),
                "intent": monster.get("intent"),
            }
            for monster in monsters
            if isinstance(monster, dict)
        ],
    }


def _hp_text(latest: dict[str, Any]) -> str:
    current = latest.get("current_hp")
    maximum = latest.get("max_hp")
    if current is None and maximum is None:
        return "?"
    return f"{current}/{maximum}"


def _combat_text(combat: dict[str, Any]) -> str:
    if not combat:
        return ""
    enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
    enemy = enemies[0] if enemies else {}
    enemy_text = ""
    if enemy:
        enemy_text = f" enemy={enemy.get('id')}:{enemy.get('hp')}/{enemy.get('max_hp')}:{enemy.get('intent')}"
    return f" combat=T{combat.get('turn')},incoming={combat.get('incoming_damage')}{enemy_text}"


def _boss_text(boss: dict[str, Any]) -> str:
    if not boss or not boss.get("reached"):
        return ""
    state = "cleared" if boss.get("cleared") else "reached"
    enemies = boss.get("enemy_ids") if isinstance(boss.get("enemy_ids"), list) else []
    enemy = enemies[0] if enemies else "?"
    details = []
    if boss.get("last_turn") is not None:
        details.append(f"T{boss.get('last_turn')}")
    if boss.get("last_hp") is not None:
        details.append(f"player_hp={boss.get('last_hp')}")
    if boss.get("cleared"):
        if boss.get("clear_step") is not None:
            details.append(f"clear_step={boss.get('clear_step')}")
        if "prefix_pristine_clear" in boss:
            details.append(f"prefix_pristine={str(bool(boss.get('prefix_pristine_clear'))).lower()}")
        blockers = boss.get("prefix_blockers") if isinstance(boss.get("prefix_blockers"), list) else []
        if blockers:
            visible = [str(blocker) for blocker in blockers[:2]]
            if len(blockers) > len(visible):
                visible.append(f"+{len(blockers) - len(visible)}")
            details.append("prefix_blockers=" + "|".join(visible))
    extra = "[" + ",".join(details) + "]" if details else ""
    return f" act1_boss={state}:{enemy}{extra}"


def _failure_text(classification: dict[str, Any]) -> str:
    attribution = classification.get("failure_attribution")
    if not attribution:
        return ""
    return f" failure={attribution}"


def _flags_text(classification: dict[str, Any]) -> str:
    flags = classification.get("validation_flags")
    if not isinstance(flags, list) or not flags:
        return ""
    visible = [str(flag) for flag in flags[:3]]
    if len(flags) > len(visible):
        visible.append(f"+{len(flags) - len(visible)}")
    return " flags=" + ",".join(visible)


def _failure_evidence_text(classification: dict[str, Any]) -> str:
    evidence = classification.get("failure_evidence")
    if not isinstance(evidence, dict) or not evidence:
        return ""
    parts = []
    mcp_read = evidence.get("mcp_read") if isinstance(evidence.get("mcp_read"), dict) else {}
    if mcp_read:
        parts.append(_mcp_read_text(mcp_read))
    synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
    if synthetic or evidence.get("terminal_outcome_source"):
        parts.append(_synthetic_terminal_text(synthetic, evidence.get("terminal_outcome_source")))
    action_error = evidence.get("action_error") if isinstance(evidence.get("action_error"), dict) else {}
    if action_error:
        parts.append(_action_error_text(action_error))
    screen_stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
    if screen_stall:
        parts.append(_screen_stall_text(screen_stall))
    return " evidence=" + ";".join(part for part in parts if part) if parts else ""


def _mcp_read_text(read: dict[str, Any]) -> str:
    text = "mcp_read"
    if read.get("step") is not None:
        text += f"@{read.get('step')}"
    details = []
    if read.get("diagnostics_status") is not None:
        details.append(f"status={read.get('diagnostics_status')}")
    state = read.get("last_state") if isinstance(read.get("last_state"), dict) else {}
    if state:
        state_parts = [str(state.get("screen_type") or "?")]
        if state.get("floor") is not None:
            state_parts.append(f"F{state.get('floor')}")
        if state.get("room_phase"):
            state_parts.append(str(state.get("room_phase")))
        details.append("/".join(state_parts))
    if details:
        text += "[" + ",".join(details[:2]) + "]"
    return text


def _synthetic_terminal_text(synthetic: dict[str, Any], outcome_source: Any) -> str:
    text = "synthetic_terminal"
    if synthetic.get("step") is not None:
        text += f"@{synthetic.get('step')}"
    details = []
    if outcome_source:
        details.append(f"outcome={outcome_source}")
    if "terminal_recovery_succeeded" in synthetic:
        details.append(f"recover={'ok' if synthetic.get('terminal_recovery_succeeded') else 'failed'}")
    if synthetic.get("post_recovery_status") is not None:
        details.append(f"post={synthetic.get('post_recovery_status')}")
    if synthetic.get("source") is not None:
        details.append(f"source={synthetic.get('source')}")
    if details:
        text += "[" + ",".join(str(item) for item in details[:3]) + "]"
    return text


def _action_error_text(action: dict[str, Any]) -> str:
    kind = action.get("kind") or action.get("action_status") or "action"
    text = f"action_error={kind}"
    if action.get("step") is not None:
        text += f"@{action.get('step')}"
    return text


def _screen_stall_text(stall: dict[str, Any]) -> str:
    screen = stall.get("screen_type") or "screen"
    text = f"screen_stall={screen}"
    if stall.get("repeat_count") is not None:
        text += f"x{stall.get('repeat_count')}"
    details = []
    if stall.get("floor") is not None:
        details.append(f"F{stall.get('floor')}")
    if stall.get("first_step") is not None or stall.get("last_step") is not None:
        details.append(f"steps={stall.get('first_step')}->{stall.get('last_step')}")
    for key, label in (
        ("last_relic_count", "relics"),
        ("last_reward_count", "rewards"),
        ("last_card_count", "cards"),
    ):
        if stall.get(key) is not None:
            details.append(f"{label}={stall.get(key)}")
            break
    if stall.get("chest_open") is not None:
        details.append(f"chest_open={str(bool(stall.get('chest_open'))).lower()}")
    action_text = _screen_stall_action_text(stall.get("last_actions"))
    if action_text:
        details.append(f"action={action_text}")
    if details:
        text += "[" + ",".join(details[:5]) + "]"
    return text


def _screen_stall_action_text(raw: Any) -> str:
    actions = raw if isinstance(raw, list) else []
    if not actions:
        return ""
    first = actions[0] if isinstance(actions[0], dict) else {}
    action = first.get("action")
    if not action:
        return ""
    if first.get("choice_index") is not None:
        return f"{action}:{first.get('choice_index')}"
    return str(action)


def _write_ascii_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
