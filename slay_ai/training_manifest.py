"""Build a clean training manifest and shadow-model examples from run logs."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .combat_label_audit import compact_excluded_label_examples
from .combat_search import find_best_combat_sequence
from .readiness import act1_readiness
from .shadow_feature_audit import audit_shadow_examples
from .static_knowledge import StaticKnowledge


CLEAN_TRAINABLE = "clean_trainable"
DIAGNOSTIC_EXCLUDED = "diagnostic_excluded"
INFRA_BLOCKED = "infra_blocked"
STARTER_DECKS = {
    "IRONCLAD": ["Strike_R", "Strike_R", "Strike_R", "Strike_R", "Strike_R", "Defend_R", "Defend_R", "Defend_R", "Defend_R", "Bash"],
    "SILENT": ["Strike_G", "Strike_G", "Strike_G", "Strike_G", "Strike_G", "Defend_G", "Defend_G", "Defend_G", "Defend_G", "Defend_G", "Neutralize", "Survivor"],
    "DEFECT": ["Strike_B", "Strike_B", "Strike_B", "Strike_B", "Defend_B", "Defend_B", "Defend_B", "Defend_B", "Zap", "Dualcast"],
    "WATCHER": ["Strike_P", "Strike_P", "Strike_P", "Strike_P", "Defend_P", "Defend_P", "Defend_P", "Defend_P", "Eruption", "Vigilance"],
}
REWARD_STALL_SCREENS = {"COMBAT_REWARD", "CARD_REWARD", "BOSS_REWARD", "CHEST"}
FAILURE_EVIDENCE_TYPE_KEYS = (
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
)


@dataclass
class LogClassification:
    path: str
    category: str
    reason: str
    character: str | None = None
    ascension: int | None = None
    floor: int = 0
    victory: bool | None = None
    score: int | None = None
    steps: int = 0
    card_picks: int = 0
    action_records: int = 0
    recovered_actions: int = 0
    failed_actions: int = 0
    action_recovery_summary: dict[str, Any] = field(default_factory=dict)
    failure_attribution: str | None = None
    failure_tags: list[str] = field(default_factory=list)
    failure_evidence: dict[str, Any] = field(default_factory=dict)
    validation_grade: str | None = None
    validation_flags: list[str] = field(default_factory=list)
    validation_evidence: dict[str, Any] = field(default_factory=dict)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify JSONL logs before offline learning.")
    parser.add_argument("logs", nargs="*", type=Path, default=[Path("runs") / "ai_runs"])
    parser.add_argument("--output", type=Path, default=Path("data") / "training_log_manifest.json")
    parser.add_argument(
        "--refresh-from",
        type=Path,
        help="Rebuild an existing manifest from its resolved_logs, falling back to inputs when needed.",
    )
    parser.add_argument("--shadow-dir", type=Path, help="Optional directory for route/potion/pre-boss/search JSONL examples.")
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        help="Optional static knowledge directory for enriched shadow features.",
    )
    parser.add_argument("--print-clean", action="store_true", help="Print clean trainable log paths, one per line.")
    args = parser.parse_args(argv)

    knowledge = StaticKnowledge.load(args.knowledge_dir) if args.knowledge_dir else None
    if args.refresh_from:
        manifest, shadow_examples = refresh_manifest_from_existing(
            args.refresh_from,
            knowledge=knowledge,
            knowledge_dir=args.knowledge_dir,
        )
    else:
        manifest, shadow_examples = build_manifest(args.logs, knowledge=knowledge, knowledge_dir=args.knowledge_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.shadow_dir:
        write_shadow_examples(args.shadow_dir, shadow_examples)
    if args.print_clean:
        for item in manifest["categories"][CLEAN_TRAINABLE]:
            print(item["path"])
    else:
        summary = manifest["summary"]
        action = "Refreshed" if args.refresh_from else "Classified"
        print(
            f"{action} "
            f"{summary['total_logs']} logs: "
            f"{summary[CLEAN_TRAINABLE]} clean, "
            f"{summary[DIAGNOSTIC_EXCLUDED]} diagnostic, "
            f"{summary[INFRA_BLOCKED]} infra."
        )
        if args.refresh_from:
            refresh_source = manifest.get("refresh_source") or {}
            print(
                "Refresh source: "
                f"{args.refresh_from} ({refresh_source.get('source', 'unknown')}, "
                f"{refresh_source.get('path_count', 0)} paths)"
            )
        print(f"Wrote manifest: {args.output}")
    return 0


def build_manifest(
    paths: Iterable[Path],
    *,
    knowledge: StaticKnowledge | None = None,
    knowledge_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    input_paths = [Path(path) for path in paths]
    resolved_log_paths = list(iter_log_files(input_paths))
    categories: dict[str, list[dict[str, Any]]] = {
        CLEAN_TRAINABLE: [],
        DIAGNOSTIC_EXCLUDED: [],
        INFRA_BLOCKED: [],
    }
    shadow_examples: dict[str, list[dict[str, Any]]] = {
        "route_risk": [],
        "potion_tempo": [],
        "pre_boss_deck_quality": [],
        "combat_search_labels": [],
    }

    for path in resolved_log_paths:
        classification, records = classify_log(path, knowledge=knowledge)
        categories[classification.category].append(asdict(classification))
        if classification.category != CLEAN_TRAINABLE:
            continue
        card_aliases = _observed_card_aliases(records, knowledge)
        shadow_examples["route_risk"].extend(_route_risk_examples(path, records, classification, knowledge, card_aliases))
        shadow_examples["potion_tempo"].extend(_potion_tempo_examples(path, records, classification, knowledge))
        shadow_examples["pre_boss_deck_quality"].extend(
            _pre_boss_examples(path, records, classification, knowledge, card_aliases)
        )
        shadow_examples["combat_search_labels"].extend(
            _combat_search_label_examples(path, records, classification, knowledge, card_aliases)
        )

    warnings = _manifest_warnings(resolved_log_paths)
    summary = {
        "input_count": len(input_paths),
        "resolved_log_count": len(resolved_log_paths),
        "warnings": warnings,
        "total_logs": sum(len(items) for items in categories.values()),
        CLEAN_TRAINABLE: len(categories[CLEAN_TRAINABLE]),
        DIAGNOSTIC_EXCLUDED: len(categories[DIAGNOSTIC_EXCLUDED]),
        INFRA_BLOCKED: len(categories[INFRA_BLOCKED]),
        "shadow_examples": {key: len(value) for key, value in shadow_examples.items()},
        "shadow_feature_coverage": audit_shadow_examples(shadow_examples),
        "shadow_label_quality": _shadow_label_quality_summary(shadow_examples),
        "classification_reasons": _classification_reason_counts(categories),
        "failure_attributions": _failure_attribution_counts(categories),
        "failure_evidence": _failure_evidence_summary(categories),
        "action_recovery": _action_recovery_manifest_summary(categories),
        "terminal_recovery": _terminal_recovery_summary(categories),
        "validation": _validation_summary(categories),
    }
    manifest = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": [str(path) for path in input_paths],
        "resolved_logs": [str(path) for path in resolved_log_paths],
        "warnings": warnings,
        "summary": summary,
        "categories": categories,
    }
    if knowledge is not None:
        manifest["static_knowledge"] = {
            "dir": str(knowledge_dir) if knowledge_dir is not None else None,
            "source_counts": knowledge.source_counts,
        }
    return manifest, shadow_examples


def refresh_manifest_from_existing(
    manifest_path: Path,
    *,
    knowledge: StaticKnowledge | None = None,
    knowledge_dir: Path | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    existing = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    source, paths = _refresh_source_paths(existing)
    manifest, shadow_examples = build_manifest(paths, knowledge=knowledge, knowledge_dir=knowledge_dir)
    manifest["refresh_source"] = {
        "manifest_path": str(manifest_path),
        "source": source,
        "path_count": len(paths),
        "touches_live_mcp": False,
        "trains_models": False,
    }
    return manifest, shadow_examples


def _refresh_source_paths(manifest: dict[str, Any]) -> tuple[str, list[Path]]:
    resolved_logs = _path_list(manifest.get("resolved_logs"))
    if resolved_logs:
        return "resolved_logs", resolved_logs
    inputs = _path_list(manifest.get("inputs"))
    if inputs:
        return "inputs", inputs
    category_paths = _category_path_list(manifest.get("categories"))
    if category_paths:
        return "category_paths", category_paths
    return "empty", []


def _path_list(raw: Any) -> list[Path]:
    if not isinstance(raw, list):
        return []
    return [Path(path) for path in raw if str(path or "").strip()]


def _category_path_list(raw: Any) -> list[Path]:
    if not isinstance(raw, dict):
        return []
    result: list[Path] = []
    seen: set[str] = set()
    for category in (CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED):
        rows = raw.get(category)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = str(row.get("path") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(Path(text))
    return result


def classify_log(path: Path, *, knowledge: StaticKnowledge | None = None) -> tuple[LogClassification, list[dict[str, Any]]]:
    try:
        records = _read_records(path)
    except json.JSONDecodeError as exc:
        return (
            LogClassification(
                path=str(path),
                category=INFRA_BLOCKED,
                reason=f"json_decode_error:{exc.lineno}",
                failure_attribution="logging_infra",
                failure_tags=["infra", "logging"],
                failure_evidence={"json_error_line": exc.lineno},
            ),
            [],
        )
    return classify_records(path, records, knowledge=knowledge)


def classify_records(
    path: Path,
    records: list[dict[str, Any]],
    *,
    knowledge: StaticKnowledge | None = None,
) -> tuple[LogClassification, list[dict[str, Any]]]:
    if not records:
        return LogClassification(path=str(path), category=DIAGNOSTIC_EXCLUDED, reason="empty_log"), []

    latest_state = _latest_state(records)
    outcome = _latest_outcome(records)
    victory = outcome.get("victory")
    if victory is None and latest_state.get("screen_type") == "GAME_OVER":
        victory = False
    if victory is not None:
        victory = bool(victory)

    action_records = [record for record in records if record.get("event") == "action_result"]
    failed_actions = [
        record
        for record in action_records
        if str(record.get("action_status") or "").lower() in {"failed", "action_failed"}
    ]
    recovered_actions = [record for record in action_records if _is_recovered_action_race(record)]
    unrecovered_action_errors = [record for record in action_records if _is_unrecovered_action_race(record)]
    action_recovery_summary = _action_recovery_summary(action_records, records=records)
    infra_error_reason = _infra_error_reason(records)
    screen_stall = _reward_screen_stall_evidence(records)
    card_picks = sum(1 for record in records if (record.get("decision") or {}).get("learn_card_pick"))

    base = LogClassification(
        path=str(path),
        category=CLEAN_TRAINABLE,
        reason="completed_clean",
        character=latest_state.get("class"),
        ascension=_optional_int(latest_state.get("ascension_level")),
        floor=_safe_int(latest_state.get("floor")),
        victory=victory,
        score=outcome.get("score"),
        steps=_safe_int(records[-1].get("step")),
        card_picks=card_picks,
        action_records=len(action_records),
        recovered_actions=len(recovered_actions),
        failed_actions=len(failed_actions),
        action_recovery_summary=action_recovery_summary,
    )
    if failed_actions:
        base.category = INFRA_BLOCKED
        base.reason = "failed_action"
    elif unrecovered_action_errors:
        base.category = INFRA_BLOCKED
        base.reason = "unrecovered_action_race"
    elif infra_error_reason:
        base.category = INFRA_BLOCKED
        base.reason = infra_error_reason
    elif victory is None and screen_stall:
        base.category = INFRA_BLOCKED
        base.reason = str(screen_stall["reason"])
        base.failure_attribution = "mcp_execution"
        base.failure_tags = _reward_screen_stall_tags(str(screen_stall["screen_type"]))
        base.failure_evidence = {"reason": base.reason, "screen_stall": screen_stall}
    elif outcome.get("source") == "synthetic_after_main_menu":
        base.category = DIAGNOSTIC_EXCLUDED
        base.reason = "synthetic_after_main_menu"
    elif victory is None:
        base.category = DIAGNOSTIC_EXCLUDED
        base.reason = "no_terminal_outcome"
    _annotate_failure_attribution(base, records, knowledge)
    _annotate_validation(base, records, knowledge)
    return base, records


def _classification_reason_counts(categories: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for category in (CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED):
        counts: dict[str, int] = {}
        for row in categories.get(category, []):
            reason = str(row.get("reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
        result[category] = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return result


def _failure_attribution_counts(categories: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rows in categories.values():
        for row in rows:
            attribution = row.get("failure_attribution")
            if not attribution:
                continue
            counts[attribution] = counts.get(attribution, 0) + 1
    return dict(sorted(counts.items()))


def _failure_evidence_summary(categories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    screen_by_screen: dict[str, int] = {}
    screen_by_reason: dict[str, int] = {}
    action_by_kind: dict[str, int] = {}
    action_by_status: dict[str, int] = {}
    mcp_by_event: dict[str, int] = {}
    mcp_by_status: dict[str, int] = {}
    synthetic_by_source: dict[str, int] = {}
    terminal_sources: dict[str, int] = {}
    runs_with_evidence = 0

    for rows in categories.values():
        for row in rows:
            evidence = row.get("failure_evidence") if isinstance(row.get("failure_evidence"), dict) else {}
            if not evidence:
                continue
            runs_with_evidence += 1
            matched_type = False
            for key in FAILURE_EVIDENCE_TYPE_KEYS:
                value = evidence.get(key)
                if value in (None, "", [], {}):
                    continue
                matched_type = True
                by_type[key] = by_type.get(key, 0) + 1
            if not matched_type and evidence.get("reason"):
                by_type["reason_only"] = by_type.get("reason_only", 0) + 1

            stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
            if stall:
                screen = str(stall.get("screen_type") or "unknown_screen")
                screen_by_screen[screen] = screen_by_screen.get(screen, 0) + 1
                reason = str(stall.get("reason") or evidence.get("reason") or row.get("reason") or "unknown")
                screen_by_reason[reason] = screen_by_reason.get(reason, 0) + 1

            action = evidence.get("action_error") if isinstance(evidence.get("action_error"), dict) else {}
            if action:
                kind = str(action.get("kind") or "unknown_action_error")
                status = str(action.get("action_status") or "unknown_status")
                action_by_kind[kind] = action_by_kind.get(kind, 0) + 1
                action_by_status[status] = action_by_status.get(status, 0) + 1

            read = evidence.get("mcp_read") if isinstance(evidence.get("mcp_read"), dict) else {}
            if read:
                event = str(read.get("event") or "unknown_event")
                status = str(read.get("diagnostics_status") or "unknown_status")
                mcp_by_event[event] = mcp_by_event.get(event, 0) + 1
                mcp_by_status[status] = mcp_by_status.get(status, 0) + 1

            synthetic = (
                evidence.get("synthetic_terminal")
                if isinstance(evidence.get("synthetic_terminal"), dict)
                else {}
            )
            if synthetic:
                source = str(synthetic.get("source") or "unknown_source")
                synthetic_by_source[source] = synthetic_by_source.get(source, 0) + 1
            terminal_source = evidence.get("terminal_outcome_source")
            if terminal_source:
                source = str(terminal_source)
                terminal_sources[source] = terminal_sources.get(source, 0) + 1

    result: dict[str, Any] = {}
    if runs_with_evidence:
        result["runs_with_evidence"] = runs_with_evidence
    if by_type:
        result["by_type"] = _sorted_count_dict(by_type)
    screen_stalls: dict[str, Any] = {}
    if screen_by_screen:
        screen_stalls["by_screen"] = _sorted_count_dict(screen_by_screen)
    if screen_by_reason:
        screen_stalls["by_reason"] = _sorted_count_dict(screen_by_reason)
    if screen_stalls:
        result["screen_stalls"] = screen_stalls
    action_errors: dict[str, Any] = {}
    if action_by_kind:
        action_errors["by_kind"] = _sorted_count_dict(action_by_kind)
    if action_by_status:
        action_errors["by_status"] = _sorted_count_dict(action_by_status)
    if action_errors:
        result["action_errors"] = action_errors
    mcp_reads: dict[str, Any] = {}
    if mcp_by_event:
        mcp_reads["by_event"] = _sorted_count_dict(mcp_by_event)
    if mcp_by_status:
        mcp_reads["by_diagnostics_status"] = _sorted_count_dict(mcp_by_status)
    if mcp_reads:
        result["mcp_reads"] = mcp_reads
    synthetic_terminals: dict[str, Any] = {}
    if synthetic_by_source:
        synthetic_terminals["by_source"] = _sorted_count_dict(synthetic_by_source)
    if terminal_sources:
        synthetic_terminals["terminal_outcome_sources"] = _sorted_count_dict(terminal_sources)
    if synthetic_terminals:
        result["synthetic_terminals"] = synthetic_terminals
    return result


def _action_recovery_manifest_summary(categories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    total = 0
    recovered = 0
    unrecovered = 0
    runs_with_action_recovery = 0
    runs_with_recovered_action = 0
    runs_with_unrecovered_action = 0
    for category, rows in categories.items():
        for row in rows:
            summary = row.get("action_recovery_summary") if isinstance(row.get("action_recovery_summary"), dict) else {}
            row_total = int(summary.get("total") or 0)
            row_recovered = int(summary.get("recovered") or 0)
            row_unrecovered = int(summary.get("unrecovered") or 0)
            total += row_total
            recovered += row_recovered
            unrecovered += row_unrecovered
            if row_total > 0:
                runs_with_action_recovery += 1
            if row_recovered > 0:
                runs_with_recovered_action += 1
            if row_unrecovered > 0:
                runs_with_unrecovered_action += 1
            _merge_count_dict(by_status, summary.get("by_status"))
            _merge_count_dict(by_kind, summary.get("by_kind"))
            for example in summary.get("examples") or []:
                if not isinstance(example, dict):
                    continue
                enriched = dict(example)
                if row.get("path") is not None:
                    enriched["source_log"] = row.get("path")
                enriched["source_category"] = category
                examples.append(enriched)
    if total <= 0:
        return {}
    result = {
        "total": total,
        "recovered": recovered,
        "unrecovered": unrecovered,
        "runs_with_action_recovery": runs_with_action_recovery,
        "runs_with_recovered_action": runs_with_recovered_action,
        "runs_with_unrecovered_action": runs_with_unrecovered_action,
        "by_status": _sorted_count_dict(by_status),
        "by_kind": _sorted_count_dict(by_kind),
    }
    if examples:
        result["examples"] = examples[:8]
    return result


def _merge_count_dict(dest: dict[str, int], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        count = int(value or 0)
        if count > 0:
            key_text = str(key)
            dest[key_text] = dest.get(key_text, 0) + count


def _shadow_label_quality_summary(shadow_examples: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    combat_rows = shadow_examples.get("combat_search_labels") or []
    missed_direct_kill = sum(1 for row in combat_rows if row.get("label_missed_direct_kill"))
    missed_single_card_search = sum(1 for row in combat_rows if row.get("label_missed_single_card_search"))
    direct_kill_available = sum(1 for row in combat_rows if row.get("direct_kill_available"))
    reasons = {}
    if missed_direct_kill:
        reasons["missed_direct_kill"] = missed_direct_kill
    if missed_single_card_search:
        reasons["missed_single_card_search"] = missed_single_card_search
    excluded = missed_direct_kill + missed_single_card_search
    combat_summary: dict[str, Any] = {
        "total": len(combat_rows),
        "trainable": len(combat_rows) - excluded,
        "excluded_from_training": excluded,
        "direct_kill_available": direct_kill_available,
        "missed_single_card_search": missed_single_card_search,
        "exclusion_reasons": reasons,
    }
    examples = compact_excluded_label_examples(combat_rows, limit=5)
    if examples:
        combat_summary["exclusion_examples"] = examples
    return {"combat_search_labels": combat_summary}


def shadow_label_quality_for_records(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
) -> dict[str, Any]:
    shadow_examples = {"combat_search_labels": []}
    if final.category == CLEAN_TRAINABLE:
        shadow_examples["combat_search_labels"] = _combat_search_label_examples(
            path,
            records,
            final,
            knowledge,
            _observed_card_aliases(records, knowledge),
        )
    return _shadow_label_quality_summary(shadow_examples)


def shadow_quality_for_records(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
) -> dict[str, Any]:
    shadow_examples = _shadow_examples_for_records(path, records, final, knowledge)
    return {
        "shadow_feature_coverage": audit_shadow_examples(shadow_examples),
        "shadow_label_quality": _shadow_label_quality_summary(shadow_examples),
    }


def shadow_feature_coverage_for_records(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
) -> dict[str, Any]:
    return shadow_quality_for_records(path, records, final, knowledge)["shadow_feature_coverage"]


def _shadow_examples_for_records(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
) -> dict[str, list[dict[str, Any]]]:
    shadow_examples: dict[str, list[dict[str, Any]]] = {
        "route_risk": [],
        "potion_tempo": [],
        "pre_boss_deck_quality": [],
        "combat_search_labels": [],
    }
    if final.category == CLEAN_TRAINABLE:
        card_aliases = _observed_card_aliases(records, knowledge)
        shadow_examples["route_risk"] = _route_risk_examples(path, records, final, knowledge, card_aliases)
        shadow_examples["potion_tempo"] = _potion_tempo_examples(path, records, final, knowledge)
        shadow_examples["pre_boss_deck_quality"] = _pre_boss_examples(path, records, final, knowledge, card_aliases)
        shadow_examples["combat_search_labels"] = _combat_search_label_examples(path, records, final, knowledge, card_aliases)
    return shadow_examples


def _validation_summary(categories: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_grade: dict[str, int] = {}
    by_flag: dict[str, int] = {}
    act1_boss_prefix_blockers: dict[str, int] = {}
    act1_boss_clear_blockers: dict[str, int] = {}
    act1_boss_reached = 0
    act1_boss_cleared = 0
    pristine_act1_boss_cleared = 0
    for rows in categories.values():
        for row in rows:
            grade = str(row.get("validation_grade") or "unknown")
            by_grade[grade] = by_grade.get(grade, 0) + 1
            for flag in row.get("validation_flags") or []:
                flag_text = str(flag)
                by_flag[flag_text] = by_flag.get(flag_text, 0) + 1
            boss = ((row.get("validation_evidence") or {}).get("act1_boss") or {})
            prefix_blockers = boss.get("prefix_blockers") if isinstance(boss.get("prefix_blockers"), list) else []
            for blocker in prefix_blockers:
                blocker_text = str(blocker)
                act1_boss_prefix_blockers[blocker_text] = act1_boss_prefix_blockers.get(blocker_text, 0) + 1
            if boss.get("reached"):
                act1_boss_reached += 1
            if boss.get("cleared"):
                act1_boss_cleared += 1
                if _act1_boss_pristine_clear_for_summary(boss, grade):
                    pristine_act1_boss_cleared += 1
                else:
                    for blocker in prefix_blockers:
                        blocker_text = str(blocker)
                        act1_boss_clear_blockers[blocker_text] = act1_boss_clear_blockers.get(blocker_text, 0) + 1
    return {
        "by_grade": _sorted_count_dict(by_grade),
        "by_flag": _sorted_count_dict(by_flag),
        "act1_boss_reached": act1_boss_reached,
        "act1_boss_cleared": act1_boss_cleared,
        "pristine_act1_boss_cleared": pristine_act1_boss_cleared,
        "act1_boss_prefix_blockers": _sorted_count_dict(act1_boss_prefix_blockers),
        "act1_boss_clear_blockers": _sorted_count_dict(act1_boss_clear_blockers),
    }


def _terminal_recovery_summary(categories: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    counts = {
        "synthetic_terminals": 0,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "not_attempted": 0,
        "unhealthy": 0,
    }
    for rows in categories.values():
        for row in rows:
            evidence = row.get("failure_evidence") if isinstance(row.get("failure_evidence"), dict) else {}
            synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
            has_synthetic = bool(synthetic or evidence.get("terminal_outcome_source"))
            if not has_synthetic:
                continue
            counts["synthetic_terminals"] += 1
            if synthetic.get("terminal_recovery_attempted"):
                counts["attempted"] += 1
                if synthetic.get("terminal_recovery_succeeded"):
                    counts["succeeded"] += 1
                else:
                    counts["failed"] += 1
            else:
                counts["not_attempted"] += 1
            post_status = synthetic.get("post_recovery_status")
            if post_status not in (None, "", "healthy"):
                counts["unhealthy"] += 1
    return _sorted_count_dict(counts)


def _sorted_count_dict(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(((key, value) for key, value in counts.items() if value > 0), key=lambda item: (-item[1], item[0])))


def _is_action_race(record: dict[str, Any]) -> bool:
    status = str(record.get("action_status") or "").lower()
    return status in {"recoverable_error", "preflight_mismatch"} or record.get("recovered") is not None


def _is_recovered_action_race(record: dict[str, Any]) -> bool:
    if not _is_action_race(record):
        return False
    if record.get("recovered") is False:
        return False
    status = str(record.get("action_status") or "").lower()
    return bool(record.get("recovered")) or status in {"recoverable_error", "preflight_mismatch"}


def _is_unrecovered_action_race(record: dict[str, Any]) -> bool:
    status = str(record.get("action_status") or "").lower()
    return status in {"recoverable_error", "preflight_mismatch"} and record.get("recovered") is False


def _action_recovery_summary(
    action_records: list[dict[str, Any]],
    *,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    race_records = [record for record in action_records if _is_action_race(record)]
    if not race_records:
        return {}
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    recovered = 0
    unrecovered = 0
    for record in race_records:
        status = str(record.get("action_status") or "unknown").lower()
        by_status[status] = by_status.get(status, 0) + 1
        kind = _action_recovery_kind(record)
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if len(examples) < 5:
            examples.append(_compact_action_recovery_event(record, kind=kind, records=records))
        if record.get("recovered") is False:
            unrecovered += 1
        else:
            recovered += 1
    result = {
        "total": len(race_records),
        "recovered": recovered,
        "unrecovered": unrecovered,
        "by_status": dict(sorted(by_status.items())),
        "by_kind": dict(sorted(by_kind.items())),
    }
    if examples:
        result["examples"] = examples
    return result


def _compact_action_recovery_event(
    record: dict[str, Any],
    *,
    kind: str,
    records: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "step": record.get("step"),
        "action_status": record.get("action_status"),
        "recovered": record.get("recovered"),
        "kind": kind,
    }
    actions = _compact_action_list(record.get("actions"))
    if actions:
        event["actions"] = actions
    executed = _compact_action_list(record.get("executed_actions"))
    if executed:
        event["executed_actions"] = executed
    commands = record.get("available_commands")
    if isinstance(commands, list) and commands:
        event["available_commands"] = list(commands[:8])
    if record.get("last_error"):
        event["last_error"] = record.get("last_error")
    if record.get("rewrite_reason"):
        event["rewrite_reason"] = record.get("rewrite_reason")
    if records is not None:
        state_record = _last_record_with_state(records, before_step=_safe_int(record.get("step")))
        if state_record:
            event["last_state"] = _compact_state_context(state_record)
    return {key: value for key, value in event.items() if value not in (None, "", [], {})}


def _compact_action_list(actions: Any, *, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(actions, list):
        return []
    result: list[dict[str, Any]] = []
    fields = (
        "action",
        "card_index",
        "target_index",
        "choice_index",
        "slot",
        "potion_slot",
        "drop",
    )
    for action in actions[:limit]:
        if not isinstance(action, dict):
            continue
        compact = {field: action[field] for field in fields if action.get(field) not in (None, "", [], {})}
        if isinstance(compact.get("drop"), list):
            compact["drop"] = compact["drop"][:8]
        if compact:
            result.append(compact)
    return result


def _action_recovery_kind(record: dict[str, Any]) -> str:
    rewrite = str(record.get("rewrite_reason") or "").lower()
    error = str(record.get("last_error") or record.get("error") or "").lower()
    status = str(record.get("action_status") or "").lower()
    if "stale targeted use_potion" in error:
        return "stale_potion_target_index"
    if "stale use_potion" in error or "potion_slot" in error:
        return "stale_potion_slot"
    if "stale target_index" in error and "for use_potion" in error:
        return "stale_potion_target_index"
    if "stale target_index" in error or "stale targeted play_card" in error:
        return "stale_target_index"
    if "hand select->choose" in rewrite:
        return "hand_select_to_choose"
    if "grid confirm->proceed" in rewrite:
        return "grid_confirm_to_proceed"
    if "chest choose->proceed" in rewrite:
        return "chest_choose_to_proceed"
    if "unavailable action" in error:
        return "unavailable_action"
    if "invalid command" in error:
        return "invalid_command"
    if status in {"recoverable_error", "preflight_mismatch"}:
        return status
    return "unknown_action_recovery"


def _reward_screen_stall_evidence(records: list[dict[str, Any]], *, min_repeats: int = 3) -> dict[str, Any] | None:
    state_records = [
        (record, record.get("state"))
        for record in records
        if isinstance(record.get("state"), dict)
    ]
    if not state_records:
        return None
    _, latest_state = state_records[-1]
    screen = str(latest_state.get("screen_type") or "")
    if screen not in REWARD_STALL_SCREENS:
        return None

    tail: list[tuple[dict[str, Any], dict[str, Any]]] = []
    floor = _safe_int(latest_state.get("floor"))
    for record, state in reversed(state_records):
        if str(state.get("screen_type") or "") != screen:
            break
        if _safe_int(state.get("floor")) != floor:
            break
        tail.append((record, state))
    tail.reverse()
    if len(tail) < min_repeats:
        return None

    first_record, first_state = tail[0]
    last_record, last_state = tail[-1]
    last_decision = last_record.get("decision") if isinstance(last_record.get("decision"), dict) else {}
    evidence = {
        "reason": _reward_screen_stall_reason(screen),
        "screen_type": screen,
        "floor": floor,
        "repeat_count": len(tail),
        "first_step": first_record.get("step"),
        "last_step": last_record.get("step"),
        "room_phase": last_state.get("room_phase"),
        "last_actions": last_decision.get("actions") or [],
    }
    evidence.update(_reward_screen_payload(screen, first_state, last_state))
    return evidence


def _reward_screen_stall_reason(screen: str) -> str:
    return {
        "COMBAT_REWARD": "combat_reward_screen_stall",
        "CARD_REWARD": "card_reward_screen_stall",
        "BOSS_REWARD": "boss_reward_screen_stall",
        "CHEST": "chest_screen_stall",
    }.get(screen, "reward_screen_stall")


def _reward_screen_stall_tags(screen: str) -> list[str]:
    tags = ["infra", _reward_screen_stall_reason(screen), "mcp", "screen_stall", "reward_collection"]
    if screen == "BOSS_REWARD":
        tags.append("relic_collection")
    if screen == "CHEST":
        tags.append("chest_collection")
    if screen == "CARD_REWARD":
        tags.append("card_reward")
    return _dedupe(tags)


def _reward_screen_payload(
    screen: str,
    first_state: dict[str, Any],
    last_state: dict[str, Any],
) -> dict[str, Any]:
    first = first_state.get("screen_state") if isinstance(first_state.get("screen_state"), dict) else {}
    last = last_state.get("screen_state") if isinstance(last_state.get("screen_state"), dict) else {}
    if screen == "COMBAT_REWARD":
        return {
            "initial_reward_count": len(first.get("rewards") or []),
            "last_reward_count": len(last.get("rewards") or []),
        }
    if screen == "CARD_REWARD":
        return {
            "initial_card_count": len(first.get("cards") or []),
            "last_card_count": len(last.get("cards") or []),
        }
    if screen == "BOSS_REWARD":
        return {
            "initial_relic_count": len(first.get("relics") or []),
            "last_relic_count": len(last.get("relics") or []),
        }
    if screen == "CHEST":
        return {
            "initial_reward_count": len(first.get("rewards") or []),
            "last_reward_count": len(last.get("rewards") or []),
            "chest_open": last.get("chest_open"),
        }
    return {}


def _annotate_failure_attribution(
    classification: LogClassification,
    records: list[dict[str, Any]],
    knowledge: StaticKnowledge | None,
) -> None:
    if classification.failure_attribution:
        return
    if classification.category == INFRA_BLOCKED:
        attribution, tags, evidence = _infra_failure_attribution(classification.reason, records)
    elif classification.category == DIAGNOSTIC_EXCLUDED:
        attribution, tags, evidence = (
            "diagnostic_incomplete",
            ["diagnostic", classification.reason],
            {"reason": classification.reason},
        )
    elif classification.victory is False:
        attribution, tags, evidence = _loss_failure_attribution(records, classification, knowledge)
    else:
        return
    _attach_terminal_recovery_context(evidence, records)
    classification.failure_attribution = attribution
    classification.failure_tags = tags
    classification.failure_evidence = evidence


def _attach_terminal_recovery_context(evidence: dict[str, Any], records: list[dict[str, Any]]) -> None:
    synthetic = _last_synthetic_terminal_event(records)
    if synthetic and "synthetic_terminal" not in evidence:
        evidence["synthetic_terminal"] = synthetic
    outcome_source = str(_latest_outcome(records).get("source") or "")
    if outcome_source.startswith("synthetic_"):
        evidence["terminal_outcome_source"] = outcome_source


def _annotate_validation(
    classification: LogClassification,
    records: list[dict[str, Any]],
    knowledge: StaticKnowledge | None,
) -> None:
    flags: list[str] = []
    if classification.category == INFRA_BLOCKED:
        flags.append("infra_blocked")
    if classification.category == DIAGNOSTIC_EXCLUDED:
        flags.append("diagnostic_excluded")
    if classification.reason:
        if classification.reason in {"no_terminal_outcome", "synthetic_after_main_menu"}:
            flags.append(classification.reason)
        if classification.reason.startswith("mcp_"):
            flags.append("mcp_execution")
    if classification.recovered_actions > 0:
        flags.append("recovered_action_race")
    outcome = _latest_outcome(records)
    source = str(outcome.get("source") or "")
    if source.startswith("synthetic_"):
        flags.append("synthetic_terminal")
        flags.append(source)
    if any(record.get("event") == "synthetic_terminal_state" for record in records):
        flags.append("synthetic_terminal_state")
    if _terminal_recovery_unhealthy(records):
        flags.append("terminal_recovery_unhealthy")

    if classification.category == INFRA_BLOCKED:
        grade = "infra_blocked"
    elif classification.category == DIAGNOSTIC_EXCLUDED:
        grade = "diagnostic"
    elif flags:
        grade = "usable_with_recoveries"
    else:
        grade = "pristine"

    classification.validation_grade = grade
    classification.validation_flags = _dedupe(flags)
    classification.validation_evidence = {
        "act1_boss": _act1_boss_validation(records, knowledge),
    }


def _terminal_recovery_unhealthy(records: list[dict[str, Any]]) -> bool:
    for record in records:
        if record.get("event") != "synthetic_terminal_state":
            continue
        post = record.get("post_recovery_diagnostics")
        if isinstance(post, dict) and post.get("status") not in {None, "healthy"}:
            return True
    return False


def _act1_boss_validation(records: list[dict[str, Any]], knowledge: StaticKnowledge | None) -> dict[str, Any]:
    boss_records: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for index, record in enumerate(records):
        state = record.get("state")
        if isinstance(state, dict) and _is_act1_boss_combat_state(state, knowledge):
            boss_records.append((index, record, state))
    if not boss_records:
        return {"reached": False, "cleared": False}

    _, first_record, first_state = boss_records[0]
    _, last_record, last_state = boss_records[-1]
    boss_floor = _safe_int(first_state.get("floor"))
    cleared = False
    clear_index: int | None = None
    clear_record: dict[str, Any] | None = None
    for index, record in enumerate(records):
        state = record.get("state")
        if not isinstance(state, dict):
            continue
        if _safe_int(state.get("floor")) > boss_floor or _safe_int(state.get("act")) > 1:
            cleared = True
            clear_index = index
            clear_record = record
            break
    potion_use_steps = []
    for _, record, state in boss_records:
        decision = record.get("decision") or {}
        if _potion_action(decision.get("actions") or []):
            potion_use_steps.append(record.get("step"))
    prefix_records = records[: clear_index + 1] if clear_index is not None else records
    prefix_blockers = _act1_boss_prefix_blockers(prefix_records)

    return {
        "reached": True,
        "cleared": cleared,
        "prefix_pristine_clear": bool(cleared and not prefix_blockers),
        "prefix_blockers": prefix_blockers,
        "floor": boss_floor,
        "enemy_ids": _combat_enemy_ids(first_state),
        "entry_step": first_record.get("step"),
        "entry_hp": first_state.get("current_hp"),
        "entry_potion_count": len(first_state.get("potions") or []),
        "clear_step": clear_record.get("step") if clear_record else None,
        "last_step": last_record.get("step"),
        "last_hp": last_state.get("current_hp"),
        "last_turn": (_combat_payload(last_state).get("turn")),
        "potion_use_steps": potion_use_steps,
    }


def _act1_boss_pristine_clear_for_summary(boss: dict[str, Any], grade: str) -> bool:
    if not boss.get("cleared"):
        return False
    if "prefix_pristine_clear" in boss:
        return bool(boss.get("prefix_pristine_clear"))
    return grade == "pristine"


def _act1_boss_prefix_blockers(records: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    action_records = [record for record in records if record.get("event") == "action_result"]
    if any(_is_recovered_action_race(record) for record in action_records):
        blockers.append("recovered_action_race")
    if any(_is_unrecovered_action_race(record) for record in action_records):
        blockers.append("unrecovered_action_race")
    if any(str(record.get("action_status") or "").lower() in {"failed", "action_failed"} for record in action_records):
        blockers.append("failed_action")
    if any(record.get("event") == "synthetic_terminal_state" for record in records):
        blockers.append("synthetic_terminal_state")
    for record in records:
        state = record.get("state") or {}
        outcome = state.get("outcome") if isinstance(state, dict) else None
        source = str((outcome or {}).get("source") or "") if isinstance(outcome, dict) else ""
        if source.startswith("synthetic_"):
            blockers.append("synthetic_terminal")
            blockers.append(source)
    return _dedupe(blockers)


def _infra_failure_attribution(reason: str, records: list[dict[str, Any]]) -> tuple[str, list[str], dict[str, Any]]:
    tags = ["infra", reason]
    if reason.startswith("mcp_"):
        tags.append("mcp")
    if reason in {"failed_action", "unrecovered_action_race", "action_error"}:
        tags.extend(["mcp", "action_execution"])
    evidence = {"reason": reason}
    evidence.update(_infra_failure_context(reason, records))
    if reason.startswith("json_decode_error"):
        return "logging_infra", tags + ["logging"], evidence
    return "mcp_execution", tags, evidence


def _infra_failure_context(reason: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    last_state_record = _last_record_with_state(records, before_step=None)
    if last_state_record:
        context["last_state"] = _compact_state_context(last_state_record)

    synthetic = _last_synthetic_terminal_event(records)
    if synthetic:
        context["synthetic_terminal"] = synthetic

    if reason.startswith("mcp_"):
        read_event = _last_mcp_read_event(records)
        if read_event:
            context["mcp_read"] = read_event

    if reason in {"failed_action", "unrecovered_action_race", "action_error"}:
        action_event = _last_failed_action_event(records)
        if action_event:
            context["action_error"] = action_event
    return context


def _last_record_with_state(records: list[dict[str, Any]], before_step: int | None) -> dict[str, Any] | None:
    for record in reversed(records):
        if before_step is not None and _safe_int(record.get("step")) > before_step:
            continue
        if isinstance(record.get("state"), dict):
            return record
    return None


def _compact_state_context(record: dict[str, Any]) -> dict[str, Any]:
    state = record.get("state") if isinstance(record.get("state"), dict) else {}
    return {
        "step": record.get("step"),
        "screen_type": state.get("screen_type"),
        "room_phase": state.get("room_phase"),
        "floor": _safe_int(state.get("floor")),
        "act": _safe_int(state.get("act")),
        "current_hp": state.get("current_hp"),
        "max_hp": state.get("max_hp"),
    }


def _last_mcp_read_event(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("event") not in {"error", "synthetic_terminal_state"}:
            continue
        text = " ".join(str(record.get(field) or "") for field in ("error", "last_error", "previous_error"))
        lowered = text.lower()
        if "read_state_failed" not in lowered and "internal error" not in lowered and "null" not in lowered:
            continue
        event: dict[str, Any] = {"step": record.get("step")}
        if record.get("event"):
            event["event"] = record.get("event")
        if text.strip():
            event["error"] = text.strip()
        diagnostics = record.get("diagnostics")
        if isinstance(diagnostics, dict):
            status = diagnostics.get("status")
            if status is not None:
                event["diagnostics_status"] = status
        last_state_record = _last_record_with_state(records, before_step=_safe_int(record.get("step")))
        if last_state_record:
            event["last_state"] = _compact_state_context(last_state_record)
        return event
    return None


def _last_failed_action_event(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("event") != "action_result":
            continue
        status = str(record.get("action_status") or "").lower()
        if not _is_action_race(record) and status not in {"failed", "action_failed"}:
            continue
        event: dict[str, Any] = {
            "step": record.get("step"),
            "action_status": record.get("action_status"),
            "recovered": record.get("recovered"),
            "kind": "failed_action" if status in {"failed", "action_failed"} else _action_recovery_kind(record),
        }
        if record.get("last_error"):
            event["last_error"] = record.get("last_error")
        if record.get("rewrite_reason"):
            event["rewrite_reason"] = record.get("rewrite_reason")
        return event
    return None


def _last_synthetic_terminal_event(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(records):
        if record.get("event") != "synthetic_terminal_state":
            continue
        event: dict[str, Any] = {"step": record.get("step")}
        source = record.get("source")
        if source is not None:
            event["source"] = source
        if record.get("last_error"):
            event["last_error"] = record.get("last_error")
        if record.get("previous_error"):
            event["previous_error"] = record.get("previous_error")
        for key in ("terminal_recovery_attempted", "terminal_recovery_succeeded"):
            if key in record:
                event[key] = bool(record.get(key))
        post = record.get("post_recovery_diagnostics")
        if isinstance(post, dict) and post.get("status") is not None:
            event["post_recovery_status"] = post.get("status")
        return event
    return None


def _loss_failure_attribution(
    records: list[dict[str, Any]],
    classification: LogClassification,
    knowledge: StaticKnowledge | None,
) -> tuple[str, list[str], dict[str, Any]]:
    final_floor = classification.floor
    last_play_state = _last_play_state(records)
    last_combat_state = _last_combat_state(records)
    pre_boss_state, pre_boss_step = _latest_pre_boss_state(records)
    route_evidence = _selected_route_evidence(records)
    evidence: dict[str, Any] = {
        "final_floor": final_floor,
        "final_act": last_play_state.get("act"),
        "last_screen_type": last_play_state.get("screen_type"),
        "last_room_phase": last_play_state.get("room_phase"),
    }
    tags = ["run_loss"]

    if route_evidence:
        evidence["route"] = route_evidence
        route_flags = route_evidence.get("readiness_flags") or route_evidence.get("act2_route_flags") or []
        if route_evidence.get("readiness_penalty") is not None or route_flags:
            tags.append("route_risk")

    boss_combat = _is_act1_boss_combat_state(last_combat_state, knowledge)
    if boss_combat:
        tags.append("boss_combat")
        evidence["boss_combat"] = {
            "floor": last_combat_state.get("floor"),
            "turn": (last_combat_state.get("combat") or {}).get("turn"),
            "enemy_ids": _combat_enemy_ids(last_combat_state),
            "incoming_damage": (last_combat_state.get("combat") or {}).get("incoming_damage"),
            "current_hp": last_combat_state.get("current_hp"),
        }

    readiness: dict[str, Any] = {}
    if pre_boss_state and knowledge is not None:
        readiness = _readiness_features(
            pre_boss_state,
            records,
            pre_boss_step,
            knowledge,
            _observed_card_aliases(records, knowledge),
        )
        evidence["pre_boss"] = {
            "step": pre_boss_step,
            "floor": pre_boss_state.get("floor"),
            "hp_ratio": _hp_ratio(pre_boss_state),
            "potion_count": len(pre_boss_state.get("potions") or []),
            "readiness_score_boss": readiness.get("readiness_score_boss"),
            "readiness_gaps": readiness.get("readiness_gaps") or [],
            "readiness_risk_flags": readiness.get("readiness_risk_flags") or [],
        }
    elif pre_boss_state:
        evidence["pre_boss"] = {
            "step": pre_boss_step,
            "floor": pre_boss_state.get("floor"),
            "hp_ratio": _hp_ratio(pre_boss_state),
            "potion_count": len(pre_boss_state.get("potions") or []),
        }

    final_act = _safe_int(last_play_state.get("act"))
    pre_boss_relevant = boss_combat or (final_act <= 1 and final_floor <= 16)
    gaps = set(readiness.get("readiness_gaps") or []) if pre_boss_relevant else set()
    flags = set(readiness.get("readiness_risk_flags") or []) if pre_boss_relevant else set()
    potion_count = _safe_int((evidence.get("pre_boss") or {}).get("potion_count"))
    boss_score = readiness.get("readiness_score_boss")
    has_potion_gap = pre_boss_relevant and ("boss_no_tempo_potion" in flags or (pre_boss_state and potion_count <= 0))
    has_deck_gap = bool(
        pre_boss_relevant
        and (
            {"boss_not_ready", "boss_lacks_premium_block"}.intersection(flags)
            or {"frontload_damage_low", "defense_density_low", "premium_block_missing", "weak_missing", "aoe_missing"}.intersection(gaps)
            or (isinstance(boss_score, (int, float)) and boss_score < 60)
        )
    )

    if has_potion_gap:
        tags.append("potion_planning")
    if has_deck_gap:
        tags.extend(["deck_quality", "card_selection"])

    if boss_combat:
        if has_potion_gap:
            return "potion_planning", _dedupe(tags), evidence
        if has_deck_gap:
            return "deck_quality", _dedupe(tags), evidence
        return "combat_planning", _dedupe(tags + ["combat_planning"]), evidence

    if "route_risk" in tags:
        return "route_risk", _dedupe(tags), evidence
    if has_potion_gap:
        return "potion_planning", _dedupe(tags), evidence
    if has_deck_gap:
        return "deck_quality", _dedupe(tags), evidence
    return "combat_planning", _dedupe(tags + ["combat_planning"]), evidence


def _infra_error_reason(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        if record.get("event") != "error":
            continue
        text = " ".join(str(record.get(field) or "") for field in ("error", "last_error"))
        lowered = text.lower()
        if "cannot reach mcpthe" in lowered or "unreachable" in lowered or "connection refused" in lowered:
            return "mcp_unreachable"
        if "read_state_failed" in lowered or "state read failed" in lowered:
            if "internal error" in lowered or "null" in lowered:
                return "mcp_state_read_failed"
            return "mcp_read_failed"
        if "action failed" in lowered or "error at action" in lowered or "invalid command" in lowered:
            return "action_error"
    return None


def write_shadow_examples(output_dir: Path, examples: dict[str, list[dict[str, Any]]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in examples.items():
        path = output_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean_log_paths_from_manifest(path: Path) -> list[Path]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = manifest.get("categories", {}).get(CLEAN_TRAINABLE, [])
    return [Path(row["path"]) for row in rows if row.get("path")]


def resolve_log_training_source(logs: Iterable[Path], manifest: Path | None = None) -> dict[str, Any]:
    if manifest:
        clean_paths = clean_log_paths_from_manifest(manifest)
        resolved_log_paths = list(iter_log_files(clean_paths))
        warnings: list[str] = []
        if not clean_paths:
            warnings.append("no_clean_trainable_logs")
        elif not resolved_log_paths:
            warnings.append("no_resolved_clean_trainable_logs")
        missing_clean_paths = [str(path) for path in clean_paths if not path.exists()]
        if missing_clean_paths:
            warnings.append("missing_clean_trainable_logs")
        return {
            "mode": "manifest_clean_trainable",
            "quality_policy": CLEAN_TRAINABLE,
            "inputs": [str(manifest)],
            "manifest_path": str(manifest),
            "manifest_clean_paths": [str(path) for path in clean_paths],
            "manifest_clean_count": len(clean_paths),
            "missing_clean_paths": missing_clean_paths,
            "resolved_logs": [str(path) for path in resolved_log_paths],
            "resolved_log_count": len(resolved_log_paths),
            "warnings": warnings,
        }

    input_paths = list(logs)
    resolved_log_paths = list(iter_log_files(input_paths))
    warnings = ["no_resolved_logs"] if not resolved_log_paths else []
    return {
        "mode": "raw_logs",
        "quality_policy": "ungated_logs",
        "inputs": [str(path) for path in input_paths],
        "manifest_path": None,
        "resolved_logs": [str(path) for path in resolved_log_paths],
        "resolved_log_count": len(resolved_log_paths),
        "warnings": warnings,
    }


def _manifest_warnings(resolved_log_paths: list[Path]) -> list[str]:
    if not resolved_log_paths:
        return ["no_resolved_logs"]
    return []


def iter_log_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.glob("*.jsonl"))
        elif path.exists():
            yield path


_iter_log_files = iter_log_files


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _latest_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for record in records:
        state = record.get("state")
        if isinstance(state, dict):
            latest = state
    return latest


def _latest_outcome(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    for record in records:
        state = record.get("state") or {}
        if isinstance(state.get("outcome"), dict):
            outcome = state["outcome"]
    return outcome


def _last_play_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    fallback: dict[str, Any] = {}
    for record in reversed(records):
        state = record.get("state")
        if not isinstance(state, dict):
            continue
        if not fallback:
            fallback = state
        if state.get("screen_type") != "GAME_OVER":
            return state
    return fallback


def _last_combat_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in reversed(records):
        state = record.get("state")
        if not isinstance(state, dict):
            continue
        combat = _combat_payload(state)
        if combat or state.get("room_phase") == "COMBAT":
            return state
    return {}


def _latest_pre_boss_state(records: list[dict[str, Any]]) -> tuple[dict[str, Any], Any]:
    for record in reversed(records):
        state = record.get("state")
        if isinstance(state, dict) and _boss_available(state):
            return state, record.get("step")
    return {}, None


def _selected_route_evidence(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in reversed(records):
        state = record.get("state")
        if not isinstance(state, dict) or state.get("screen_type") != "MAP":
            continue
        route = state.get("route_evaluation") or {}
        options = route.get("options") or []
        if not options:
            continue
        selected = _first_choice_index((record.get("decision") or {}).get("actions") or [])
        selected_option = next((option for option in options if _safe_int(option.get("choice_index")) == selected), {})
        lookahead = selected_option.get("lookahead") or {}
        return {
            "step": record.get("step"),
            "floor": state.get("floor"),
            "selected_choice": selected,
            "selected_symbol": selected_option.get("symbol"),
            "route_score": selected_option.get("score"),
            "forced_elite_within_3": bool(lookahead.get("forced_elite_within_3")),
            "forced_combat_within_2": bool(lookahead.get("forced_combat_within_2")),
            "nearest_rest": lookahead.get("nearest_rest"),
            "nearest_shop": lookahead.get("nearest_shop"),
            "readiness_penalty": lookahead.get("readiness_penalty"),
            "readiness_flags": lookahead.get("readiness_flags") or [],
            "readiness_gaps": lookahead.get("readiness_gaps") or [],
            "act2_route_flags": lookahead.get("act2_route_flags") or [],
        }
    return {}


def _is_act1_boss_combat_state(state: dict[str, Any], knowledge: StaticKnowledge | None) -> bool:
    if not state:
        return False
    combat = _combat_payload(state)
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    if knowledge is not None and any(
        knowledge.boss_for(monster) is not None and _safe_int((knowledge.boss_for(monster) or {}).get("act")) == 1
        for monster in monsters
    ):
        return True
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        label = _norm_key(f"{monster.get('id', '')} {monster.get('name', '')}")
        if label.startswith("hexaghost") or label.startswith("slimeboss") or label.startswith("theguardian"):
            return True
        if label == "guardian":
            return True
        if monster.get("boss") and (_safe_int(state.get("act")) == 1 or _safe_int(state.get("floor")) == 16):
            return True
    return _safe_int(state.get("act")) == 1 and _safe_int(state.get("floor")) == 16


def _combat_enemy_ids(state: dict[str, Any]) -> list[Any]:
    monsters = _combat_payload(state).get("monsters") or []
    if not isinstance(monsters, list):
        return []
    return [monster.get("id") or monster.get("name") for monster in monsters if isinstance(monster, dict)]


def _combat_payload(state: dict[str, Any]) -> dict[str, Any]:
    combat = state.get("combat") or state.get("combat_state") or {}
    return combat if isinstance(combat, dict) else {}


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _norm_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _shadow_source_fields(final: LogClassification) -> dict[str, Any]:
    summary = final.action_recovery_summary if isinstance(final.action_recovery_summary, dict) else {}
    kinds = summary.get("by_kind") if isinstance(summary.get("by_kind"), dict) else {}
    return {
        "source_category": final.category,
        "source_reason": final.reason,
        "source_validation_grade": final.validation_grade or "unknown",
        "source_validation_flags": list(final.validation_flags or []),
        "source_recovered_actions": final.recovered_actions,
        "source_failed_actions": final.failed_actions,
        "source_has_recovered_action_race": final.recovered_actions > 0,
        "source_action_recovery_kinds": dict(sorted(kinds.items())),
    }


def _route_risk_examples(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
    card_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        if state.get("screen_type") != "MAP":
            continue
        route = state.get("route_evaluation") or {}
        options = route.get("options") or []
        if not options:
            continue
        decision = record.get("decision") or {}
        selected = _first_choice_index(decision.get("actions") or [])
        selected_option = next((option for option in options if _safe_int(option.get("choice_index")) == selected), {})
        lookahead = selected_option.get("lookahead") or {}
        row = {
                "source_log": str(path),
                "step": record.get("step"),
                "character": state.get("class"),
                "ascension": state.get("ascension_level"),
                "floor": state.get("floor"),
                "act": state.get("act"),
                "hp_ratio": _hp_ratio(state),
                "selected_choice": selected,
                "selected_symbol": selected_option.get("symbol"),
                "route_score": selected_option.get("score"),
                "forced_elite_within_3": bool(lookahead.get("forced_elite_within_3")),
                "forced_combat_within_2": bool(lookahead.get("forced_combat_within_2")),
                "nearest_rest": lookahead.get("nearest_rest"),
                "nearest_shop": lookahead.get("nearest_shop"),
                "readiness_penalty": lookahead.get("readiness_penalty"),
                "readiness_flags": lookahead.get("readiness_flags") or [],
                "act2_route_flags": lookahead.get("act2_route_flags") or [],
                "final_floor": final.floor,
                "victory": final.victory,
                "floor_delta": final.floor - _safe_int(state.get("floor")),
        }
        row.update(_shadow_source_fields(final))
        if knowledge is not None:
            deck_features = knowledge.deck_features(
                _deck_items_for_knowledge(state, records, record.get("step"), knowledge, card_aliases)
            )
            row.update(deck_features)
            row.update(knowledge.potion_features(state.get("potions") or []))
            row.update(knowledge.relic_features(_state_relic_items(state)))
            row.update(
                knowledge.boss_features(
                    _boss_items_for_knowledge(state),
                    act=_safe_int(state.get("act")),
                    boss_available=_boss_available(state),
                )
            )
            row.update(_readiness_features(state, records, record.get("step"), knowledge, card_aliases))
        rows.append(row)
    return rows


def _potion_tempo_examples(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        combat = state.get("combat") or {}
        potions = state.get("potions") or []
        incoming = _safe_int(combat.get("incoming_damage"))
        if not combat or not potions or incoming <= 0:
            continue
        potion_action = _potion_action((record.get("decision") or {}).get("actions") or [])
        row = {
                "source_log": str(path),
                "step": record.get("step"),
                "character": state.get("class"),
                "ascension": state.get("ascension_level"),
                "floor": state.get("floor"),
                "act": state.get("act"),
                "turn": combat.get("turn"),
                "hp_ratio": _hp_ratio(state),
                "incoming": incoming,
                "potion_ids": [_potion_identity(potion) for potion in potions if isinstance(potion, dict)],
                "enemy_ids": [
                    monster.get("id") or monster.get("name")
                    for monster in combat.get("monsters", [])
                    if isinstance(monster, dict)
                ],
                "used_potion": potion_action is not None,
                "used_potion_slot": potion_action.get("potion_slot") if potion_action else None,
                "used_potion_targeted": bool(potion_action and potion_action.get("target_index") is not None),
                "died_same_floor": bool(final.victory is False and final.floor == _safe_int(state.get("floor"))),
                "final_floor": final.floor,
                "victory": final.victory,
        }
        row.update(_shadow_source_fields(final))
        if knowledge is not None:
            row.update(knowledge.potion_features(potions))
            row.update(knowledge.monster_features(combat.get("monsters") or []))
            row.update(knowledge.boss_features(combat.get("monsters") or [], act=_safe_int(state.get("act"))))
            row.update(knowledge.relic_features(_state_relic_items(state)))
        rows.append(row)
    return rows


def _potion_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for action in actions:
        if action.get("action") != "use_potion":
            continue
        normalized = dict(action)
        if "potion_slot" not in normalized and "slot" in normalized:
            normalized["potion_slot"] = normalized.get("slot")
        return normalized
    return None


def _pre_boss_examples(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
    card_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        if not state.get("boss_available"):
            continue
        deck = _state_deck_items(state)
        deck_for_counts = state.get("deck") if isinstance(state.get("deck"), list) else deck
        row = {
                "source_log": str(path),
                "step": record.get("step"),
                "character": state.get("class"),
                "ascension": state.get("ascension_level"),
                "floor": state.get("floor"),
                "act": state.get("act"),
                "hp_ratio": _hp_ratio(state),
                "deck_size": len(deck) if isinstance(deck, list) else None,
                "attack_cards": _count_named_cards(
                    deck_for_counts,
                    {"Strike_R", "Strike", "Bash", "Clothesline", "Wild Strike", "Immolate"},
                ),
                "block_cards": _count_named_cards(
                    deck_for_counts,
                    {"Defend_R", "Defend", "Shrug It Off", "True Grit", "Power Through"},
                ),
                "draw_cards": _count_named_cards(deck_for_counts, {"Battle Trance", "Burning Pact", "Pommel Strike", "Offering"}),
                "potion_count": len(state.get("potions") or []),
                "relic_count": len(_state_relic_items(state)),
                "gold": state.get("gold"),
                "final_floor": final.floor,
                "victory": final.victory,
        }
        row.update(_shadow_source_fields(final))
        if knowledge is not None:
            deck_features = knowledge.deck_features(
                _deck_items_for_knowledge(state, records, record.get("step"), knowledge, card_aliases)
            )
            row.update(deck_features)
            if deck_features.get("deck_known_cards", 0) > 0:
                row["attack_cards"] = deck_features.get("deck_attack_cards", row["attack_cards"])
                row["block_cards"] = deck_features.get("deck_tag_block", row["block_cards"])
                row["draw_cards"] = deck_features.get("deck_tag_draw", row["draw_cards"])
            row.update(knowledge.potion_features(state.get("potions") or []))
            row.update(knowledge.relic_features(_state_relic_items(state)))
            row.update(
                knowledge.boss_features(
                    _boss_items_for_knowledge(state),
                    act=_safe_int(state.get("act")),
                    boss_available=_boss_available(state),
                )
            )
            row.update(_readiness_features(state, records, record.get("step"), knowledge, card_aliases))
        flags = row.get("readiness_risk_flags") if isinstance(row.get("readiness_risk_flags"), list) else []
        row["boss_potion_gap"] = bool(row.get("potion_count", 0) <= 0 or "boss_no_tempo_potion" in flags)
        rows.append(row)
    return rows


def _combat_search_label_examples(
    path: Path,
    records: list[dict[str, Any]],
    final: LogClassification,
    knowledge: StaticKnowledge | None = None,
    card_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        combat = _combat_payload(state)
        if not combat:
            continue
        decision = record.get("decision") or {}
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        search = metadata.get("search") if isinstance(metadata.get("search"), dict) else None
        actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
        actual_action = _first_play_card_action(actions)
        diagnostic_search: dict[str, Any] | None = None
        if search is None:
            diagnostic_search = _missed_single_card_search_signal(state, combat, actual_action)
            if diagnostic_search is None:
                continue
            search = diagnostic_search["search"]
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hand = _combat_hand_cards(combat)
        monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
        label_action = (
            diagnostic_search.get("label_action")
            if diagnostic_search is not None and isinstance(diagnostic_search.get("label_action"), dict)
            else actual_action
        )
        initial_loss = _optional_int(search.get("initial_loss"))
        projected_loss = _optional_int(search.get("projected_loss"))
        direct_kill = _direct_kill_signal(combat, actual_action)
        row = {
            "source_log": str(path),
            "step": record.get("step"),
            "character": state.get("class"),
            "ascension": state.get("ascension_level"),
            "floor": state.get("floor"),
            "act": state.get("act"),
            "turn": combat.get("turn"),
            "hp_ratio": _hp_ratio(state),
            "current_hp": player.get("current_hp", state.get("current_hp")),
            "max_hp": player.get("max_hp", state.get("max_hp")),
            "current_block": player.get("block"),
            "current_energy": player.get("current_energy"),
            "incoming": _safe_int(combat.get("incoming_damage")),
            "hand_size": len(hand),
            "hand_ids": [_card_identity(card) for card in hand],
            "hand_names": [_card_name(card) for card in hand],
            "playable_count": sum(1 for card in hand if not isinstance(card, dict) or card.get("is_playable", True)),
            "enemy_count": len([monster for monster in monsters if isinstance(monster, dict)]),
            "enemy_ids": [
                monster.get("id") or monster.get("name")
                for monster in monsters
                if isinstance(monster, dict)
            ],
            "enemy_hps": [_monster_hp(monster) for monster in monsters if isinstance(monster, dict)],
            "enemy_intents": [
                monster.get("intent") or monster.get("move")
                for monster in monsters
                if isinstance(monster, dict)
            ],
            "actual_action": actual_action.get("action") if actual_action else None,
            "actual_card_index": actual_action.get("card_index") if actual_action else None,
            "actual_target_index": actual_action.get("target_index") if actual_action else None,
            "label_action": label_action.get("action") if label_action else None,
            "label_card_index": label_action.get("card_index") if label_action else None,
            "label_target_index": label_action.get("target_index") if label_action else None,
            "label_first_card_key": search.get("first_card_key"),
            "label_sequence_card_keys": list(search.get("sequence_card_keys") or []),
            "search_type": search.get("type"),
            "search_score": search.get("score"),
            "initial_loss": initial_loss,
            "projected_loss": projected_loss,
            "loss_delta": None if initial_loss is None or projected_loss is None else initial_loss - projected_loss,
            "kills": _safe_int(search.get("kills")),
            "attacks_removed": _safe_int(search.get("attacks_removed")),
            "retaliation_damage": _safe_int(search.get("retaliation_damage")),
            "avoided_lethal": bool(search.get("avoided_lethal")),
            "direct_kill_available": bool(direct_kill["available"]),
            "direct_kill_card_indices": direct_kill["card_indices"],
            "direct_kill_card_keys": direct_kill["card_keys"],
            "direct_kill_enemy_id": direct_kill["enemy_id"],
            "direct_kill_enemy_hp": direct_kill["enemy_hp"],
            "label_missed_direct_kill": bool(direct_kill["missed"]),
            "label_missed_single_card_search": bool(diagnostic_search is not None and not direct_kill["missed"]),
            "died_same_floor": bool(final.victory is False and final.floor == _safe_int(state.get("floor"))),
            "final_floor": final.floor,
            "victory": final.victory,
        }
        row.update(_shadow_source_fields(final))
        if knowledge is not None:
            row.update(
                knowledge.deck_features(_deck_items_for_knowledge(state, records, record.get("step"), knowledge, card_aliases))
            )
            row.update(knowledge.potion_features(state.get("potions") or []))
            row.update(knowledge.monster_features(monsters))
            row.update(knowledge.boss_features(monsters, act=_safe_int(state.get("act"))))
            row.update(knowledge.relic_features(_state_relic_items(state)))
        rows.append(row)
    return rows


def _combat_hand_cards(combat: dict[str, Any]) -> list[Any]:
    hand_cards = combat.get("hand_cards")
    if isinstance(hand_cards, list) and hand_cards:
        return hand_cards
    hand = combat.get("hand")
    return hand if isinstance(hand, list) else []


def _missed_single_card_search_signal(
    state: dict[str, Any],
    combat: dict[str, Any],
    actual_action: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not _boss_search_diagnostic_context(state, combat):
        return None
    search_combat = _combat_for_search(combat)
    search_game = dict(state)
    search_game["combat_state"] = search_combat
    result = find_best_combat_sequence(search_game, max_depth=1)
    if result is None or len(result.sequence) != 1:
        return None
    if not _single_card_search_is_diagnostic(result):
        return None

    label_action = dict(result.first_action)
    if _search_action_matches_actual(label_action, actual_action):
        return None

    return {
        "label_action": label_action,
        "search": {
            "type": "single_card_search_diagnostic",
            "sequence_card_keys": list(result.sequence_card_keys),
            "first_card_key": result.first_card_key,
            "score": result.score,
            "initial_loss": result.initial_loss,
            "projected_loss": result.projected_loss,
            "kills": result.kills,
            "attacks_removed": result.attacks_removed,
            "retaliation_damage": result.retaliation_damage,
            "avoided_lethal": result.avoided_lethal,
            "reason": result.reason,
        },
    }


def _single_card_search_is_diagnostic(result: Any) -> bool:
    loss_delta = int(result.initial_loss or 0) - int(result.projected_loss or 0)
    if int(result.attacks_removed or 0) > 0:
        return True
    if bool(result.avoided_lethal) and loss_delta > 0:
        return True
    if int(getattr(result, "retaliation_damage", 0) or 0) > 0 and loss_delta > 0:
        return True
    return int(result.initial_loss or 0) >= 10 and loss_delta >= 8


def _boss_search_diagnostic_context(state: dict[str, Any], combat: dict[str, Any]) -> bool:
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        if _monster_is_act1_boss(monster):
            return True
    return _safe_int(state.get("act")) == 1 and _safe_int(state.get("floor")) >= 16


def _monster_is_act1_boss(monster: dict[str, Any]) -> bool:
    label = f"{monster.get('id', '')} {monster.get('name', '')}".replace(" ", "").lower()
    return any(boss in label for boss in ("theguardian", "slimeboss", "hexaghost"))


def _combat_for_search(combat: dict[str, Any]) -> dict[str, Any]:
    search_combat = dict(combat)
    cards = _combat_hand_cards(combat)
    if cards:
        search_combat["hand"] = cards
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    search_monsters: list[dict[str, Any]] = []
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        search_monster = dict(monster)
        if "current_hp" not in search_monster and "hp" in search_monster:
            search_monster["current_hp"] = search_monster.get("hp")
        search_monsters.append(search_monster)
    if search_monsters:
        search_combat["monsters"] = search_monsters
    return search_combat


def _search_action_matches_actual(label_action: dict[str, Any], actual_action: dict[str, Any] | None) -> bool:
    if actual_action is None:
        return False
    if actual_action.get("action") != label_action.get("action"):
        return False
    if _safe_int(actual_action.get("card_index")) != _safe_int(label_action.get("card_index")):
        return False
    label_target = _safe_int(label_action.get("target_index"))
    if label_target > 0 and _safe_int(actual_action.get("target_index")) != label_target:
        return False
    return True


def _direct_kill_signal(combat: dict[str, Any], first_action: dict[str, Any] | None) -> dict[str, Any]:
    result = {
        "available": False,
        "card_indices": [],
        "card_keys": [],
        "enemy_id": None,
        "enemy_hp": None,
        "missed": False,
    }
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    live = [
        monster
        for monster in monsters
        if isinstance(monster, dict)
        and not monster.get("is_dead")
        and not monster.get("is_gone")
        and _safe_int(_monster_hp(monster)) > 0
    ]
    if len(live) != 1:
        return result

    target = live[0]
    target_hp = _safe_int(_monster_hp(target)) + _safe_int(target.get("block"))
    player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
    energy = _safe_int(player.get("current_energy"))
    kill_indices: list[int] = []
    kill_keys: list[str] = []
    for index, card in enumerate(_combat_hand_cards(combat), start=1):
        if not isinstance(card, dict):
            continue
        if not card.get("is_playable", True):
            continue
        if str(card.get("type") or "").upper() != "ATTACK":
            continue
        cost = _card_energy_cost(card, energy)
        if cost is None or cost > energy:
            continue
        if _safe_int(card.get("damage")) < target_hp:
            continue
        kill_indices.append(index)
        key = _card_identity(card)
        kill_keys.append(str(key) if key is not None else "")

    if not kill_indices:
        return result
    chosen_index = _safe_int(first_action.get("card_index")) if first_action else 0
    result.update(
        {
            "available": True,
            "card_indices": kill_indices,
            "card_keys": kill_keys,
            "enemy_id": target.get("id") or target.get("name"),
            "enemy_hp": _safe_int(_monster_hp(target)),
            "missed": chosen_index not in kill_indices,
        }
    )
    return result


def _card_energy_cost(card: dict[str, Any], energy: int) -> int | None:
    cost = _safe_int(card.get("cost"))
    if cost == -1:
        return energy if energy > 0 else None
    if cost < 0:
        return 0
    return cost


def _card_identity(card: Any) -> Any:
    if isinstance(card, dict):
        return card.get("id") or card.get("card_id") or card.get("name")
    return card


def _card_name(card: Any) -> Any:
    if isinstance(card, dict):
        return card.get("name") or card.get("id") or card.get("card_id")
    return card


def _potion_identity(potion: Any) -> Any:
    if isinstance(potion, dict):
        return potion.get("id") or potion.get("potion_id") or potion.get("name")
    return potion


def _monster_hp(monster: dict[str, Any]) -> Any:
    return monster.get("current_hp", monster.get("hp"))


def _first_play_card_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for action in actions:
        if isinstance(action, dict) and action.get("action") == "play_card":
            return action
    return None


def _first_choice_index(actions: list[dict[str, Any]]) -> int | None:
    for action in actions:
        if action.get("action") == "choose":
            return _optional_int(action.get("choice_index"))
    return None


def _hp_ratio(state: dict[str, Any]) -> float | None:
    current = _safe_int(state.get("current_hp"))
    maximum = _safe_int(state.get("max_hp"))
    if maximum <= 0:
        return None
    return round(current / maximum, 3)


def _count_named_cards(deck: Any, names: set[str]) -> int:
    if not isinstance(deck, list):
        return 0
    normalized = {name.replace(" ", "").lower() for name in names}
    count = 0
    for card in deck:
        key = str(card).replace(" ", "").lower()
        if key in normalized:
            count += 1
    return count


def _list_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _observed_card_aliases(records: list[dict[str, Any]], knowledge: StaticKnowledge | None) -> dict[str, str]:
    if knowledge is None:
        return {}
    candidates: dict[str, set[str]] = {}
    for record in records:
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
        for source in (state, screen_state):
            for option in _list_items(source.get("card_reward_options")):
                if not isinstance(option, dict):
                    continue
                card_id = str(option.get("id") or option.get("card_id") or "").strip()
                if not card_id or knowledge.card_for({"id": card_id}) is None:
                    continue
                for key in _card_alias_keys(option.get("name")):
                    if key and key != card_id:
                        candidates.setdefault(key, set()).add(card_id)
    return {alias: next(iter(ids)) for alias, ids in candidates.items() if len(ids) == 1}


def _resolve_card_aliases(items: list[Any], aliases: dict[str, str]) -> list[Any]:
    if not aliases:
        return list(items)
    return [_resolve_card_alias(item, aliases) for item in items]


def _resolve_card_alias(item: Any, aliases: dict[str, str]) -> Any:
    for key in _card_alias_keys(item.get("name") if isinstance(item, dict) else item):
        card_id = aliases.get(key)
        if card_id:
            if isinstance(item, dict):
                resolved = dict(item)
                resolved["id"] = card_id
                return resolved
            return {"id": card_id, "name": item}
    if isinstance(item, dict):
        for key in _card_alias_keys(item.get("id")):
            card_id = aliases.get(key)
            if card_id:
                resolved = dict(item)
                resolved["id"] = card_id
                return resolved
    return item


def _card_alias_keys(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    base = raw.split("+", 1)[0].removesuffix("_P").strip()
    return _dedupe([raw, base])


def _deck_items_for_knowledge(
    state: dict[str, Any],
    records: list[dict[str, Any]],
    step: Any,
    knowledge: StaticKnowledge,
    card_aliases: dict[str, str] | None = None,
) -> list[Any]:
    deck = _state_deck_items(state)
    if card_aliases is None:
        card_aliases = _observed_card_aliases(records, knowledge)
    resolved_deck = _resolve_card_aliases(deck, card_aliases) if isinstance(deck, list) else []
    resolved_features = knowledge.deck_features(resolved_deck)
    if resolved_deck and resolved_features.get("deck_unknown_cards", 0) == 0:
        return resolved_deck
    character = str(state.get("class") or "").upper()
    reconstructed = list(STARTER_DECKS.get(character, []))
    current_step = _safe_int(step)
    for record in records:
        record_step = _safe_int(record.get("step"))
        if current_step and record_step > current_step:
            break
        pick = (record.get("decision") or {}).get("learn_card_pick")
        if pick:
            reconstructed.append(pick)
    reconstructed_features = knowledge.deck_features(reconstructed)
    if (
        reconstructed
        and reconstructed_features.get("deck_unknown_cards", 0) < resolved_features.get("deck_unknown_cards", 0)
        and (
            resolved_features.get("deck_known_cards", 0) <= 0
            or _unknown_cards_look_localized(resolved_deck, knowledge)
        )
    ):
        return reconstructed
    if resolved_deck and resolved_features.get("deck_known_cards", 0) > 0:
        return resolved_deck
    return reconstructed if reconstructed else resolved_deck


def _state_deck_items(state: dict[str, Any]) -> list[Any]:
    deck_cards = state.get("deck_cards")
    if isinstance(deck_cards, list) and deck_cards:
        return deck_cards
    deck = state.get("deck")
    return deck if isinstance(deck, list) else []


def _state_relic_items(state: dict[str, Any]) -> list[Any]:
    relic_items = state.get("relic_items")
    if isinstance(relic_items, list) and relic_items:
        return relic_items
    relics = state.get("relics")
    return relics if isinstance(relics, list) else []


def _unknown_cards_look_localized(items: list[Any], knowledge: StaticKnowledge) -> bool:
    unknown_items = [item for item in items if knowledge.card_for(item) is None]
    return bool(unknown_items) and all(_looks_localized_or_garbled(_card_display_text(item)) for item in unknown_items)


def _card_display_text(card: Any) -> str:
    if isinstance(card, dict):
        for key in ("id", "name", "card_id"):
            value = card.get(key)
            if value not in (None, ""):
                return str(value)
        return ""
    return str(card or "")


def _looks_localized_or_garbled(value: str) -> bool:
    return bool(value) and any(ord(char) > 127 for char in value)


def _boss_items_for_knowledge(state: dict[str, Any]) -> list[Any]:
    items: list[Any] = []
    screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
    for source in (state, screen_state):
        for key in ("boss", "boss_id", "boss_name", "act_boss", "act_boss_id", "act_boss_name"):
            value = source.get(key)
            if isinstance(value, list):
                items.extend(value)
            elif value:
                items.append(value)
    return items


def _boss_available(state: dict[str, Any]) -> bool:
    screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
    return bool(state.get("boss_available") or screen_state.get("boss_available"))


def _readiness_features(
    state: dict[str, Any],
    records: list[dict[str, Any]],
    step: Any,
    knowledge: StaticKnowledge,
    card_aliases: dict[str, str] | None = None,
) -> dict[str, Any]:
    readiness_state = dict(state)
    readiness_state["deck"] = _deck_items_for_knowledge(state, records, step, knowledge, card_aliases)
    result = act1_readiness(readiness_state, knowledge=knowledge)
    scores = result.get("scores") if isinstance(result.get("scores"), dict) else {}
    return {
        "readiness_score_hp": scores.get("hp"),
        "readiness_score_output": scores.get("output"),
        "readiness_score_defense": scores.get("defense"),
        "readiness_score_aoe": scores.get("aoe"),
        "readiness_score_debuff": scores.get("debuff"),
        "readiness_score_potion": scores.get("potion"),
        "readiness_score_elite": scores.get("elite"),
        "readiness_score_boss": scores.get("boss"),
        "readiness_score_overall": scores.get("overall"),
        "readiness_gaps": result.get("gaps") or [],
        "readiness_risk_flags": result.get("risk_flags") or [],
        "readiness_recommendations": result.get("recommendations") or [],
    }


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
