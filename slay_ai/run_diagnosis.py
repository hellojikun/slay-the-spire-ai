"""Summarize manifest and shadow-advice outputs into run-level diagnosis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .combat_search import find_best_combat_sequence
from .shadow_feature_audit import audit_adjacent_shadow_features
from .training_manifest import CLEAN_TRAINABLE, DIAGNOSTIC_EXCLUDED, INFRA_BLOCKED


ADVICE_FILES = {
    "route_risk": "route_risk_advice.jsonl",
    "potion_tempo": "potion_tempo_advice.jsonl",
    "pre_boss_deck_quality": "pre_boss_deck_quality_advice.jsonl",
    "combat_search": "combat_search_advice.jsonl",
}
HIGH_RISK_ADVICE = {
    "route_risk": {"avoid_or_require_recovery"},
    "potion_tempo": {"consider_potion"},
    "pre_boss_deck_quality": {"needs_boss_resources"},
    "combat_search": {"review_missed_lethal", "review_missed_single_card_search"},
}
WATCH_ADVICE = {
    "route_risk": {"watch_route"},
    "potion_tempo": {"watch_potion_tempo"},
    "pre_boss_deck_quality": {"boss_borderline"},
    "combat_search": {"review_search_label"},
}
EXPECTED_FEATURE_PREFIXES = {
    "route_risk": ("deck_",),
    "potion_tempo": ("potion_", "enemy_"),
    "pre_boss_deck_quality": ("deck_", "boss_"),
    "combat_search": ("deck_", "enemy_"),
}
ENGINEERING_ATTRIBUTIONS = {"diagnostic_incomplete", "logging_infra", "mcp_execution", "unknown_clean_failure"}
AI_ATTRIBUTIONS = {"card_selection", "combat_planning", "deck_quality", "potion_planning", "route_risk"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize training manifests and shadow advice into run diagnoses.")
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--advice-dir", type=Path, help="Optional shadow-advice directory for a single manifest.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    parser.add_argument("--assignment-output", type=Path, help="Optional text output path for diagnosis handoff prompts.")
    args = parser.parse_args(argv)

    if args.advice_dir is not None and len(args.manifests) != 1:
        parser.error("--advice-dir can only be used with one manifest.")

    diagnoses = [
        diagnose_manifest(path, advice_dir=args.advice_dir if len(args.manifests) == 1 else None)
        for path in args.manifests
    ]
    payload: dict[str, Any] = {"version": 1, "diagnoses": diagnoses}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote run diagnosis: {args.output}")
    if args.assignment_output:
        args.assignment_output.parent.mkdir(parents=True, exist_ok=True)
        args.assignment_output.write_text(_assignment_output_text(diagnoses) + "\n", encoding="utf-8")
        print(f"Wrote run diagnosis assignment: {args.assignment_output}")
    for diagnosis in diagnoses:
        print(diagnosis["status_line"])
    return 0


def diagnose_manifest(path: Path, *, advice_dir: Path | None = None) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    context = _diagnosis_context(path, manifest, advice_dir=advice_dir)
    category, item = _primary_manifest_item(manifest)
    return _diagnosis_for_item(path, category, item, context)


def diagnose_manifest_runs(path: Path, *, advice_dir: Path | None = None) -> list[dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    context = _diagnosis_context(path, manifest, advice_dir=advice_dir)
    diagnoses = [
        _diagnosis_for_item(path, category, item, context, source_scoped_advice=True)
        for category, item in _manifest_items(manifest)
    ]
    if diagnoses:
        return diagnoses
    return [_diagnosis_for_item(path, None, {}, context, source_scoped_advice=True)]


def _diagnosis_context(
    path: Path,
    manifest: dict[str, Any],
    *,
    advice_dir: Path | None = None,
) -> dict[str, Any]:
    manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    advice_path = advice_dir or _manifest_advice_path(manifest)
    advice = summarize_advice(advice_path)
    shadow_feature_coverage = manifest_summary.get("shadow_feature_coverage") or {}
    shadow_feature_coverage_source = "manifest_summary" if shadow_feature_coverage else None
    if not shadow_feature_coverage:
        shadow_feature_coverage, shadow_feature_coverage_source = audit_adjacent_shadow_features(path)
    shadow_feature_gaps = summarize_shadow_feature_coverage(shadow_feature_coverage)
    if not shadow_feature_gaps.get("available") and shadow_feature_coverage_source:
        shadow_feature_gaps["reason"] = shadow_feature_coverage_source
    return {
        "manifest_summary": manifest_summary,
        "advice_path": advice_path,
        "advice": advice,
        "shadow_feature_coverage": shadow_feature_coverage,
        "shadow_feature_coverage_source": shadow_feature_coverage_source,
        "shadow_feature_gaps": shadow_feature_gaps,
    }


def _diagnosis_for_item(
    path: Path,
    category: str | None,
    item: dict[str, Any],
    context: dict[str, Any],
    *,
    source_scoped_advice: bool = False,
) -> dict[str, Any]:
    manifest_summary = context["manifest_summary"]
    advice = _advice_for_item(context, item) if source_scoped_advice else context["advice"]
    attribution = _failure_attribution(category, item)
    combat_endgame = summarize_combat_endgame(path, item)
    diagnosis = {
        "manifest_path": str(path),
        "category": category,
        "reason": item.get("reason"),
        "failure_attribution": attribution,
        "failure_tags": item.get("failure_tags") or [],
        "failure_evidence": item.get("failure_evidence") or {},
        "path": item.get("path"),
        "character": item.get("character"),
        "ascension": item.get("ascension"),
        "floor": item.get("floor"),
        "victory": item.get("victory"),
        "steps": item.get("steps"),
        "action_records": item.get("action_records"),
        "recovered_actions": item.get("recovered_actions"),
        "failed_actions": item.get("failed_actions"),
        "action_recovery_summary": item.get("action_recovery_summary") or {},
        "validation_grade": item.get("validation_grade"),
        "validation_flags": item.get("validation_flags") or [],
        "act1_boss": ((item.get("validation_evidence") or {}).get("act1_boss") or {}),
        "shadow_examples": manifest_summary.get("shadow_examples") or {},
        "shadow_feature_coverage": context["shadow_feature_coverage"],
        "shadow_feature_coverage_source": context["shadow_feature_coverage_source"],
        "shadow_feature_gaps": context["shadow_feature_gaps"],
        "shadow_label_quality": manifest_summary.get("shadow_label_quality") or {},
        "shadow_advice": advice,
        "combat_endgame": combat_endgame,
        "next_action": _next_action(category, attribution, advice, item, combat_endgame),
    }
    diagnosis["status_line"] = _status_line(diagnosis)
    diagnosis["recommended_assignment"] = _recommended_assignment(diagnosis)
    return diagnosis


def summarize_shadow_feature_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    if not coverage:
        return {"available": False, "reason": "missing"}
    categories = coverage.get("categories") if isinstance(coverage.get("categories"), dict) else {}
    result: dict[str, Any] = {
        "available": True,
        "total_rows": int(coverage.get("total_rows") or 0),
        "categories": {},
        "gap_count": 0,
        "zero_count": 0,
        "unknown_static_total": 0,
        "unknown_static_features": {},
    }
    for category, expected_prefixes in EXPECTED_FEATURE_PREFIXES.items():
        category_summary = categories.get(category) if isinstance(categories.get(category), dict) else {}
        rows = int(category_summary.get("rows") or 0)
        if rows <= 0:
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
        raw_unknown = (
            category_summary.get("unknown_static_features")
            if isinstance(category_summary.get("unknown_static_features"), dict)
            else {}
        )
        unknown_static_features: dict[str, int] = {}
        for field, stats in raw_unknown.items():
            if isinstance(stats, dict):
                total = _safe_int(stats.get("total"))
            else:
                total = _safe_int(stats)
            if total > 0:
                unknown_static_features[str(field)] = total
        result["categories"][category] = {
            "rows": rows,
            "source_quality": category_summary.get("source_quality") or {},
            "missing_prefixes": missing_prefixes,
            "zero_prefixes": zero_prefixes,
            "nonzero_prefixes": nonzero_prefixes,
            "unknown_static_features": unknown_static_features,
        }
        result["gap_count"] += len(missing_prefixes)
        result["zero_count"] += len(zero_prefixes)
        for field, total in unknown_static_features.items():
            key = f"{category}:{field}"
            result["unknown_static_features"][key] = result["unknown_static_features"].get(key, 0) + total
            result["unknown_static_total"] += total
    return result


def summarize_advice(advice_dir: Path | None) -> dict[str, Any]:
    return _summarize_advice(advice_dir)


def summarize_advice_for_source(advice_dir: Path | None, source_log: str | None) -> dict[str, Any]:
    return _summarize_advice(advice_dir, source_log=source_log)


def _summarize_advice(advice_dir: Path | None, *, source_log: str | None = None) -> dict[str, Any]:
    if advice_dir is None:
        return {"available": False, "reason": "not_configured", "source_log": source_log, "categories": {}}
    if not advice_dir.exists():
        return {"available": False, "reason": "not_found", "path": str(advice_dir), "source_log": source_log, "categories": {}}

    advice_summary = _read_advice_summary(advice_dir)
    summary_shadow_inputs = advice_summary.get("shadow_inputs") if isinstance(advice_summary.get("shadow_inputs"), dict) else {}
    summary_model_sources = (
        advice_summary.get("model_training_source")
        if isinstance(advice_summary.get("model_training_source"), dict)
        else {}
    )
    summary_model_source_quality = (
        advice_summary.get("model_training_source_quality")
        if isinstance(advice_summary.get("model_training_source_quality"), dict)
        else {}
    )
    categories: dict[str, Any] = {}
    for category, filename in ADVICE_FILES.items():
        rows = [
            row
            for row in _read_jsonl(advice_dir / filename)
            if source_log is None or _source_log_matches(row.get("source_log"), source_log)
        ]
        advice_counts: dict[str, int] = {}
        model_available = False
        scored_rows = 0
        top_signal: dict[str, Any] | None = None
        model_load_quality: dict[str, Any] = {}
        model_training_source: dict[str, Any] = {}
        model_training_source_quality: Any = None
        shadow_source_files: set[str] = set()
        shadow_source_modes: set[str] = set()
        for row in rows:
            advice = str(row.get("advice") or "unknown")
            advice_counts[advice] = advice_counts.get(advice, 0) + 1
            model_available = model_available or bool(row.get("model_available"))
            if not model_load_quality:
                row_load_quality = row.get("model_load_quality")
                if isinstance(row_load_quality, dict) and row_load_quality:
                    model_load_quality = row_load_quality
            if not model_training_source:
                row_training_source = row.get("model_training_source")
                if isinstance(row_training_source, dict) and row_training_source:
                    model_training_source = row_training_source
            if model_training_source_quality in (None, "", {}, []):
                row_training_source_quality = row.get("model_training_source_quality")
                if row_training_source_quality not in (None, "", {}, []):
                    model_training_source_quality = row_training_source_quality
            if row.get("shadow_source_file"):
                shadow_source_files.add(str(row["shadow_source_file"]))
            if row.get("shadow_source_mode"):
                shadow_source_modes.add(str(row["shadow_source_mode"]))
            score = _category_score(category, row)
            if score is not None:
                scored_rows += 1
                if top_signal is None or _stronger_signal(category, score, float(top_signal["score"])):
                    top_signal = {
                        "score": score,
                        "advice": advice,
                        "floor": row.get("floor"),
                        "step": row.get("step"),
                        "source_log": row.get("source_log"),
                    } | _top_signal_details(category, row)
        summary_category_inputs = (
            summary_shadow_inputs.get("categories", {}).get(category)
            if isinstance(summary_shadow_inputs.get("categories"), dict)
            else {}
        )
        if not model_training_source and isinstance(summary_model_sources.get(category), dict):
            model_training_source = summary_model_sources.get(category) or {}
        if model_training_source_quality in (None, "", {}, []):
            model_training_source_quality = summary_model_source_quality.get(category)
        categories[category] = {
            "rows": len(rows),
            "model_available": model_available,
            "scored_rows": scored_rows,
            "advice_counts": dict(sorted(advice_counts.items())),
            "high_risk_count": sum(advice_counts.get(name, 0) for name in HIGH_RISK_ADVICE[category]),
            "watch_count": sum(advice_counts.get(name, 0) for name in WATCH_ADVICE[category]),
            "top_signal": top_signal,
            "model_load_quality": model_load_quality,
            "model_training_source": model_training_source,
            "model_training_source_quality": model_training_source_quality,
            "shadow_source_files": sorted(shadow_source_files),
            "shadow_source_modes": sorted(shadow_source_modes),
            "shadow_input": summary_category_inputs if isinstance(summary_category_inputs, dict) else {},
        }
    return {
        "available": True,
        "path": str(advice_dir),
        "summary_path": str(advice_dir / "summary.json") if advice_summary else None,
        "source_log": source_log,
        "shadow_inputs": summary_shadow_inputs,
        "model_training_source": summary_model_sources,
        "model_training_source_quality": summary_model_source_quality,
        "categories": categories,
    }


def _advice_for_item(context: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    advice_path = context.get("advice_path")
    if advice_path is None:
        return context["advice"]
    source_log = item.get("path")
    if not source_log:
        return context["advice"]
    return summarize_advice_for_source(advice_path, str(source_log))


def _source_log_matches(raw_source: Any, expected_source: str) -> bool:
    if not raw_source:
        return False
    actual = _normalize_source_log(raw_source)
    expected = _normalize_source_log(expected_source)
    return actual == expected or actual.endswith("/" + expected) or expected.endswith("/" + actual)


def _normalize_source_log(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().lower()


def summarize_combat_endgame(manifest_path: Path, item: dict[str, Any]) -> dict[str, Any]:
    log_path = _resolve_log_path(manifest_path, item.get("path"))
    if log_path is None:
        return {"available": False, "reason": "no_log_path"}
    if not log_path.exists():
        return {"available": False, "reason": "log_not_found", "path": str(log_path)}

    act1_boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    boss_ids = {_normalize_key(enemy) for enemy in act1_boss.get("enemy_ids") or []}
    boss_cleared = bool(act1_boss.get("cleared"))
    last_summary: dict[str, Any] | None = None
    missed_lethal_summary: dict[str, Any] | None = None
    missed_single_card_search_summary: dict[str, Any] | None = None
    for record in _read_jsonl(log_path):
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        combat = _combat_payload(state)
        if not combat:
            continue
        live_monsters = _live_monsters(combat)
        if not live_monsters:
            continue
        if boss_ids and not any(_monster_matches(monster, boss_ids) for monster in live_monsters):
            continue
        summary = _combat_endgame_frame_summary(log_path, record, state, combat, live_monsters)
        last_summary = summary
        if not boss_cleared:
            if summary.get("missed_lethal"):
                missed_lethal_summary = summary
            elif summary.get("missed_single_card_search"):
                missed_single_card_search_summary = summary

    if last_summary is None:
        return {"available": False, "reason": "no_boss_combat_frame", "path": str(log_path)}
    return missed_lethal_summary or missed_single_card_search_summary or last_summary


def _combat_endgame_frame_summary(
    log_path: Path,
    record: dict[str, Any],
    state: dict[str, Any],
    combat: dict[str, Any],
    live_monsters: list[dict[str, Any]],
) -> dict[str, Any]:
    player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
    target = live_monsters[0] if len(live_monsters) == 1 else None
    kill_cards = _direct_kill_cards(combat, target) if target is not None else []
    chosen = _first_action(record.get("decision") or {})
    chosen_card_index = _safe_int(chosen.get("card_index")) if chosen else None
    played_kill_card = any(card.get("card_index") == chosen_card_index for card in kill_cards)
    single_card_search = _single_card_search_summary(state, combat, chosen)
    return {
        "available": True,
        "path": str(log_path),
        "step": record.get("step"),
        "floor": state.get("floor"),
        "turn": combat.get("turn"),
        "current_hp": player.get("current_hp", state.get("current_hp")),
        "current_energy": player.get("current_energy"),
        "incoming_damage": combat.get("incoming_damage"),
        "enemy_count": len(live_monsters),
        "enemy_id": _monster_label(target) if target is not None else None,
        "enemy_hp": _monster_hp(target) if target is not None else None,
        "kill_cards": kill_cards,
        "chosen_action": chosen.get("action") if chosen else None,
        "chosen_card_index": chosen_card_index if chosen_card_index else None,
        "missed_lethal": bool(kill_cards) and not played_kill_card,
        "single_card_search": single_card_search if single_card_search else None,
        "missed_single_card_search": bool(single_card_search.get("missed")) if single_card_search else False,
    }


def _single_card_search_summary(
    state: dict[str, Any],
    combat: dict[str, Any],
    chosen: dict[str, Any] | None,
) -> dict[str, Any]:
    search_combat = _combat_for_search(combat)
    search_game = dict(state)
    search_game["combat_state"] = search_combat
    result = find_best_combat_sequence(search_game, max_depth=1)
    if result is None or len(result.sequence) != 1:
        return {}
    if not _single_card_search_is_diagnostic(result):
        return {}

    first_action = dict(result.first_action)
    chosen_card_index = _safe_int(chosen.get("card_index")) if chosen else None
    chosen_target_index = _safe_int(chosen.get("target_index")) if chosen else None
    first_card_index = _safe_int(first_action.get("card_index"))
    first_target_index = _safe_int(first_action.get("target_index"))
    target_matches = first_target_index <= 0 or chosen_target_index == first_target_index
    chosen_matches = (
        chosen is not None
        and chosen.get("action") == first_action.get("action")
        and chosen_card_index == first_card_index
        and target_matches
    )

    first_card = _card_by_index(_combat_hand_cards(search_combat), first_card_index)
    return {
        "first_action": first_action,
        "first_card_index": first_card_index if first_card_index else None,
        "first_card_key": result.first_card_key,
        "first_card_name": (first_card or {}).get("name") or result.first_card_key,
        "sequence_card_keys": list(result.sequence_card_keys),
        "initial_loss": result.initial_loss,
        "projected_loss": result.projected_loss,
        "attacks_removed": result.attacks_removed,
        "retaliation_damage": result.retaliation_damage,
        "avoided_lethal": result.avoided_lethal,
        "score": result.score,
        "reason": result.reason,
        "missed": not chosen_matches,
    }


def _primary_manifest_item(manifest: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    for category, item in _manifest_items(manifest):
        return category, item
    return None, {}


def _single_card_search_is_diagnostic(result: Any) -> bool:
    loss_delta = int(result.initial_loss or 0) - int(result.projected_loss or 0)
    if int(result.attacks_removed or 0) > 0:
        return True
    if bool(result.avoided_lethal) and loss_delta > 0:
        return True
    if int(getattr(result, "retaliation_damage", 0) or 0) > 0 and loss_delta > 0:
        return True
    return int(result.initial_loss or 0) >= 10 and loss_delta >= 8


def _manifest_items(manifest: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}
    for category in (INFRA_BLOCKED, DIAGNOSTIC_EXCLUDED, CLEAN_TRAINABLE):
        rows = categories.get(category) or []
        for item in rows:
            if isinstance(item, dict):
                yield category, item


def _failure_attribution(category: str | None, item: dict[str, Any]) -> str:
    if item.get("failure_attribution"):
        return str(item["failure_attribution"])
    if category == INFRA_BLOCKED:
        return "mcp_execution"
    if category == DIAGNOSTIC_EXCLUDED:
        return "diagnostic_incomplete"
    if category == CLEAN_TRAINABLE and item.get("victory") is False:
        floor = _safe_int(item.get("floor"))
        if floor >= 16:
            return "combat_planning"
        return "unknown_clean_failure"
    return "none"


def _next_action(
    category: str | None,
    attribution: str,
    advice: dict[str, Any],
    item: dict[str, Any] | None = None,
    combat_endgame: dict[str, Any] | None = None,
) -> str:
    if category == INFRA_BLOCKED:
        return "fix_execution_layer"
    if (combat_endgame or {}).get("missed_lethal"):
        return "fix_combat_lethal_priority"
    if (combat_endgame or {}).get("missed_single_card_search"):
        return "fix_combat_search_priority"
    if category == DIAGNOSTIC_EXCLUDED:
        boss = (((item or {}).get("validation_evidence") or {}).get("act1_boss") or {})
        if boss.get("cleared"):
            if boss.get("prefix_pristine_clear") is True:
                return "preserve_boss_validation_evidence"
            return "exclude_and_collect_pristine_boss_clear"
        if boss.get("reached"):
            return "exclude_and_collect_terminal_boss_evidence"
        return "exclude_and_collect_terminal_evidence"
    if attribution in {"mcp_execution", "logging_infra"}:
        return "fix_execution_layer"
    if attribution in {"potion_planning", "route_risk", "deck_quality", "combat_planning", "card_selection"}:
        return f"inspect_{attribution}"
    signal = _strongest_advice_category(advice)
    if signal:
        return f"inspect_shadow_{signal}"
    return "inspect_clean_failure"


def _strongest_advice_category(advice: dict[str, Any]) -> str | None:
    if not advice.get("available"):
        return None
    categories = advice.get("categories") if isinstance(advice.get("categories"), dict) else {}
    for category in ("route_risk", "potion_tempo", "pre_boss_deck_quality", "combat_search"):
        summary = categories.get(category) or {}
        if int(summary.get("high_risk_count") or 0) > 0:
            return category
    for category in ("route_risk", "potion_tempo", "pre_boss_deck_quality", "combat_search"):
        summary = categories.get(category) or {}
        if int(summary.get("watch_count") or 0) > 0:
            return category
    return None


def _recommended_assignment(diagnosis: dict[str, Any]) -> dict[str, Any]:
    action = str(diagnosis.get("next_action") or "inspect_clean_failure")
    owner = _owner_for_diagnosis(diagnosis, action)
    contract = _execution_contract_for_owner(owner, action)
    assignment = {
        "owner": owner,
        "action": action,
        "reason": _assignment_reason(diagnosis),
        "execution_contract": contract,
        "manifest_path": diagnosis.get("manifest_path"),
        "source_path": diagnosis.get("path"),
        "status_line": diagnosis.get("status_line"),
        "evidence": _assignment_evidence(diagnosis),
    }
    assignment["assignment_prompt"] = _assignment_prompt(assignment)
    return assignment


def _owner_for_diagnosis(diagnosis: dict[str, Any], action: str) -> str:
    attribution = str(diagnosis.get("failure_attribution") or "")
    if action == "preserve_boss_validation_evidence":
        return "main_agent"
    if action == "keep_for_training":
        return "ai_agent"
    if action.startswith("exclude_and_collect"):
        return "runner_agent"
    if action == "fix_execution_layer":
        return "engineering_agent"
    if action in {"fix_combat_lethal_priority", "fix_combat_search_priority"}:
        return "ai_agent"
    if action.startswith("inspect_shadow_") or action.startswith("inspect_"):
        return "ai_agent" if attribution in AI_ATTRIBUTIONS else _owner_for_attribution(attribution)
    return _owner_for_attribution(attribution)


def _owner_for_attribution(attribution: str) -> str:
    if attribution in AI_ATTRIBUTIONS:
        return "ai_agent"
    if attribution in ENGINEERING_ATTRIBUTIONS:
        return "engineering_agent"
    return "main_agent"


def _execution_contract_for_owner(owner: str, action: str) -> dict[str, Any]:
    if owner == "runner_agent":
        live_gated = action.startswith("exclude_and_collect") or action.startswith("collect")
        return {
            "mode": "live_mcp_gated_collection" if live_gated else "read_only_flow_monitoring",
            "requires_live_mcp_ownership": live_gated,
            "allowed_operations": ["read_logs", "refresh_manifest_gate", "prepare_probe_command"],
            "forbidden_operations": ["edit_code", "train_models", "control_live_mcp_without_explicit_ownership"],
        }
    if owner == "engineering_agent":
        return {
            "mode": "offline_code_or_data_infra",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["inspect_manifest", "inspect_logs", "edit_runner_or_data_layer", "add_tests"],
            "forbidden_operations": ["control_live_mcp_without_explicit_ownership", "train_strategy_models"],
        }
    if owner == "ai_agent":
        return {
            "mode": "offline_strategy_or_shadow_model",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["inspect_manifest", "inspect_shadow_advice", "train_shadow_models_to_scratch_dir"],
            "forbidden_operations": ["control_live_mcp", "replace_live_policy_with_model"],
        }
    if owner == "main_agent":
        return {
            "mode": "coordination_only",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["merge_evidence", "choose_next_handoff", "decide_acceptance"],
            "forbidden_operations": ["control_live_mcp_unless_ownership_changes"],
        }
    return {
        "mode": "unknown_owner",
        "requires_live_mcp_ownership": False,
        "allowed_operations": ["inspect_manifest", "read_logs"],
        "forbidden_operations": ["control_live_mcp_without_explicit_ownership"],
    }


def _assignment_reason(diagnosis: dict[str, Any]) -> str:
    category = diagnosis.get("category") or "unknown"
    attribution = diagnosis.get("failure_attribution") or "none"
    reason = diagnosis.get("reason") or "no_reason"
    return f"{category}/{attribution}/{reason}"


def _assignment_evidence(diagnosis: dict[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "category": diagnosis.get("category"),
        "reason": diagnosis.get("reason"),
        "failure_attribution": diagnosis.get("failure_attribution"),
        "validation_grade": diagnosis.get("validation_grade"),
        "recovered_actions": diagnosis.get("recovered_actions"),
        "failed_actions": diagnosis.get("failed_actions"),
    }
    for key in ("action_recovery_summary", "failure_evidence", "act1_boss", "combat_endgame"):
        value = diagnosis.get(key)
        if value not in (None, "", [], {}):
            evidence[key] = value
    feature_gaps = diagnosis.get("shadow_feature_gaps") if isinstance(diagnosis.get("shadow_feature_gaps"), dict) else {}
    if feature_gaps:
        evidence["shadow_feature_gaps"] = _compact_feature_gap_evidence(feature_gaps)
    label_quality = diagnosis.get("shadow_label_quality") if isinstance(diagnosis.get("shadow_label_quality"), dict) else {}
    label_text = _label_quality_suffix(label_quality).lstrip("; ")
    if label_text:
        evidence["shadow_label_quality"] = label_text
    combat_quality = (
        label_quality.get("combat_search_labels")
        if isinstance(label_quality.get("combat_search_labels"), dict)
        else {}
    )
    examples = combat_quality.get("exclusion_examples") if isinstance(combat_quality.get("exclusion_examples"), list) else []
    if examples:
        evidence["label_exclusion_examples"] = examples[:3]
    advice = diagnosis.get("shadow_advice") if isinstance(diagnosis.get("shadow_advice"), dict) else {}
    advice_text = _advice_suffix(advice).lstrip("; ")
    if advice_text:
        evidence["shadow_advice"] = advice_text
    return {key: value for key, value in evidence.items() if value not in (None, "", [], {})}


def _compact_feature_gap_evidence(summary: dict[str, Any]) -> dict[str, Any]:
    result = {
        "available": summary.get("available"),
        "total_rows": summary.get("total_rows"),
        "gap_count": summary.get("gap_count"),
        "zero_count": summary.get("zero_count"),
        "unknown_static_total": summary.get("unknown_static_total"),
    }
    if summary.get("reason"):
        result["reason"] = summary.get("reason")
    unknown = summary.get("unknown_static_features") if isinstance(summary.get("unknown_static_features"), dict) else {}
    if unknown:
        result["unknown_static_features"] = unknown
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _assignment_prompt(assignment: dict[str, Any]) -> str:
    contract = assignment.get("execution_contract") if isinstance(assignment.get("execution_contract"), dict) else {}
    lines = [
        "Run diagnosis assignment",
        f"Agent: {assignment.get('owner') or 'unknown'}",
        f"Action: {assignment.get('action') or 'none'}",
        f"Reason: {assignment.get('reason') or 'none'}",
        f"Execution mode: {contract.get('mode') or 'unknown'}",
        f"Requires live MCP ownership: {bool(contract.get('requires_live_mcp_ownership'))}",
    ]
    if assignment.get("manifest_path"):
        lines.append(f"Manifest: {assignment.get('manifest_path')}")
    if assignment.get("source_path"):
        lines.append(f"Source log: {assignment.get('source_path')}")
    if assignment.get("status_line"):
        lines.append(f"Status: {assignment.get('status_line')}")
    allowed = contract.get("allowed_operations") if isinstance(contract.get("allowed_operations"), list) else []
    if allowed:
        lines.append("Allowed operations: " + ", ".join(str(item) for item in allowed))
    forbidden = contract.get("forbidden_operations") if isinstance(contract.get("forbidden_operations"), list) else []
    if forbidden:
        lines.append("Forbidden operations: " + ", ".join(str(item) for item in forbidden))
    evidence = assignment.get("evidence") if isinstance(assignment.get("evidence"), dict) else {}
    if evidence:
        lines.append("Evidence: " + _prompt_json(evidence))
    return "\n".join(lines)


def _assignment_output_text(diagnoses: list[dict[str, Any]]) -> str:
    prompts = []
    for diagnosis in diagnoses:
        assignment = diagnosis.get("recommended_assignment") if isinstance(diagnosis.get("recommended_assignment"), dict) else {}
        prompt = assignment.get("assignment_prompt")
        if prompt:
            prompts.append(str(prompt).rstrip())
    return "\n\n---\n\n".join(prompts)


def _prompt_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _status_line(diagnosis: dict[str, Any]) -> str:
    character = diagnosis.get("character") or "?"
    ascension = diagnosis.get("ascension")
    ascension_text = f"A{ascension}" if ascension is not None else "A?"
    floor = diagnosis.get("floor")
    category = diagnosis.get("category") or "unknown"
    reason = diagnosis.get("reason") or "no_reason"
    attribution = diagnosis.get("failure_attribution") or "none"
    recovered = diagnosis.get("recovered_actions") or 0
    failed = diagnosis.get("failed_actions") or 0
    recovery_suffix = _recovery_suffix(diagnosis.get("action_recovery_summary") or {})
    validation_suffix = _validation_suffix(diagnosis)
    failure_suffix = _failure_evidence_suffix(diagnosis.get("failure_evidence") or {})
    advice_suffix = _advice_suffix(diagnosis.get("shadow_advice") or {})
    endgame_suffix = _combat_endgame_suffix(diagnosis.get("combat_endgame") or {})
    feature_suffix = _feature_coverage_suffix(diagnosis.get("shadow_feature_gaps") or {})
    label_quality_suffix = _label_quality_suffix(diagnosis.get("shadow_label_quality") or {})
    return (
        f"{category}/{attribution}: {character} {ascension_text} floor {floor} "
        f"({reason}; recovered={recovered}, failed={failed}{recovery_suffix}{validation_suffix}{failure_suffix}{endgame_suffix}{feature_suffix}{label_quality_suffix}) "
        f"-> {diagnosis['next_action']}{advice_suffix}"
    )


def _recovery_suffix(summary: dict[str, Any]) -> str:
    kinds = summary.get("by_kind") if isinstance(summary.get("by_kind"), dict) else {}
    if not kinds:
        return ""
    top_kind, top_count = max(kinds.items(), key=lambda item: (int(item[1] or 0), str(item[0])))
    return f", recovery={top_kind}:{top_count}"


def _validation_suffix(diagnosis: dict[str, Any]) -> str:
    parts: list[str] = []
    grade = diagnosis.get("validation_grade")
    if grade:
        parts.append(f"validation={grade}")
    boss = diagnosis.get("act1_boss") if isinstance(diagnosis.get("act1_boss"), dict) else {}
    if boss and boss.get("reached"):
        enemy = _first_enemy_label(boss)
        state = "cleared" if boss.get("cleared") else "reached"
        detail = f"act1_boss={state}"
        if enemy:
            detail += f":{enemy}"
        last_turn = boss.get("last_turn")
        last_hp = boss.get("last_hp")
        potion_steps = boss.get("potion_use_steps") if isinstance(boss.get("potion_use_steps"), list) else []
        clear_step = boss.get("clear_step")
        prefix_pristine = boss.get("prefix_pristine_clear") if "prefix_pristine_clear" in boss else None
        prefix_blockers = boss.get("prefix_blockers") if isinstance(boss.get("prefix_blockers"), list) else []
        extras = []
        if last_turn is not None:
            extras.append(f"T{last_turn}")
        if last_hp is not None:
            extras.append(f"hp={last_hp}")
        if potion_steps:
            extras.append(f"potions={len(potion_steps)}")
        if clear_step is not None:
            extras.append(f"clear_step={clear_step}")
        if prefix_pristine is not None:
            extras.append(f"prefix_pristine={str(bool(prefix_pristine)).lower()}")
        if prefix_blockers:
            extras.append("prefix_blockers=" + "|".join(str(blocker) for blocker in prefix_blockers[:3]))
        if extras:
            detail += "[" + ",".join(extras) + "]"
        parts.append(detail)
    return "; " + "; ".join(parts) if parts else ""


def _failure_evidence_suffix(evidence: dict[str, Any]) -> str:
    stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
    mcp_read = evidence.get("mcp_read") if isinstance(evidence.get("mcp_read"), dict) else {}
    action_error = evidence.get("action_error") if isinstance(evidence.get("action_error"), dict) else {}
    synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
    suffixes = []
    if stall:
        suffixes.append(_screen_stall_suffix(stall))
    if mcp_read:
        suffixes.append(_mcp_read_suffix(mcp_read))
    if action_error:
        suffixes.append(_action_error_suffix(action_error))
    if synthetic or evidence.get("terminal_outcome_source"):
        suffixes.append(_synthetic_terminal_suffix(synthetic, evidence.get("terminal_outcome_source")))
    return "".join(suffix for suffix in suffixes if suffix)


def _screen_stall_suffix(stall: dict[str, Any]) -> str:
    screen = stall.get("screen_type") or "unknown_screen"
    repeats = stall.get("repeat_count")
    floor = stall.get("floor")
    first_step = stall.get("first_step")
    last_step = stall.get("last_step")
    detail = f"; screen_stall={screen}"
    if repeats is not None:
        detail += f"x{repeats}"
    extras = []
    if floor is not None:
        extras.append(f"F{floor}")
    if first_step is not None or last_step is not None:
        extras.append(f"steps={first_step}->{last_step}")
    for key, label in (
        ("last_relic_count", "relics"),
        ("last_reward_count", "rewards"),
        ("last_card_count", "cards"),
    ):
        if stall.get(key) is not None:
            extras.append(f"{label}={stall.get(key)}")
            break
    actions = stall.get("last_actions") if isinstance(stall.get("last_actions"), list) else []
    if actions:
        first_action = actions[0] if isinstance(actions[0], dict) else {}
        action = first_action.get("action")
        if action:
            if first_action.get("choice_index") is not None:
                extras.append(f"action={action}:{first_action.get('choice_index')}")
            else:
                extras.append(f"action={action}")
    if extras:
        detail += "[" + ",".join(str(item) for item in extras[:5]) + "]"
    return detail


def _mcp_read_suffix(read: dict[str, Any]) -> str:
    step = read.get("step")
    detail = "; mcp_read"
    if step is not None:
        detail += f"=step{step}"
    extras = []
    status = read.get("diagnostics_status")
    if status is not None:
        extras.append(f"status={status}")
    state = read.get("last_state") if isinstance(read.get("last_state"), dict) else {}
    if state:
        screen = state.get("screen_type")
        floor = state.get("floor")
        phase = state.get("room_phase")
        if screen is not None:
            state_bits = [str(screen)]
            if floor is not None:
                state_bits.append(f"F{floor}")
            if phase:
                state_bits.append(str(phase))
            extras.append("/".join(state_bits))
    if extras:
        detail += "[" + ",".join(str(item) for item in extras[:3]) + "]"
    return detail


def _action_error_suffix(action: dict[str, Any]) -> str:
    kind = action.get("kind") or action.get("action_status") or "action"
    step = action.get("step")
    detail = f"; action_error={kind}"
    if step is not None:
        detail += f"@{step}"
    return detail


def _synthetic_terminal_suffix(synthetic: dict[str, Any], outcome_source: Any) -> str:
    step = synthetic.get("step")
    detail = "; synthetic_terminal"
    if step is not None:
        detail += f"=step{step}"
    extras = []
    source = synthetic.get("source")
    if source is not None:
        extras.append(f"source={source}")
    if outcome_source:
        extras.append(f"outcome={outcome_source}")
    if "terminal_recovery_attempted" in synthetic:
        recovered = bool(synthetic.get("terminal_recovery_succeeded"))
        extras.append(f"recover={'ok' if recovered else 'failed'}")
    post_status = synthetic.get("post_recovery_status")
    if post_status is not None:
        extras.append(f"post={post_status}")
    if extras:
        detail += "[" + ",".join(str(item) for item in extras[:4]) + "]"
    return detail


def _first_enemy_label(boss: dict[str, Any]) -> str | None:
    enemies = boss.get("enemy_ids")
    if isinstance(enemies, list) and enemies:
        return str(enemies[0])
    return None


def _advice_suffix(advice: dict[str, Any]) -> str:
    if not advice.get("available"):
        return f"; advice={advice.get('reason', 'missing')}"
    categories = advice.get("categories") if isinstance(advice.get("categories"), dict) else {}
    parts = []
    for category in ("route_risk", "potion_tempo", "pre_boss_deck_quality", "combat_search"):
        summary = categories.get(category) or {}
        high = int(summary.get("high_risk_count") or 0)
        watch = int(summary.get("watch_count") or 0)
        if high or watch:
            parts.append(f"{category}:high={high},watch={watch}")
    return (
        "; advice="
        + (", ".join(parts) if parts else "no_signals")
        + _top_signal_suffix(categories)
        + _model_skip_suffix(categories)
    )


def _top_signal_suffix(categories: dict[str, Any]) -> str:
    parts: list[str] = []
    for category in ("potion_tempo", "combat_search"):
        summary = categories.get(category) if isinstance(categories.get(category), dict) else {}
        high = int(summary.get("high_risk_count") or 0)
        watch = int(summary.get("watch_count") or 0)
        if high <= 0 and watch <= 0:
            continue
        top_signal = summary.get("top_signal") if isinstance(summary.get("top_signal"), dict) else {}
        compact = _compact_top_signal(category, top_signal)
        if compact:
            parts.append(f"{category}[{compact}]")
    return "; signals=" + ";".join(parts) if parts else ""


def _compact_top_signal(category: str, signal: dict[str, Any]) -> str:
    if category == "potion_tempo":
        pieces: list[str] = []
        if signal.get("incoming") is not None:
            pieces.append(f"inc={signal.get('incoming')}")
        potions = signal.get("potion_ids") if isinstance(signal.get("potion_ids"), list) else []
        if potions:
            pieces.append("potions=" + "|".join(str(potion) for potion in potions[:3]))
        enemies = signal.get("enemy_ids") if isinstance(signal.get("enemy_ids"), list) else []
        if enemies:
            pieces.append("enemy=" + str(enemies[0]))
        if "used_potion" in signal:
            pieces.append(f"used={str(bool(signal.get('used_potion'))).lower()}")
        for key, label in (
            ("potion_block_value", "block"),
            ("potion_damage_value", "damage"),
            ("potion_draw_value", "draw"),
            ("potion_vulnerable_value", "vuln"),
            ("potion_weak_value", "weak"),
            ("enemy_total_expected_attack", "enemy_attack"),
        ):
            value = signal.get(key)
            if _safe_int(value) > 0:
                pieces.append(f"{label}={value}")
        if signal.get("boss_identity_known"):
            boss_count = signal.get("boss_known_count")
            pieces.append(f"boss_known={boss_count if boss_count is not None else 1}")
        return ",".join(pieces[:8])
    if category == "combat_search":
        pieces = []
        if signal.get("label_first_card_key"):
            pieces.append(f"label={signal.get('label_first_card_key')}")
        if signal.get("model_top_card_key"):
            pieces.append(f"model={signal.get('model_top_card_key')}")
        if signal.get("loss_delta") is not None:
            pieces.append(f"loss_delta={signal.get('loss_delta')}")
        if signal.get("attacks_removed") is not None:
            pieces.append(f"attacks_removed={signal.get('attacks_removed')}")
        retaliation = _safe_int(signal.get("retaliation_damage"))
        if retaliation > 0:
            pieces.append(f"retaliation={retaliation}")
        if signal.get("label_missed_direct_kill"):
            pieces.append("missed_direct_kill=true")
        if signal.get("label_missed_single_card_search"):
            pieces.append("missed_search=true")
        return ",".join(pieces[:7])
    return ""


def _model_skip_suffix(categories: dict[str, Any]) -> str:
    parts: list[str] = []
    for category in ("route_risk", "potion_tempo", "pre_boss_deck_quality", "combat_search"):
        summary = categories.get(category) if isinstance(categories.get(category), dict) else {}
        load_quality = summary.get("model_load_quality") if isinstance(summary.get("model_load_quality"), dict) else {}
        skipped = int(load_quality.get("skipped") or 0)
        if skipped <= 0:
            continue
        reasons = load_quality.get("skip_reasons") if isinstance(load_quality.get("skip_reasons"), dict) else {}
        if reasons:
            top_reason, top_count = max(reasons.items(), key=lambda item: (int(item[1] or 0), str(item[0])))
            parts.append(f"{category}:{skipped}/{top_reason}:{top_count}")
        else:
            parts.append(f"{category}:{skipped}")
    return "; model_skips=" + ",".join(parts) if parts else ""


def _combat_endgame_suffix(endgame: dict[str, Any]) -> str:
    if not endgame.get("available"):
        return ""
    if endgame.get("missed_lethal"):
        cards = endgame.get("kill_cards") if isinstance(endgame.get("kill_cards"), list) else []
        card = cards[0] if cards else {}
        card_name = card.get("name") or card.get("id") or "unknown_card"
        enemy = endgame.get("enemy_id") or "unknown_enemy"
        hp = endgame.get("enemy_hp")
        return f"; missed_lethal={card_name}->{enemy} hp={hp}"
    if endgame.get("missed_single_card_search"):
        search = endgame.get("single_card_search") if isinstance(endgame.get("single_card_search"), dict) else {}
        card_name = search.get("first_card_name") or search.get("first_card_key") or "unknown_card"
        enemy = endgame.get("enemy_id") or "unknown_enemy"
        initial = search.get("initial_loss")
        projected = search.get("projected_loss")
        attacks_removed = search.get("attacks_removed")
        suffix = f"; missed_search={card_name}->{enemy} loss={initial}->{projected} attacks_removed={attacks_removed}"
        retaliation = int(search.get("retaliation_damage") or 0)
        if retaliation > 0:
            suffix += f" retaliation={retaliation}"
        return suffix
    return ""


def _feature_coverage_suffix(summary: dict[str, Any]) -> str:
    if not summary.get("available"):
        reason = summary.get("reason")
        return f"; features={reason}" if reason else ""
    total_rows = int(summary.get("total_rows") or 0)
    if total_rows <= 0:
        return "; features=0rows"
    parts = [f"features={total_rows}rows"]
    categories = summary.get("categories") if isinstance(summary.get("categories"), dict) else {}
    gap_parts: list[str] = []
    zero_parts: list[str] = []
    unknown_parts: list[str] = []
    for category in ("route_risk", "potion_tempo", "pre_boss_deck_quality", "combat_search"):
        category_summary = categories.get(category) if isinstance(categories.get(category), dict) else {}
        missing = category_summary.get("missing_prefixes") if isinstance(category_summary.get("missing_prefixes"), list) else []
        zero = category_summary.get("zero_prefixes") if isinstance(category_summary.get("zero_prefixes"), list) else []
        unknown = (
            category_summary.get("unknown_static_features")
            if isinstance(category_summary.get("unknown_static_features"), dict)
            else {}
        )
        if missing:
            gap_parts.append(f"{category}:" + "|".join(str(prefix) for prefix in missing[:3]))
        if zero:
            zero_parts.append(f"{category}:" + "|".join(str(prefix) for prefix in zero[:3]))
        if unknown:
            top_unknown = sorted(
                ((str(field), _safe_int(total)) for field, total in unknown.items() if _safe_int(total) > 0),
                key=lambda item: (-item[1], item[0]),
            )
            if top_unknown:
                unknown_parts.append(f"{category}:" + "|".join(f"{field}:{total}" for field, total in top_unknown[:3]))
    if gap_parts:
        parts.append("feature_gaps=" + ",".join(gap_parts))
    if zero_parts:
        parts.append("feature_zero=" + ",".join(zero_parts))
    if unknown_parts:
        parts.append("feature_unknown=" + ",".join(unknown_parts))
    return "; " + "; ".join(parts)


def _label_quality_suffix(summary: dict[str, Any]) -> str:
    combat = summary.get("combat_search_labels") if isinstance(summary.get("combat_search_labels"), dict) else {}
    excluded = int(combat.get("excluded_from_training") or 0)
    if excluded <= 0:
        return ""
    reasons = combat.get("exclusion_reasons") if isinstance(combat.get("exclusion_reasons"), dict) else {}
    if not reasons:
        return f"; label_exclusions=combat_search:{excluded}"
    top_reason, top_count = max(reasons.items(), key=lambda item: (int(item[1] or 0), str(item[0])))
    return f"; label_exclusions=combat_search:{excluded}/{top_reason}:{top_count}"


def _manifest_advice_path(manifest: dict[str, Any]) -> Path | None:
    advice = manifest.get("shadow_advice") if isinstance(manifest.get("shadow_advice"), dict) else {}
    path = advice.get("path")
    return Path(path) if path else None


def _resolve_log_path(manifest_path: Path, raw_path: Any) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if path.is_absolute():
        return path
    if path.exists():
        return path
    return manifest_path.parent / path


def _combat_payload(state: dict[str, Any]) -> dict[str, Any]:
    combat = state.get("combat_state")
    if isinstance(combat, dict) and combat:
        return combat
    combat = state.get("combat")
    if isinstance(combat, dict) and combat:
        return combat
    return {}


def _live_monsters(combat: dict[str, Any]) -> list[dict[str, Any]]:
    monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
    live = []
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        if monster.get("is_dead") or monster.get("is_gone"):
            continue
        if _monster_hp(monster) <= 0:
            continue
        live.append(monster)
    return live


def _direct_kill_cards(combat: dict[str, Any], target: dict[str, Any] | None) -> list[dict[str, Any]]:
    if target is None:
        return []
    player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
    energy = _safe_int(player.get("current_energy"))
    target_hp_with_block = _monster_hp(target) + _safe_int(target.get("block"))
    cards = _combat_hand_cards(combat)
    kill_cards: list[dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            continue
        if not card.get("is_playable", True):
            continue
        if str(card.get("type") or "").upper() != "ATTACK":
            continue
        cost = _card_cost(card, energy)
        if cost is None or cost > energy:
            continue
        damage = _safe_int(card.get("damage"))
        if damage < target_hp_with_block:
            continue
        kill_cards.append(
            {
                "card_index": index,
                "id": card.get("id"),
                "name": card.get("name") or card.get("id"),
                "damage": damage,
                "cost": cost,
            }
        )
    return kill_cards


def _combat_hand_cards(combat: dict[str, Any]) -> list[Any]:
    hand_cards = combat.get("hand_cards")
    if isinstance(hand_cards, list) and hand_cards:
        return hand_cards
    hand = combat.get("hand")
    return hand if isinstance(hand, list) else []


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


def _card_by_index(cards: list[Any], index: int) -> dict[str, Any] | None:
    if index <= 0 or index > len(cards):
        return None
    card = cards[index - 1]
    return card if isinstance(card, dict) else None


def _card_cost(card: dict[str, Any], energy: int) -> int | None:
    cost = _safe_int(card.get("cost"))
    if cost == -1:
        return energy if energy > 0 else None
    if cost < 0:
        return 0
    return cost


def _first_action(decision: dict[str, Any]) -> dict[str, Any] | None:
    actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
    for action in actions:
        if isinstance(action, dict):
            return action
    return None


def _monster_hp(monster: dict[str, Any] | None) -> int:
    if monster is None:
        return 0
    return _safe_int(monster.get("current_hp", monster.get("hp")))


def _monster_label(monster: dict[str, Any] | None) -> str | None:
    if monster is None:
        return None
    value = monster.get("id") or monster.get("name")
    return str(value) if value else None


def _monster_matches(monster: dict[str, Any], expected: set[str]) -> bool:
    return _normalize_key(monster.get("id")) in expected or _normalize_key(monster.get("name")) in expected


def _normalize_key(value: Any) -> str:
    return str(value or "").replace(" ", "").lower()


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _read_advice_summary(advice_dir: Path) -> dict[str, Any]:
    path = advice_dir / "summary.json"
    if not path.exists():
        return {}
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return summary if isinstance(summary, dict) else {}


def _category_score(category: str, row: dict[str, Any]) -> float | None:
    field = {
        "route_risk": "route_risk_score",
        "potion_tempo": "potion_tempo_score",
        "pre_boss_deck_quality": "deck_quality_score",
        "combat_search": "combat_search_score",
    }[category]
    value = row.get(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stronger_signal(category: str, score: float, previous: float) -> bool:
    if category == "pre_boss_deck_quality":
        return score < previous
    return score > previous


def _top_signal_details(category: str, row: dict[str, Any]) -> dict[str, Any]:
    if category == "potion_tempo":
        details = {
            "incoming": row.get("incoming"),
            "potion_ids": row.get("potion_ids"),
            "used_potion": row.get("used_potion"),
            "used_potion_slot": row.get("used_potion_slot"),
            "used_potion_targeted": row.get("used_potion_targeted"),
            "enemy_ids": row.get("enemy_ids"),
            "potion_block_value": row.get("potion_block_value"),
            "potion_damage_value": row.get("potion_damage_value"),
            "potion_draw_value": row.get("potion_draw_value"),
            "potion_vulnerable_value": row.get("potion_vulnerable_value"),
            "potion_weak_value": row.get("potion_weak_value"),
            "enemy_total_expected_attack": row.get("enemy_total_expected_attack"),
            "enemy_boss_count": row.get("enemy_boss_count"),
            "boss_identity_known": row.get("boss_identity_known"),
            "boss_known_count": row.get("boss_known_count"),
        }
        return {key: value for key, value in details.items() if value not in (None, "", [], {})}
    if category != "combat_search":
        return {}
    details = {
        "label_first_card_key": row.get("label_first_card_key"),
        "model_top_card_key": row.get("model_top_card_key"),
        "loss_delta": row.get("loss_delta"),
        "attacks_removed": row.get("attacks_removed"),
        "retaliation_damage": row.get("retaliation_damage"),
        "label_missed_direct_kill": row.get("label_missed_direct_kill"),
        "label_missed_single_card_search": row.get("label_missed_single_card_search"),
    }
    return {key: value for key, value in details.items() if value not in (None, "", [], {})}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
