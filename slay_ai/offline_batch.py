"""Replay completed logs through the offline evidence pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .act1_boss_gate import build_report, status_line as gate_status_line
from .combat_label_audit import build_audit_report as build_combat_label_audit
from .combat_label_audit import compact_audit_report as compact_combat_label_audit
from .combat_label_replay_audit import build_replay_report as build_combat_label_replay_audit
from .combat_label_replay_audit import compact_replay_report as compact_combat_label_replay_audit
from .model import COMBAT_SEARCH_MODEL_PATH, DECK_QUALITY_MODEL_PATH, POTION_TEMPO_MODEL_PATH, ROUTE_RISK_MODEL_PATH
from .run_diagnosis import diagnose_manifest_runs
from .shadow_advice import ShadowModels, score_shadow_inputs, write_advice
from .shadow_inputs import resolve_shadow_training_source
from .static_knowledge import StaticKnowledge
from .static_knowledge_gaps import build_gap_report, compact_gap_report
from .training_manifest import build_manifest, iter_log_files, write_shadow_examples

ENGINEERING_ATTRIBUTIONS = {"diagnostic_incomplete", "logging_infra", "mcp_execution", "unknown_clean_failure"}
AI_ATTRIBUTIONS = {"card_selection", "combat_planning", "deck_quality", "potion_planning", "route_risk"}
RUNNER_GATE_ACTIONS = {
    "collect_a0_manifest_batch": ("collect_a0_manifest_batch", "act1_boss_gate_needs_manifest_batch"),
    "collect_pristine_act1_boss_clears": (
        "collect_pristine_act1_boss_clears",
        "act1_boss_gate_needs_pristine_clear",
    ),
    "promote_to_next_validation_batch": ("collect_next_validation_batch", "act1_boss_gate_passed"),
}
AI_GATE_ACTIONS = {
    "improve_act1_boss_combat": ("improve_act1_boss_combat", "act1_boss_gate_needs_clear", 0),
    "improve_route_and_early_act1_survival": (
        "improve_route_and_early_act1_survival",
        "act1_boss_gate_needs_reach",
        1,
    ),
}
COMBAT_LABEL_REPAIR_ACTIONS = {"fix_combat_lethal_priority", "fix_combat_search_priority"}
COMBAT_LABEL_GATE_DATA_QUALITY_FIELDS = (
    "label_exclusion_manifests",
    "shadow_label_excluded",
    "missed_single_card_search_labels",
)
AGENT_OWNERSHIP_CONTRACTS = {
    "runner_agent": {
        "role": "flow/data collection",
        "owns": ["run monitoring", "A0/A1 validation batches", "manifest/advice/gate refresh"],
        "live_mcp_policy": "may control live MCP only when explicitly assigned ownership",
    },
    "engineering_agent": {
        "role": "execution/data infrastructure",
        "owns": ["MCP recovery", "runner preflight", "logging", "manifest classification"],
        "live_mcp_policy": "stay offline unless debugging is explicitly assigned",
    },
    "ai_agent": {
        "role": "strategy analysis and shadow models",
        "owns": ["failure analysis", "shadow advice", "model training", "combat-search label review"],
        "live_mcp_policy": "do not control live MCP",
    },
    "main_agent": {
        "role": "coordination and merge judgment",
        "owns": ["handoff selection", "scope arbitration", "acceptance decisions"],
        "live_mcp_policy": "do not control live MCP unless ownership changes",
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build manifest, shadow rows, advice, diagnosis, and Act 1 boss gate from existing logs only."
    )
    parser.add_argument("logs", nargs="+", type=Path, help="JSONL logs or directories containing JSONL logs.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for all generated offline artifacts.")
    parser.add_argument("--name", default="batch", help="Artifact name suffix, for example probe94_106_a0.")
    parser.add_argument("--knowledge-dir", type=Path, default=Path("data") / "static_knowledge")
    parser.add_argument("--no-knowledge", action="store_true", help="Do not load static knowledge features.")
    parser.add_argument("--skip-advice", action="store_true", help="Do not score shadow advice.")
    parser.add_argument("--skip-diagnosis", action="store_true", help="Do not write run diagnosis.")
    parser.add_argument("--skip-gate", action="store_true", help="Do not write Act 1 boss gate report.")
    parser.add_argument("--route-model-path", type=Path, default=ROUTE_RISK_MODEL_PATH)
    parser.add_argument("--potion-model-path", type=Path, default=POTION_TEMPO_MODEL_PATH)
    parser.add_argument("--deck-model-path", type=Path, default=DECK_QUALITY_MODEL_PATH)
    parser.add_argument("--combat-model-path", type=Path, default=COMBAT_SEARCH_MODEL_PATH)
    parser.add_argument("--character", default="IRONCLAD")
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--min-reached", type=int, default=3)
    parser.add_argument("--min-cleared", type=int, default=2)
    parser.add_argument("--min-pristine-cleared", type=int, default=2)
    args = parser.parse_args(argv)

    summary = run_offline_batch(
        args.logs,
        output_dir=args.output_dir,
        name=args.name,
        knowledge_dir=None if args.no_knowledge else args.knowledge_dir,
        write_shadow_advice=not args.skip_advice,
        write_diagnosis=not args.skip_diagnosis,
        write_gate=not args.skip_gate,
        route_model_path=args.route_model_path,
        potion_model_path=args.potion_model_path,
        deck_model_path=args.deck_model_path,
        combat_model_path=args.combat_model_path,
        character=args.character,
        ascension=args.ascension,
        min_reached=args.min_reached,
        min_cleared=args.min_cleared,
        min_pristine_cleared=args.min_pristine_cleared,
    )
    print(f"Wrote offline batch summary: {summary['summary_path']}")
    print(f"Manifest: {summary['manifest_path']}")
    if summary.get("artifact_manifest_path"):
        print(f"Artifact manifest: {summary['artifact_manifest_path']}")
    if summary.get("diagnosis_status_line"):
        print(summary["diagnosis_status_line"])
    if summary.get("gate_status_line"):
        print(summary["gate_status_line"])
    if summary.get("offline_batch_next_line"):
        print(summary["offline_batch_next_line"])
    return 0


def run_offline_batch(
    logs: list[Path],
    *,
    output_dir: Path,
    name: str = "batch",
    knowledge_dir: Path | None = Path("data") / "static_knowledge",
    write_shadow_advice: bool = True,
    write_diagnosis: bool = True,
    write_gate: bool = True,
    route_model_path: Path = ROUTE_RISK_MODEL_PATH,
    potion_model_path: Path = POTION_TEMPO_MODEL_PATH,
    deck_model_path: Path = DECK_QUALITY_MODEL_PATH,
    combat_model_path: Path = COMBAT_SEARCH_MODEL_PATH,
    character: str = "IRONCLAD",
    ascension: int = 0,
    min_reached: int = 3,
    min_cleared: int = 2,
    min_pristine_cleared: int = 2,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_name = _safe_artifact_name(name)
    manifest_path = output_dir / f"training_manifest_{artifact_name}.json"
    shadow_dir = output_dir / f"shadow_{artifact_name}"
    advice_dir = output_dir / f"shadow_advice_{artifact_name}"
    diagnosis_path = output_dir / f"run_diagnosis_{artifact_name}.json"
    gate_path = output_dir / f"act1_boss_gate_{artifact_name}.json"
    summary_path = output_dir / f"offline_batch_{artifact_name}.json"
    static_knowledge_gap_report_path = output_dir / f"static_knowledge_gaps_{artifact_name}.json"
    combat_label_audit_path = output_dir / f"combat_label_audit_{artifact_name}.json"
    combat_label_replay_audit_path = output_dir / f"combat_label_replay_audit_{artifact_name}.json"
    artifact_manifest_path = output_dir / f"artifact_manifest_{artifact_name}.json"
    handoff_dir = output_dir / f"handoff_{artifact_name}"
    handoff_index_path = handoff_dir / "index.json"
    next_handoff_prompt_path = handoff_dir / "next_handoff.txt"
    resolved_log_paths = list(iter_log_files(logs))
    resolved_logs = [str(path) for path in resolved_log_paths]
    offline_refresh_command = _offline_refresh_command(
        logs,
        resolved_logs=resolved_log_paths,
        output_dir=output_dir,
        artifact_name=artifact_name,
        knowledge_dir=knowledge_dir,
        write_shadow_advice=write_shadow_advice,
        write_diagnosis=write_diagnosis,
        write_gate=write_gate,
        route_model_path=route_model_path,
        potion_model_path=potion_model_path,
        deck_model_path=deck_model_path,
        combat_model_path=combat_model_path,
        character=character,
        ascension=ascension,
        min_reached=min_reached,
        min_cleared=min_cleared,
        min_pristine_cleared=min_pristine_cleared,
    )

    knowledge = StaticKnowledge.load(knowledge_dir) if knowledge_dir else None
    static_knowledge_gap_report: dict[str, Any] | None = None
    if knowledge is not None:
        static_knowledge_gap_report = build_gap_report(
            resolved_log_paths,
            knowledge=knowledge,
            knowledge_dir=knowledge_dir,
        )
        _write_ascii_json(static_knowledge_gap_report_path, static_knowledge_gap_report)
    manifest, shadow_examples = build_manifest(resolved_log_paths, knowledge=knowledge, knowledge_dir=knowledge_dir)
    write_shadow_examples(shadow_dir, shadow_examples)
    combat_label_audit_report = build_combat_label_audit([shadow_dir], source_quality="all")
    _write_ascii_json(combat_label_audit_path, combat_label_audit_report)
    combat_label_replay_audit_report = build_combat_label_replay_audit([shadow_dir], source_quality="all")
    _write_ascii_json(combat_label_replay_audit_path, combat_label_replay_audit_report)

    advice_summary: dict[str, Any] | None = None
    if write_shadow_advice:
        models = ShadowModels.load(
            route_model_path=route_model_path,
            potion_model_path=potion_model_path,
            deck_model_path=deck_model_path,
            combat_model_path=combat_model_path,
        )
        advice = score_shadow_inputs([shadow_dir], models=models)
        advice_summary = write_advice(
            advice_dir,
            advice,
            shadow_inputs=resolve_shadow_training_source([shadow_dir]),
        )
        manifest["shadow_advice"] = {"path": str(advice_dir)} | advice_summary

    _write_json(manifest_path, manifest)

    diagnoses: list[dict[str, Any]] = []
    diagnosis: dict[str, Any] | None = None
    if write_diagnosis:
        diagnoses = diagnose_manifest_runs(manifest_path, advice_dir=advice_dir if write_shadow_advice else None)
        diagnosis = diagnoses[0] if diagnoses else None
        _write_json(diagnosis_path, {"version": 1, "diagnoses": diagnoses})

    gate_report: dict[str, Any] | None = None
    if write_gate:
        gate_report = build_report(
            [manifest_path],
            character=character,
            ascension=ascension,
            min_reached=min_reached,
            min_cleared=min_cleared,
            min_pristine_cleared=min_pristine_cleared,
        )
        _write_json(gate_path, gate_report)

    artifact_paths = _compact_artifact_paths(
        {
            "manifest_path": str(manifest_path),
            "shadow_dir": str(shadow_dir),
            "advice_dir": str(advice_dir) if write_shadow_advice else None,
            "diagnosis_path": str(diagnosis_path) if write_diagnosis else None,
            "gate_path": str(gate_path) if write_gate else None,
            "static_knowledge_gap_report_path": str(static_knowledge_gap_report_path)
            if static_knowledge_gap_report is not None
            else None,
            "combat_label_audit_path": str(combat_label_audit_path),
            "combat_label_replay_audit_path": str(combat_label_replay_audit_path),
            "summary_path": str(summary_path),
            "artifact_manifest_path": str(artifact_manifest_path),
            "handoff_dir": str(handoff_dir),
            "handoff_index_path": str(handoff_index_path),
            "next_handoff_prompt_path": str(next_handoff_prompt_path),
        }
    )
    batch_triage = build_batch_triage(
        manifest,
        diagnosis=diagnosis,
        diagnoses=diagnoses,
        gate_report=gate_report,
        artifact_paths=artifact_paths,
        offline_refresh_command=offline_refresh_command,
        static_knowledge_gap_report=static_knowledge_gap_report,
        combat_label_audit_report=combat_label_audit_report,
        combat_label_replay_audit_report=combat_label_replay_audit_report,
    )
    agent_handoff_paths, agent_prompt_paths = _write_agent_handoffs(
        handoff_dir,
        batch_name=artifact_name,
        batch_triage=batch_triage,
    )
    batch_triage["agent_handoff_paths"] = agent_handoff_paths
    batch_triage["agent_prompt_paths"] = agent_prompt_paths
    batch_triage["handoff_index_path"] = str(handoff_index_path)
    batch_triage["next_handoff_prompt_path"] = str(next_handoff_prompt_path)
    handoff_index = _write_handoff_index(
        handoff_index_path,
        next_handoff_prompt_path=next_handoff_prompt_path,
        batch_name=artifact_name,
        batch_triage=batch_triage,
    )
    summary = {
        "version": 1,
        "name": artifact_name,
        "inputs": [str(path) for path in logs],
        "resolved_logs": resolved_logs,
        "artifact_paths": artifact_paths,
        "offline_refresh_command": offline_refresh_command,
        "handoff_dir": str(handoff_dir),
        "handoff_index_path": str(handoff_index_path),
        "next_handoff_prompt_path": str(next_handoff_prompt_path),
        "artifact_manifest_path": str(artifact_manifest_path),
        "artifact_manifest": {
            "path": str(artifact_manifest_path),
            "health_source": "artifact_manifest_path",
            "warnings_source": "artifact_manifest_path",
            "self_checksum_excluded": True,
        },
        "agent_handoff_paths": agent_handoff_paths,
        "agent_prompt_paths": agent_prompt_paths,
        "handoff_index": handoff_index,
        "manifest_path": str(manifest_path),
        "shadow_dir": str(shadow_dir),
        "advice_dir": str(advice_dir) if write_shadow_advice else None,
        "diagnosis_path": str(diagnosis_path) if write_diagnosis else None,
        "gate_path": str(gate_path) if write_gate else None,
        "static_knowledge_gap_report_path": str(static_knowledge_gap_report_path)
        if static_knowledge_gap_report is not None
        else None,
        "combat_label_audit_path": str(combat_label_audit_path),
        "combat_label_replay_audit_path": str(combat_label_replay_audit_path),
        "summary_path": str(summary_path),
        "manifest_summary": manifest.get("summary") or {},
        "shadow_advice": advice_summary or {},
        "batch_triage": batch_triage,
        "static_knowledge_gaps": batch_triage.get("static_knowledge_gaps") or {},
        "combat_label_audit": batch_triage.get("combat_label_audit") or {},
        "combat_label_replay_audit": batch_triage.get("combat_label_replay_audit") or {},
        "diagnosis_count": len(diagnoses),
        "diagnosis_status_lines": [diagnosis["status_line"] for diagnosis in diagnoses],
        "diagnosis_status_line": diagnosis.get("status_line") if diagnosis else None,
        "gate_passed": (gate_report or {}).get("gate", {}).get("passed") if gate_report else None,
        "gate_status_line": gate_status_line(gate_report) if gate_report else None,
        "gate_next_action": batch_triage.get("gate_next_action"),
        "gate_progress": batch_triage.get("gate_progress") or {},
        "gate_remaining_progress": batch_triage.get("gate_remaining_progress") or [],
        "gate_primary_remaining_progress": batch_triage.get("gate_primary_remaining_progress"),
        "gate_data_quality_replay_resolved_issues": batch_triage.get("gate_data_quality_replay_resolved_issues") or {},
        "next_probe_goal": batch_triage.get("next_probe_goal"),
        "latest_run_acceptance": batch_triage.get("latest_run_acceptance"),
        "recommended_next_action": batch_triage.get("recommended_next_action"),
    }
    summary["offline_batch_next_line"] = _offline_batch_next_line(summary)
    _write_json(summary_path, summary)
    _write_artifact_manifest(
        artifact_manifest_path,
        batch_name=artifact_name,
        inputs=[str(path) for path in logs],
        resolved_logs=resolved_logs,
        artifact_paths=artifact_paths,
        agent_handoff_paths=agent_handoff_paths,
        agent_prompt_paths=agent_prompt_paths,
    )
    return summary


def build_batch_triage(
    manifest: dict[str, Any],
    *,
    diagnosis: dict[str, Any] | None = None,
    diagnoses: list[dict[str, Any]] | None = None,
    gate_report: dict[str, Any] | None = None,
    artifact_paths: dict[str, Any] | None = None,
    offline_refresh_command: dict[str, Any] | None = None,
    static_knowledge_gap_report: dict[str, Any] | None = None,
    combat_label_audit_report: dict[str, Any] | None = None,
    combat_label_replay_audit_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    categories = _category_counts(manifest, manifest_summary)
    classification_reasons = _classification_reason_counts(manifest, manifest_summary)
    artifact_paths = _compact_artifact_paths(artifact_paths)
    diagnoses_by_path = _diagnoses_by_path(diagnoses or ([diagnosis] if diagnosis else []))
    runs = _run_triage_rows(manifest, diagnoses_by_path=diagnoses_by_path)
    failure_attributions = _sorted_counts(manifest_summary.get("failure_attributions"))
    failure_evidence = _compact_failure_evidence_summary(manifest_summary.get("failure_evidence"))
    gate = gate_report.get("gate") if isinstance((gate_report or {}).get("gate"), dict) else {}
    gate_next_action = gate.get("next_action")
    gate_progress = gate.get("progress") if isinstance(gate.get("progress"), dict) else {}
    gate_remaining_progress = gate.get("remaining_progress") if isinstance(gate.get("remaining_progress"), list) else []
    gate_primary_remaining_progress = (
        gate.get("primary_remaining_progress") if isinstance(gate.get("primary_remaining_progress"), dict) else None
    )
    next_probe_goal = gate.get("next_probe_goal") if isinstance(gate.get("next_probe_goal"), dict) else None
    latest_run_acceptance = (
        gate.get("latest_run_acceptance") if isinstance(gate.get("latest_run_acceptance"), dict) else None
    )
    gate_execution_recovery = _compact_gate_execution_recovery(
        (gate_report or {}).get("execution_recovery") if isinstance(gate_report, dict) else None
    )
    gate_data_quality = _compact_gate_data_quality(
        (gate_report or {}).get("data_quality") if isinstance(gate_report, dict) else None
    )
    static_knowledge_gaps = _compact_static_knowledge_gaps(static_knowledge_gap_report)
    combat_label_audit = compact_combat_label_audit(combat_label_audit_report)
    combat_label_replay_audit = compact_combat_label_replay_audit(combat_label_replay_audit_report)
    gate_data_quality_replay_resolved_issues = _gate_data_quality_replay_resolved_issues(
        gate_data_quality,
        combat_label_replay_audit,
    )
    gate_failure_evidence = _compact_failure_evidence_summary(
        (gate_report or {}).get("failure_evidence") if isinstance(gate_report, dict) else None
    )
    gate_status_text = gate_status_line(gate_report) if gate_report else None
    diagnosis_next_action = _batch_diagnosis_next_action(runs, diagnosis)
    if (
        diagnosis_next_action in COMBAT_LABEL_REPAIR_ACTIONS
        and _combat_label_replay_current_policy_resolved(combat_label_replay_audit)
    ):
        diagnosis_next_action = "verify_current_policy_replay_for_combat_labels"
    recommended_next_action = _recommended_next_action(categories, gate, diagnosis_next_action)
    label_quality = _compact_label_quality(manifest_summary.get("shadow_label_quality"))
    shadow_advice_provenance = _compact_shadow_advice_provenance(
        diagnoses or ([diagnosis] if diagnosis else [])
    )
    run_action_counts = _run_action_counts(runs)
    action_recovery = _compact_action_recovery(manifest_summary.get("action_recovery"))
    if not action_recovery:
        action_recovery = _action_recovery_summary(runs)
    terminal_recovery = _compact_terminal_recovery(manifest_summary.get("terminal_recovery"))
    if not terminal_recovery:
        terminal_recovery = _terminal_recovery_summary(runs)
    agent_queues = _agent_queues(
        categories,
        failure_attributions,
        runs=runs,
        recommended_next_action=recommended_next_action,
        gate_next_action=gate_next_action,
        next_probe_goal=next_probe_goal,
        latest_run_acceptance=latest_run_acceptance,
        gate_execution_recovery=gate_execution_recovery,
        gate_data_quality=gate_data_quality,
        static_knowledge_gaps=static_knowledge_gaps,
        combat_label_audit=combat_label_audit,
        combat_label_replay_audit=combat_label_replay_audit,
        gate_failure_evidence=gate_failure_evidence,
        diagnosis_next_action=diagnosis_next_action,
        label_quality=label_quality,
        artifact_paths=artifact_paths,
    )
    agent_queue_counts = _agent_queue_counts(agent_queues)
    agent_queue_actions = _agent_queue_actions(agent_queues)
    next_handoff = _next_handoff(agent_queues)
    return {
        "run_count": sum(categories.values()),
        "artifact_paths": artifact_paths,
        "offline_refresh_command": offline_refresh_command or {},
        "classification_counts": categories,
        "classification_reasons": classification_reasons,
        "runs": runs,
        "validation": manifest_summary.get("validation") or {},
        "shadow_examples": manifest_summary.get("shadow_examples") or {},
        "shadow_label_quality": label_quality,
        "shadow_advice_provenance": shadow_advice_provenance,
        "failure_attributions": failure_attributions,
        "failure_evidence": failure_evidence,
        "run_action_counts": run_action_counts,
        "action_recovery": action_recovery,
        "terminal_recovery": terminal_recovery,
        "gate_execution_recovery": gate_execution_recovery,
        "gate_data_quality": gate_data_quality,
        "gate_data_quality_replay_resolved_issues": gate_data_quality_replay_resolved_issues,
        "static_knowledge_gaps": static_knowledge_gaps,
        "combat_label_audit": combat_label_audit,
        "combat_label_replay_audit": combat_label_replay_audit,
        "gate_failure_evidence": gate_failure_evidence,
        "gate_passed": gate.get("passed") if gate else None,
        "gate_status_line": gate_status_text,
        "gate_next_action": gate_next_action,
        "gate_progress": gate_progress,
        "gate_remaining_progress": gate_remaining_progress,
        "gate_primary_remaining_progress": gate_primary_remaining_progress,
        "next_probe_goal": next_probe_goal,
        "latest_run_acceptance": latest_run_acceptance,
        "diagnosis_next_action": diagnosis_next_action,
        "recommended_next_action": recommended_next_action,
        "agent_queue_counts": agent_queue_counts,
        "agent_queue_actions": agent_queue_actions,
        "next_handoff": next_handoff,
        "agent_queues": agent_queues,
    }


def _offline_refresh_command(
    logs: list[Path],
    *,
    resolved_logs: list[Path],
    output_dir: Path,
    artifact_name: str,
    knowledge_dir: Path | None,
    write_shadow_advice: bool,
    write_diagnosis: bool,
    write_gate: bool,
    route_model_path: Path,
    potion_model_path: Path,
    deck_model_path: Path,
    combat_model_path: Path,
    character: str,
    ascension: int,
    min_reached: int,
    min_cleared: int,
    min_pristine_cleared: int,
) -> dict[str, Any]:
    argv = _offline_batch_argv(
        logs,
        output_dir=output_dir,
        artifact_name=artifact_name,
        knowledge_dir=knowledge_dir,
        write_shadow_advice=write_shadow_advice,
        write_diagnosis=write_diagnosis,
        write_gate=write_gate,
        route_model_path=route_model_path,
        potion_model_path=potion_model_path,
        deck_model_path=deck_model_path,
        combat_model_path=combat_model_path,
        character=character,
        ascension=ascension,
        min_reached=min_reached,
        min_cleared=min_cleared,
        min_pristine_cleared=min_pristine_cleared,
    )
    resolved_argv = (
        _offline_batch_argv(
            resolved_logs,
            output_dir=output_dir,
            artifact_name=artifact_name,
            knowledge_dir=knowledge_dir,
            write_shadow_advice=write_shadow_advice,
            write_diagnosis=write_diagnosis,
            write_gate=write_gate,
            route_model_path=route_model_path,
            potion_model_path=potion_model_path,
            deck_model_path=deck_model_path,
            combat_model_path=combat_model_path,
            character=character,
            ascension=ascension,
            min_reached=min_reached,
            min_cleared=min_cleared,
            min_pristine_cleared=min_pristine_cleared,
        )
        if resolved_logs
        else []
    )
    return {
        "kind": "offline_batch_replay",
        "argv": argv,
        "resolved_argv": resolved_argv,
        "resolved_argv_available": bool(resolved_logs),
        "resolved_log_count": len(resolved_logs),
        "touches_live_mcp": False,
        "trains_models": False,
        "writes_models": False,
    }


def _offline_batch_argv(
    logs: list[Path],
    *,
    output_dir: Path,
    artifact_name: str,
    knowledge_dir: Path | None,
    write_shadow_advice: bool,
    write_diagnosis: bool,
    write_gate: bool,
    route_model_path: Path,
    potion_model_path: Path,
    deck_model_path: Path,
    combat_model_path: Path,
    character: str,
    ascension: int,
    min_reached: int,
    min_cleared: int,
    min_pristine_cleared: int,
) -> list[str]:
    argv = ["python", "-m", "slay_ai.offline_batch", *[str(path) for path in logs]]
    argv.extend(["--output-dir", str(output_dir), "--name", artifact_name])
    if knowledge_dir:
        argv.extend(["--knowledge-dir", str(knowledge_dir)])
    else:
        argv.append("--no-knowledge")
    if not write_shadow_advice:
        argv.append("--skip-advice")
    else:
        argv.extend(
            [
                "--route-model-path",
                str(route_model_path),
                "--potion-model-path",
                str(potion_model_path),
                "--deck-model-path",
                str(deck_model_path),
                "--combat-model-path",
                str(combat_model_path),
            ]
        )
    if not write_diagnosis:
        argv.append("--skip-diagnosis")
    if not write_gate:
        argv.append("--skip-gate")
    argv.extend(
        [
            "--character",
            character,
            "--ascension",
            str(ascension),
            "--min-reached",
            str(min_reached),
            "--min-cleared",
            str(min_cleared),
            "--min-pristine-cleared",
            str(min_pristine_cleared),
        ]
    )
    return argv


def _diagnoses_by_path(diagnoses: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for diagnosis in diagnoses:
        path = diagnosis.get("path") if isinstance(diagnosis, dict) else None
        if path:
            result[str(path)] = diagnosis
    return result


def _batch_diagnosis_next_action(
    runs: list[dict[str, Any]],
    fallback_diagnosis: dict[str, Any] | None,
) -> str | None:
    ignored_actions = {"", "keep_for_training", "preserve_boss_validation_evidence"}
    owner_rank = {
        "engineering_agent": 0,
        "ai_agent": 1,
        "runner_agent": 2,
        "main_agent": 3,
    }
    candidates: list[tuple[int, int, str]] = []
    for run in runs:
        action = str(run.get("diagnosis_next_action") or "")
        if action in ignored_actions:
            continue
        owner = str(run.get("owner") or "main_agent")
        candidates.append(
            (
                _priority_for_run_action(action, owner),
                owner_rank.get(owner, 9),
                action,
            )
        )
    if candidates:
        return min(candidates)[2]
    fallback_action = str(fallback_diagnosis.get("next_action") or "") if fallback_diagnosis else ""
    return fallback_action or None


def _run_triage_rows(
    manifest: dict[str, Any],
    *,
    diagnoses_by_path: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}
    rows: list[dict[str, Any]] = []
    for category in ("infra_blocked", "diagnostic_excluded", "clean_trainable"):
        items = categories.get(category)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                diagnosis = (diagnoses_by_path or {}).get(str(item.get("path") or ""))
                rows.append(_run_triage_row(category, item, diagnosis=diagnosis))
    return sorted(rows, key=lambda row: str(row.get("path") or ""))


def _run_triage_row(
    category: str,
    item: dict[str, Any],
    *,
    diagnosis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attribution = str(item.get("failure_attribution") or "none")
    action = _run_next_action(category, attribution, item)
    diagnosis_action = str(diagnosis.get("next_action")) if diagnosis and diagnosis.get("next_action") else None
    if diagnosis_action:
        action = diagnosis_action
    owner = _owner_for_run_action(action, attribution)
    return {
        "path": item.get("path"),
        "category": category,
        "reason": item.get("reason"),
        "owner": owner,
        "action": action,
        "diagnosis_next_action": diagnosis_action,
        "failure_attribution": attribution,
        "failure_tags": item.get("failure_tags") or [],
        "failure_evidence": _compact_failure_evidence(item.get("failure_evidence")),
        "floor": item.get("floor"),
        "victory": item.get("victory"),
        "validation_grade": item.get("validation_grade"),
        "recovered_actions": int(item.get("recovered_actions") or 0),
        "failed_actions": int(item.get("failed_actions") or 0),
        "action_recovery_summary": _compact_action_recovery(item.get("action_recovery_summary")),
        "act1_boss": _compact_act1_boss(item),
        "shadow_advice": _compact_run_shadow_advice(diagnosis),
    }


def _run_next_action(category: str, attribution: str, item: dict[str, Any]) -> str:
    if category == "infra_blocked":
        return "fix_execution_layer"
    boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    if category == "diagnostic_excluded":
        if boss.get("cleared"):
            if boss.get("prefix_pristine_clear") is True:
                return "preserve_boss_validation_evidence"
            return "exclude_and_collect_pristine_boss_clear"
        if boss.get("reached"):
            return "exclude_and_collect_terminal_boss_evidence"
        return "exclude_and_collect_terminal_evidence"
    if category == "clean_trainable" and item.get("victory") is False and attribution != "none":
        return f"inspect_{attribution}"
    if category == "clean_trainable":
        return "keep_for_training"
    return "review_run"


def _compact_act1_boss(item: dict[str, Any]) -> dict[str, Any]:
    boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    if not isinstance(boss, dict) or not boss:
        return {}
    result = {
        "reached": bool(boss.get("reached")),
        "cleared": bool(boss.get("cleared")),
        "enemy_ids": list(boss.get("enemy_ids") or []),
        "last_hp": boss.get("last_hp"),
        "clear_step": boss.get("clear_step"),
        "potion_use_count": len(boss.get("potion_use_steps") or []),
    }
    if "prefix_pristine_clear" in boss:
        result["prefix_pristine_clear"] = bool(boss.get("prefix_pristine_clear"))
    prefix_blockers = boss.get("prefix_blockers")
    if isinstance(prefix_blockers, list) and prefix_blockers:
        result["prefix_blockers"] = list(prefix_blockers)
    return result


def _compact_failure_evidence(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    compact: dict[str, Any] = {"reason": raw.get("reason")}
    stall = raw.get("screen_stall") if isinstance(raw.get("screen_stall"), dict) else {}
    if stall:
        compact["reason"] = raw.get("reason") or stall.get("reason")
        compact["screen_stall"] = _compact_screen_stall(stall)
    mcp_read = raw.get("mcp_read") if isinstance(raw.get("mcp_read"), dict) else {}
    if mcp_read:
        compact["mcp_read"] = _compact_mcp_read(mcp_read)
    action_error = raw.get("action_error") if isinstance(raw.get("action_error"), dict) else {}
    if action_error:
        compact["action_error"] = _compact_action_error(action_error)
    synthetic = raw.get("synthetic_terminal") if isinstance(raw.get("synthetic_terminal"), dict) else {}
    if synthetic:
        compact["synthetic_terminal"] = _compact_synthetic_terminal(synthetic)
    if raw.get("terminal_outcome_source"):
        compact["terminal_outcome_source"] = raw.get("terminal_outcome_source")
    return {key: value for key, value in compact.items() if value not in (None, "", {}, [])}


def _compact_screen_stall(stall: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "screen_type",
        "floor",
        "repeat_count",
        "first_step",
        "last_step",
        "room_phase",
        "last_reward_count",
        "last_card_count",
        "last_relic_count",
        "chest_open",
    )
    compact = {field: stall[field] for field in fields if stall.get(field) not in (None, "", [], {})}
    actions = stall.get("last_actions") if isinstance(stall.get("last_actions"), list) else []
    if actions:
        compact["last_actions"] = actions[:2]
    return compact


def _compact_mcp_read(read: dict[str, Any]) -> dict[str, Any]:
    fields = ("step", "event", "diagnostics_status", "error")
    compact = {field: read[field] for field in fields if read.get(field) not in (None, "", [], {})}
    state = read.get("last_state") if isinstance(read.get("last_state"), dict) else {}
    if state:
        compact["last_state"] = {
            field: state[field]
            for field in ("step", "screen_type", "room_phase", "floor", "act", "current_hp", "max_hp")
            if state.get(field) not in (None, "", [], {})
        }
    return compact


def _compact_action_error(action: dict[str, Any]) -> dict[str, Any]:
    fields = ("step", "action_status", "recovered", "kind", "last_error", "rewrite_reason")
    return {field: action[field] for field in fields if action.get(field) not in (None, "", [], {})}


def _compact_synthetic_terminal(synthetic: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "step",
        "source",
        "last_error",
        "previous_error",
        "terminal_recovery_attempted",
        "terminal_recovery_succeeded",
        "post_recovery_status",
    )
    return {field: synthetic[field] for field in fields if synthetic.get(field) not in (None, "", [], {})}


def _compact_failure_evidence_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    runs_with_evidence = int(raw.get("runs_with_evidence") or 0)
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
    return result


def _run_action_counts(runs: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for run in runs:
        action = str(run.get("action") or "")
        if not action:
            continue
        counts[action] = counts.get(action, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _combat_label_replay_current_policy_resolved(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    excluded = int(raw.get("excluded_rows") or 0)
    if excluded <= 0:
        return False
    return (
        int(raw.get("replayed_rows") or 0) >= excluded
        and int(raw.get("current_policy_matches_label") or 0) >= excluded
        and int(raw.get("current_policy_other") or 0) == 0
        and int(raw.get("current_policy_matches_actual") or 0) == 0
        and int(raw.get("missing_source_log") or 0) == 0
        and int(raw.get("missing_source_step") or 0) == 0
        and int(raw.get("policy_errors") or 0) == 0
    )


def _combat_label_replay_resolution(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        return {}
    return {
        "current_policy_resolved": _combat_label_replay_current_policy_resolved(raw),
        "excluded_rows": int(raw.get("excluded_rows") or 0),
        "replayed_rows": int(raw.get("replayed_rows") or 0),
        "current_policy_matches_label": int(raw.get("current_policy_matches_label") or 0),
        "current_policy_other": int(raw.get("current_policy_other") or 0),
        "missing_source_log": int(raw.get("missing_source_log") or 0),
        "missing_source_step": int(raw.get("missing_source_step") or 0),
        "policy_errors": int(raw.get("policy_errors") or 0),
    }


def _action_recovery_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    total = 0
    recovered = 0
    unrecovered = 0
    runs_with_action_recovery = 0
    runs_with_recovered_action = 0
    runs_with_unrecovered_action = 0
    for run in runs:
        summary = run.get("action_recovery_summary") if isinstance(run.get("action_recovery_summary"), dict) else {}
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
        _merge_counts(by_status, summary.get("by_status"))
        _merge_counts(by_kind, summary.get("by_kind"))
    if total <= 0:
        return {}
    return {
        "total": total,
        "recovered": recovered,
        "unrecovered": unrecovered,
        "runs_with_action_recovery": runs_with_action_recovery,
        "runs_with_recovered_action": runs_with_recovered_action,
        "runs_with_unrecovered_action": runs_with_unrecovered_action,
        "by_status": _sorted_counts(by_status),
        "by_kind": _sorted_counts(by_kind),
    }


def _compact_action_recovery(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    total = int(raw.get("total") or 0)
    if total <= 0:
        return {}
    result = {
        "total": total,
        "recovered": int(raw.get("recovered") or 0),
        "unrecovered": int(raw.get("unrecovered") or 0),
        "runs_with_action_recovery": int(raw.get("runs_with_action_recovery") or 0),
        "runs_with_recovered_action": int(raw.get("runs_with_recovered_action") or 0),
        "runs_with_unrecovered_action": int(raw.get("runs_with_unrecovered_action") or 0),
        "by_status": _sorted_counts(raw.get("by_status")),
        "by_kind": _sorted_counts(raw.get("by_kind")),
    }
    examples = _compact_action_recovery_examples(raw.get("examples"))
    if examples:
        result["examples"] = examples
    return result


def _compact_action_recovery_examples(raw: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    allowed = {
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
    for example in raw:
        if not isinstance(example, dict):
            continue
        compact = {
            key: value
            for key, value in example.items()
            if key in allowed and value not in (None, "", [], {})
        }
        if compact:
            result.append(compact)
        if len(result) >= limit:
            break
    return result


def _merge_counts(dest: dict[str, int], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        count = int(value or 0)
        if count > 0:
            key_text = str(key)
            dest[key_text] = dest.get(key_text, 0) + count


def _terminal_recovery_summary(runs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "synthetic_terminals": 0,
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "not_attempted": 0,
        "unhealthy": 0,
    }
    for run in runs:
        evidence = run.get("failure_evidence") if isinstance(run.get("failure_evidence"), dict) else {}
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
    return {key: value for key, value in counts.items() if value > 0}


def _compact_terminal_recovery(raw: Any) -> dict[str, int]:
    return _sorted_counts(raw)


def _compact_gate_execution_recovery(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    action = _compact_action_recovery(raw.get("action_recovery"))
    terminal = _compact_terminal_recovery(raw.get("terminal_recovery"))
    result: dict[str, Any] = {}
    if action:
        result["action_recovery"] = action
    if terminal:
        result["terminal_recovery"] = terminal
    return result


def _compact_gate_data_quality(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    int_fields = (
        "total_manifests",
        "coverage_manifests",
        "missing_coverage_manifests",
        "shadow_feature_rows",
        "feature_gap_manifests",
        "feature_zero_manifests",
        "unknown_static_manifests",
        "unknown_static_total",
        "shadow_label_rows",
        "shadow_label_trainable",
        "shadow_label_excluded",
        "label_exclusion_manifests",
        "direct_kill_available_labels",
        "missed_single_card_search_labels",
        "schema_complete_manifests",
        "schema_missing_manifests",
        "schema_missing_key_total",
    )
    count_fields = (
        "feature_gaps",
        "feature_zero",
        "feature_issue_categories",
        "unknown_static_features",
        "label_exclusion_reasons",
        "schema_missing_summary_keys",
    )
    result: dict[str, Any] = {}
    for field in int_fields:
        count = int(raw.get(field) or 0)
        if count > 0:
            result[field] = count
    for field in count_fields:
        counts = _sorted_counts(raw.get(field))
        if counts:
            result[field] = counts
    stale_targets, remaining_targets = _compact_stale_manifest_targets(raw.get("manifests"))
    if stale_targets:
        result["stale_manifest_targets"] = stale_targets
    if remaining_targets > 0:
        result["stale_manifest_targets_truncated"] = True
        result["stale_manifest_targets_remaining"] = remaining_targets
    return result


def _compact_stale_manifest_targets(raw: Any, *, limit: int = 8) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw, list):
        return [], 0
    targets: list[dict[str, Any]] = []
    total = 0
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("schema_complete") is not False:
            continue
        total += 1
        if len(targets) >= limit:
            continue
        missing = [str(key) for key in entry.get("missing_summary_keys") or [] if str(key)]
        target = {
            "manifest_path": entry.get("manifest_path"),
            "missing_key_count": len(missing),
        }
        if missing:
            target["missing_summary_keys"] = missing
        if entry.get("coverage_source"):
            target["coverage_source"] = entry.get("coverage_source")
        if target.get("manifest_path"):
            targets.append(target)
    return targets, max(0, total - len(targets))


def _compact_static_knowledge_gaps(raw: Any, *, per_kind_limit: int = 5) -> dict[str, Any]:
    return compact_gap_report(raw, per_kind_limit=per_kind_limit)


def _owner_for_run_action(action: str, attribution: str) -> str:
    if action == "preserve_boss_validation_evidence":
        return "main_agent"
    if action == "keep_for_training":
        return "ai_agent"
    if action.startswith("exclude_and_collect"):
        return "runner_agent"
    if action == "fix_execution_layer":
        return "engineering_agent"
    return _owner_for_attribution(attribution)


def _category_counts(manifest: dict[str, Any], manifest_summary: dict[str, Any]) -> dict[str, int]:
    categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}
    counts: dict[str, int] = {}
    for category in ("clean_trainable", "diagnostic_excluded", "infra_blocked"):
        rows = categories.get(category)
        if isinstance(rows, list):
            counts[category] = len(rows)
        else:
            counts[category] = int(manifest_summary.get(category) or 0)
    return counts


def _classification_reason_counts(manifest: dict[str, Any], manifest_summary: dict[str, Any]) -> dict[str, dict[str, int]]:
    summary_reasons = (
        manifest_summary.get("classification_reasons")
        if isinstance(manifest_summary.get("classification_reasons"), dict)
        else {}
    )
    categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}
    result: dict[str, dict[str, int]] = {}
    for category in ("clean_trainable", "diagnostic_excluded", "infra_blocked"):
        summary_counts = summary_reasons.get(category) if isinstance(summary_reasons.get(category), dict) else {}
        if summary_counts:
            result[category] = _sorted_counts(summary_counts)
            continue
        rows = categories.get(category)
        counts: dict[str, int] = {}
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                reason = str(row.get("reason") or "unknown")
                counts[reason] = counts.get(reason, 0) + 1
        result[category] = _sorted_counts(counts)
    return result


def _sorted_counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    pairs = [(str(key), int(value or 0)) for key, value in raw.items() if int(value or 0) > 0]
    return dict(sorted(pairs, key=lambda item: (-item[1], item[0])))


def _compact_label_quality(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    combat = raw.get("combat_search_labels") if isinstance(raw.get("combat_search_labels"), dict) else {}
    if not combat:
        return raw
    compact = dict(raw)
    compact["combat_search_labels"] = {
        "total": int(combat.get("total") or 0),
        "trainable": int(combat.get("trainable") or 0),
        "excluded_from_training": int(combat.get("excluded_from_training") or 0),
        "exclusion_reasons": _sorted_counts(combat.get("exclusion_reasons")),
    }
    return compact


def _compact_shadow_advice_provenance(diagnoses: list[dict[str, Any]]) -> dict[str, Any]:
    for diagnosis in diagnoses:
        if not isinstance(diagnosis, dict):
            continue
        advice = diagnosis.get("shadow_advice")
        if not isinstance(advice, dict) or not advice.get("available"):
            continue
        shadow_inputs = advice.get("shadow_inputs") if isinstance(advice.get("shadow_inputs"), dict) else {}
        return {
            "available": True,
            "advice_dir": advice.get("path"),
            "summary_path": advice.get("summary_path"),
            "mode": shadow_inputs.get("mode"),
            "resolved_file_count": int(shadow_inputs.get("resolved_file_count") or 0),
            "warnings": list(shadow_inputs.get("warnings") or []),
            "categories": _compact_shadow_advice_categories(advice),
        }
    return {"available": False}


def _compact_run_shadow_advice(diagnosis: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(diagnosis, dict):
        return {}
    advice = diagnosis.get("shadow_advice")
    if not isinstance(advice, dict):
        return {}
    result = {
        "available": bool(advice.get("available")),
        "path": advice.get("path"),
        "source_log": advice.get("source_log"),
        "categories": _compact_shadow_advice_categories(advice),
    }
    shadow_inputs = advice.get("shadow_inputs") if isinstance(advice.get("shadow_inputs"), dict) else {}
    if shadow_inputs:
        result["shadow_inputs"] = {
            "mode": shadow_inputs.get("mode"),
            "resolved_file_count": int(shadow_inputs.get("resolved_file_count") or 0),
            "warnings": list(shadow_inputs.get("warnings") or []),
        }
    return result


def _compact_shadow_advice_categories(advice: dict[str, Any]) -> dict[str, Any]:
    categories = advice.get("categories") if isinstance(advice.get("categories"), dict) else {}
    result: dict[str, Any] = {}
    for category in ("route_risk", "potion_tempo", "pre_boss_deck_quality", "combat_search"):
        summary = categories.get(category) if isinstance(categories.get(category), dict) else {}
        if not summary:
            continue
        shadow_input = summary.get("shadow_input") if isinstance(summary.get("shadow_input"), dict) else {}
        model_source = (
            summary.get("model_training_source")
            if isinstance(summary.get("model_training_source"), dict)
            else {}
        )
        compact = {
            "rows": int(summary.get("rows") or 0),
            "high_risk_count": int(summary.get("high_risk_count") or 0),
            "watch_count": int(summary.get("watch_count") or 0),
            "model_available": bool(summary.get("model_available")),
            "shadow_source_file_count": len(summary.get("shadow_source_files") or []),
            "shadow_input_file_count": int(shadow_input.get("resolved_file_count") or 0),
            "model_training_source_quality": summary.get("model_training_source_quality"),
            "model_training_source_file_count": int(model_source.get("resolved_file_count") or 0),
            "model_training_source_policy": model_source.get("source_quality_policy"),
        }
        top_signal = _compact_shadow_top_signal(category, summary.get("top_signal"))
        if top_signal:
            compact["top_signal"] = top_signal
        model_load_quality = _compact_model_load_quality(summary.get("model_load_quality"))
        if model_load_quality:
            compact["model_load_quality"] = model_load_quality
        warnings = list(shadow_input.get("warnings") or [])
        if warnings:
            compact["warnings"] = warnings
        result[category] = compact
    return result


def _compact_model_load_quality(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("files", "rows", "accepted", "skipped"):
        if key in raw:
            result[key] = int(raw.get(key) or 0)
    reasons = _sorted_counts(raw.get("skip_reasons"))
    if reasons:
        result["skip_reasons"] = reasons
    for key in ("source_quality_policy", "source_quality"):
        if raw.get(key) not in (None, "", {}, []):
            result[key] = raw.get(key)
    return result


def _compact_group_shadow_advice(runs: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, Any] = {}
    available = False
    for run in runs:
        advice = run.get("shadow_advice") if isinstance(run.get("shadow_advice"), dict) else {}
        if advice.get("available"):
            available = True
        for category, summary in (advice.get("categories") or {}).items():
            if not isinstance(summary, dict):
                continue
            bucket = categories.setdefault(
                str(category),
                {
                    "rows": 0,
                    "high_risk_count": 0,
                    "watch_count": 0,
                    "shadow_source_file_count": 0,
                    "shadow_input_file_count": 0,
                    "model_training_source_file_count": 0,
                    "model_training_source_quality": summary.get("model_training_source_quality"),
                    "model_training_source_policy": summary.get("model_training_source_policy"),
                    "model_load_quality": summary.get("model_load_quality") or {},
                },
            )
            for key in (
                "rows",
                "high_risk_count",
                "watch_count",
                "shadow_source_file_count",
                "shadow_input_file_count",
                "model_training_source_file_count",
            ):
                bucket[key] += int(summary.get(key) or 0)
            if not bucket.get("model_load_quality") and summary.get("model_load_quality"):
                bucket["model_load_quality"] = summary.get("model_load_quality") or {}
            if summary.get("top_signal"):
                signals = bucket.setdefault("top_signals", [])
                if isinstance(signals, list) and len(signals) < 3:
                    signals.append(summary["top_signal"])
    return {"available": available, "categories": categories} if available or categories else {}


def _compact_group_failure_evidence(runs: list[dict[str, Any]]) -> dict[str, Any]:
    screen_stalls: list[dict[str, Any]] = []
    mcp_reads: list[dict[str, Any]] = []
    action_errors: list[dict[str, Any]] = []
    synthetic_terminals: list[dict[str, Any]] = []
    for run in runs:
        evidence = run.get("failure_evidence") if isinstance(run.get("failure_evidence"), dict) else {}
        stall = evidence.get("screen_stall") if isinstance(evidence.get("screen_stall"), dict) else {}
        if stall and len(screen_stalls) < 3:
            item = dict(stall)
            if run.get("path"):
                item["path"] = run.get("path")
            screen_stalls.append(item)
        mcp_read = evidence.get("mcp_read") if isinstance(evidence.get("mcp_read"), dict) else {}
        if mcp_read and len(mcp_reads) < 3:
            item = dict(mcp_read)
            if run.get("path"):
                item["path"] = run.get("path")
            mcp_reads.append(item)
        action_error = evidence.get("action_error") if isinstance(evidence.get("action_error"), dict) else {}
        if action_error and len(action_errors) < 3:
            item = dict(action_error)
            if run.get("path"):
                item["path"] = run.get("path")
            action_errors.append(item)
        synthetic = evidence.get("synthetic_terminal") if isinstance(evidence.get("synthetic_terminal"), dict) else {}
        if synthetic and len(synthetic_terminals) < 3:
            item = dict(synthetic)
            if evidence.get("terminal_outcome_source"):
                item["terminal_outcome_source"] = evidence.get("terminal_outcome_source")
            if run.get("path"):
                item["path"] = run.get("path")
            synthetic_terminals.append(item)
    result = {
        "screen_stalls": screen_stalls,
        "mcp_reads": mcp_reads,
        "action_errors": action_errors,
        "synthetic_terminals": synthetic_terminals,
    }
    return {key: value for key, value in result.items() if value}


def _compact_shadow_top_signal(category: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    keep_by_category = {
        "potion_tempo": (
            "score",
            "advice",
            "floor",
            "step",
            "source_log",
            "incoming",
            "potion_ids",
            "used_potion",
            "used_potion_slot",
            "used_potion_targeted",
            "enemy_ids",
            "potion_block_value",
            "potion_damage_value",
            "potion_draw_value",
            "potion_vulnerable_value",
            "potion_weak_value",
            "enemy_total_expected_attack",
            "enemy_boss_count",
            "boss_identity_known",
            "boss_known_count",
        ),
        "combat_search": (
            "score",
            "advice",
            "floor",
            "step",
            "source_log",
            "label_first_card_key",
            "model_top_card_key",
            "loss_delta",
            "attacks_removed",
            "retaliation_damage",
            "label_missed_direct_kill",
            "label_missed_single_card_search",
        ),
    }
    fields = keep_by_category.get(category, ("score", "advice", "floor", "step", "source_log"))
    compact: dict[str, Any] = {}
    for field in fields:
        value = raw.get(field)
        if value in (None, "", [], {}):
            continue
        if field in {"potion_ids", "enemy_ids"} and isinstance(value, list):
            compact[field] = value[:3]
        else:
            compact[field] = value
    return compact


def _recommended_next_action(
    categories: dict[str, int],
    gate: dict[str, Any],
    diagnosis_next_action: str | None,
) -> str:
    if categories.get("infra_blocked", 0) > 0:
        return "fix_execution_layer"
    if gate.get("next_action"):
        return str(gate["next_action"])
    if diagnosis_next_action:
        return diagnosis_next_action
    if categories.get("diagnostic_excluded", 0) > 0:
        return "review_diagnostic_exclusions"
    if categories.get("clean_trainable", 0) > 0:
        return "review_clean_trainable_failures"
    return "collect_a0_manifest_batch"


def _agent_queues(
    categories: dict[str, int],
    failure_attributions: dict[str, int],
    *,
    runs: list[dict[str, Any]],
    recommended_next_action: str,
    gate_next_action: str | None,
    next_probe_goal: dict[str, Any] | None,
    latest_run_acceptance: dict[str, Any] | None,
    gate_execution_recovery: dict[str, Any],
    gate_data_quality: dict[str, Any],
    static_knowledge_gaps: dict[str, Any],
    combat_label_audit: dict[str, Any],
    combat_label_replay_audit: dict[str, Any],
    gate_failure_evidence: dict[str, Any],
    diagnosis_next_action: str | None,
    label_quality: dict[str, Any],
    artifact_paths: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    queues: dict[str, list[dict[str, Any]]] = {
        "runner_agent": [],
        "engineering_agent": [],
        "ai_agent": [],
        "main_agent": [],
    }
    run_groups = _group_runs_for_queue(runs)
    run_action_keys = {(owner, action) for owner, action, _ in run_groups}
    replay_resolved = _combat_label_replay_current_policy_resolved(combat_label_replay_audit)
    replay_resolution = _combat_label_replay_resolution(combat_label_replay_audit)
    runner_item = _runner_gate_queue_item(
        gate_next_action,
        next_probe_goal,
        latest_run_acceptance,
        gate_execution_recovery,
        gate_data_quality,
        gate_failure_evidence,
    )
    if runner_item:
        queues["runner_agent"].append(runner_item)
    ai_gate_item = _ai_gate_queue_item(
        gate_next_action,
        next_probe_goal,
        latest_run_acceptance,
        gate_execution_recovery,
        gate_data_quality,
        gate_failure_evidence,
    )
    if ai_gate_item:
        queues["ai_agent"].append(ai_gate_item)
    gate_execution_item = _gate_execution_recovery_queue_item(
        gate_next_action,
        gate_execution_recovery,
        gate_failure_evidence,
    )
    if gate_execution_item:
        queues["engineering_agent"].append(gate_execution_item)
    manifest_schema_item = _manifest_schema_queue_item(gate_data_quality)
    if manifest_schema_item:
        queues["engineering_agent"].append(manifest_schema_item)
    if categories.get("infra_blocked", 0) > 0 and ("engineering_agent", "fix_execution_layer") not in run_action_keys:
        queues["engineering_agent"].append(
            {
                "action": "fix_execution_layer",
                "count": categories["infra_blocked"],
                "reason": "infra_blocked_runs",
                "priority": 0,
            }
        )
    if categories.get("diagnostic_excluded", 0) > 0:
        queues["engineering_agent"].append(
            {
                "action": "review_diagnostic_exclusions",
                "count": categories["diagnostic_excluded"],
                "reason": "diagnostic_excluded_runs",
                "priority": 2,
            }
        )
    for attribution, count in failure_attributions.items():
        owner = _owner_for_attribution(attribution)
        action = f"inspect_{attribution}"
        matching_runs = [
            run for run in runs if str(run.get("failure_attribution") or "none") == attribution
        ]
        if matching_runs and not any(str(run.get("action") or "") == action for run in matching_runs):
            continue
        if (owner, action) in run_action_keys:
            continue
        queues[owner].append(
            {
                "action": action,
                "failure_attribution": attribution,
                "count": count,
                "priority": 1 if owner == "ai_agent" else 2,
            }
        )
    for owner, action, matching_runs in run_groups:
        queue_action = action
        queue_reason = "per_run_diagnosis"
        queue_priority = _priority_for_run_action(action, owner)
        original_action: str | None = None
        if action in COMBAT_LABEL_REPAIR_ACTIONS and replay_resolved:
            original_action = action
            queue_action = "verify_current_policy_replay_for_combat_labels"
            queue_reason = "per_run_diagnosis_replay_resolved"
            queue_priority = max(queue_priority, 2)
        item = {
            "action": queue_action,
            "count": len(matching_runs),
            "reason": queue_reason,
            "paths": [str(run.get("path")) for run in matching_runs[:5] if run.get("path")],
            "shadow_advice": _compact_group_shadow_advice(matching_runs),
            "priority": queue_priority,
        }
        if original_action:
            item["original_action"] = original_action
            item["combat_label_replay_resolution"] = replay_resolution
        failure_evidence = _compact_group_failure_evidence(matching_runs)
        if failure_evidence:
            item["failure_evidence"] = failure_evidence
        queues[owner].append(item)
    combat_labels = (
        label_quality.get("combat_search_labels")
        if isinstance(label_quality.get("combat_search_labels"), dict)
        else {}
    )
    excluded = int(combat_labels.get("excluded_from_training") or 0)
    if excluded > 0:
        queues["ai_agent"].append(
            {
                "action": "review_excluded_combat_search_labels",
                "count": excluded,
                "reasons": combat_labels.get("exclusion_reasons") or {},
                "combat_label_audit": combat_label_audit,
                "combat_label_replay_audit": combat_label_replay_audit,
                "reason": "current_policy_replay_resolved" if replay_resolved else "excluded_from_training",
                "combat_label_replay_resolution": replay_resolution,
                "priority": 3 if replay_resolved else 1,
            }
        )
    gate_data_quality_item = _gate_data_quality_queue_item(
        gate_data_quality,
        static_knowledge_gaps=static_knowledge_gaps,
        combat_label_replay_audit=combat_label_replay_audit,
    )
    if gate_data_quality_item:
        queues["ai_agent"].append(gate_data_quality_item)
    queues["main_agent"].append(
        _attach_gate_context(
            {
                "action": recommended_next_action,
                "gate_next_action": gate_next_action,
                "diagnosis_next_action": diagnosis_next_action,
                "priority": 0,
            },
            next_probe_goal,
            latest_run_acceptance,
            gate_execution_recovery,
            gate_data_quality,
            gate_failure_evidence,
        )
    )
    if artifact_paths:
        _attach_artifact_paths_to_queues(queues, artifact_paths)
    _attach_execution_contracts_to_queues(queues)
    return {owner: sorted(items, key=lambda item: (int(item.get("priority", 9)), str(item.get("action")))) for owner, items in queues.items()}


def _compact_artifact_paths(paths: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(paths, dict):
        return {}
    return {str(key): str(value) for key, value in paths.items() if value is not None}


def _write_agent_handoffs(
    handoff_dir: Path,
    *,
    batch_name: str,
    batch_triage: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    queues = batch_triage.get("agent_queues") if isinstance(batch_triage.get("agent_queues"), dict) else {}
    handoff_paths = {owner: str(handoff_dir / f"{owner}.json") for owner in sorted(str(key) for key in queues.keys())}
    prompt_paths = {owner: str(handoff_dir / f"{owner}.txt") for owner in handoff_paths}
    for owner, path_text in handoff_paths.items():
        queue = queues.get(owner)
        if not isinstance(queue, list):
            queue = []
        path = Path(path_text)
        prompt_path_text = prompt_paths[owner]
        payload = {
            "version": 1,
            "batch_name": batch_name,
            "owner": owner,
            "handoff_path": path_text,
            "assignment_prompt_path": prompt_path_text,
            "agent_handoff_paths": handoff_paths,
            "agent_prompt_paths": prompt_paths,
            "ownership_contract": _agent_ownership_contract(owner),
            "default_execution_contract": _execution_contract_for_item(owner, {}),
            "queue_count": len(queue),
            "first_item": queue[0] if queue else None,
            "queue": queue,
            "next_handoff": batch_triage.get("next_handoff") or {},
            "agent_queue_counts": batch_triage.get("agent_queue_counts") or {},
            "agent_queue_actions": batch_triage.get("agent_queue_actions") or {},
            "classification_reasons": batch_triage.get("classification_reasons") or {},
            "validation": batch_triage.get("validation") or {},
            "artifact_paths": batch_triage.get("artifact_paths") or {},
            "offline_refresh_command": batch_triage.get("offline_refresh_command") or {},
            "shadow_advice_provenance": batch_triage.get("shadow_advice_provenance") or {},
            "shadow_label_quality": batch_triage.get("shadow_label_quality") or {},
            "combat_label_audit": batch_triage.get("combat_label_audit") or {},
            "combat_label_replay_audit": batch_triage.get("combat_label_replay_audit") or {},
            "failure_evidence": batch_triage.get("failure_evidence") or {},
            "action_recovery": batch_triage.get("action_recovery") or {},
            "terminal_recovery": batch_triage.get("terminal_recovery") or {},
            "gate_execution_recovery": batch_triage.get("gate_execution_recovery") or {},
            "gate_data_quality": batch_triage.get("gate_data_quality") or {},
            "gate_data_quality_replay_resolved_issues": batch_triage.get("gate_data_quality_replay_resolved_issues") or {},
            "static_knowledge_gaps": batch_triage.get("static_knowledge_gaps") or {},
            "gate_failure_evidence": batch_triage.get("gate_failure_evidence") or {},
            "gate_passed": batch_triage.get("gate_passed"),
            "gate_status_line": batch_triage.get("gate_status_line"),
            "gate_next_action": batch_triage.get("gate_next_action"),
            "gate_progress": batch_triage.get("gate_progress") or {},
            "gate_remaining_progress": batch_triage.get("gate_remaining_progress") or [],
            "gate_primary_remaining_progress": batch_triage.get("gate_primary_remaining_progress"),
            "next_probe_goal": batch_triage.get("next_probe_goal"),
            "latest_run_acceptance": batch_triage.get("latest_run_acceptance"),
            "recommended_next_action": batch_triage.get("recommended_next_action"),
        }
        payload["assignment_prompt"] = _agent_assignment_prompt(payload)
        _write_json(path, payload)
        Path(prompt_path_text).write_text(payload["assignment_prompt"] + "\n", encoding="utf-8")
    return handoff_paths, prompt_paths


def _write_handoff_index(
    handoff_index_path: Path,
    *,
    next_handoff_prompt_path: Path,
    batch_name: str,
    batch_triage: dict[str, Any],
) -> dict[str, Any]:
    next_handoff = batch_triage.get("next_handoff") if isinstance(batch_triage.get("next_handoff"), dict) else {}
    agent_prompt_paths = (
        batch_triage.get("agent_prompt_paths") if isinstance(batch_triage.get("agent_prompt_paths"), dict) else {}
    )
    primary_owner = _handoff_owner(next_handoff.get("primary_worker"))
    coordinator_owner = _handoff_owner(next_handoff.get("coordinator"))
    primary_prompt_path = str(agent_prompt_paths.get(primary_owner) or "") if primary_owner else None
    coordinator_prompt_path = str(agent_prompt_paths.get(coordinator_owner) or "") if coordinator_owner else None
    payload = {
        "version": 1,
        "batch_name": batch_name,
        "handoff_index_path": str(handoff_index_path),
        "next_handoff_prompt_path": str(next_handoff_prompt_path),
        "primary_worker": next_handoff.get("primary_worker"),
        "primary_worker_prompt_path": primary_prompt_path,
        "coordinator": next_handoff.get("coordinator"),
        "coordinator_prompt_path": coordinator_prompt_path,
        "agent_handoff_paths": batch_triage.get("agent_handoff_paths") or {},
        "agent_prompt_paths": agent_prompt_paths,
        "agent_queue_counts": batch_triage.get("agent_queue_counts") or {},
        "agent_queue_actions": batch_triage.get("agent_queue_actions") or {},
        "classification_reasons": batch_triage.get("classification_reasons") or {},
        "validation": batch_triage.get("validation") or {},
        "artifact_paths": batch_triage.get("artifact_paths") or {},
        "offline_refresh_command": batch_triage.get("offline_refresh_command") or {},
        "shadow_advice_provenance": batch_triage.get("shadow_advice_provenance") or {},
        "shadow_label_quality": batch_triage.get("shadow_label_quality") or {},
        "combat_label_audit": batch_triage.get("combat_label_audit") or {},
        "combat_label_replay_audit": batch_triage.get("combat_label_replay_audit") or {},
        "failure_evidence": batch_triage.get("failure_evidence") or {},
        "action_recovery": batch_triage.get("action_recovery") or {},
        "terminal_recovery": batch_triage.get("terminal_recovery") or {},
        "gate_execution_recovery": batch_triage.get("gate_execution_recovery") or {},
        "gate_data_quality": batch_triage.get("gate_data_quality") or {},
        "gate_data_quality_replay_resolved_issues": batch_triage.get("gate_data_quality_replay_resolved_issues") or {},
        "static_knowledge_gaps": batch_triage.get("static_knowledge_gaps") or {},
        "gate_failure_evidence": batch_triage.get("gate_failure_evidence") or {},
        "gate_passed": batch_triage.get("gate_passed"),
        "gate_status_line": batch_triage.get("gate_status_line"),
        "gate_next_action": batch_triage.get("gate_next_action"),
        "gate_progress": batch_triage.get("gate_progress") or {},
        "gate_remaining_progress": batch_triage.get("gate_remaining_progress") or [],
        "gate_primary_remaining_progress": batch_triage.get("gate_primary_remaining_progress"),
        "next_probe_goal": batch_triage.get("next_probe_goal"),
        "latest_run_acceptance": batch_triage.get("latest_run_acceptance"),
        "recommended_next_action": batch_triage.get("recommended_next_action"),
    }
    _write_json(handoff_index_path, payload)
    _write_next_handoff_prompt(
        next_handoff_prompt_path,
        primary_prompt_path,
        coordinator_prompt_path,
        next_handoff=next_handoff,
        agent_queue_counts=payload["agent_queue_counts"],
        agent_queue_actions=payload["agent_queue_actions"],
        classification_reasons=payload["classification_reasons"],
        gate_status_line=payload["gate_status_line"],
        gate_remaining_progress=payload["gate_remaining_progress"],
        gate_primary_remaining_progress=payload["gate_primary_remaining_progress"],
        next_probe_goal=payload["next_probe_goal"],
        latest_run_acceptance=payload["latest_run_acceptance"],
        static_knowledge_gaps=payload["static_knowledge_gaps"],
        combat_label_audit=payload["combat_label_audit"],
        combat_label_replay_audit=payload["combat_label_replay_audit"],
        gate_data_quality_replay_resolved_issues=payload["gate_data_quality_replay_resolved_issues"],
        gate_failure_evidence=payload["gate_failure_evidence"],
    )
    return payload


def _handoff_owner(entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    owner = entry.get("owner")
    return str(owner) if owner else None


def _write_next_handoff_prompt(
    path: Path,
    primary_prompt_path: str | None,
    coordinator_prompt_path: str | None,
    *,
    next_handoff: dict[str, Any] | None = None,
    agent_queue_counts: dict[str, int] | None = None,
    agent_queue_actions: dict[str, list[dict[str, Any]]] | None = None,
    classification_reasons: dict[str, dict[str, int]] | None = None,
    gate_status_line: str | None = None,
    gate_remaining_progress: list[dict[str, Any]] | None = None,
    gate_primary_remaining_progress: dict[str, Any] | None = None,
    next_probe_goal: dict[str, Any] | None = None,
    latest_run_acceptance: dict[str, Any] | None = None,
    static_knowledge_gaps: dict[str, Any] | None = None,
    combat_label_audit: dict[str, Any] | None = None,
    combat_label_replay_audit: dict[str, Any] | None = None,
    gate_data_quality_replay_resolved_issues: dict[str, Any] | None = None,
    gate_failure_evidence: dict[str, Any] | None = None,
) -> None:
    next_handoff = next_handoff if isinstance(next_handoff, dict) else {}
    sections: list[str] = [
        "Handoff summary",
        "===============",
        f"Primary worker: {_handoff_entry_prompt_text(next_handoff.get('primary_worker'))}",
        f"Coordinator: {_handoff_entry_prompt_text(next_handoff.get('coordinator'))}",
    ]
    if gate_status_line:
        sections.append(f"Gate status: {gate_status_line}")
    if gate_primary_remaining_progress:
        sections.append(f"Gate primary remaining progress: {_prompt_json(gate_primary_remaining_progress)}")
    if gate_remaining_progress:
        sections.append(f"Gate remaining progress: {_prompt_json(gate_remaining_progress)}")
    if latest_run_acceptance:
        sections.append(f"Latest run acceptance: {_prompt_json(latest_run_acceptance)}")
    if next_probe_goal:
        sections.append(f"Next probe goal: {_prompt_json(next_probe_goal)}")
    if static_knowledge_gaps:
        sections.append(f"Static knowledge gaps: {_prompt_json(static_knowledge_gaps)}")
    if combat_label_audit:
        sections.append(f"Combat label audit: {_prompt_json(combat_label_audit)}")
    if combat_label_replay_audit:
        sections.append(f"Combat label replay audit: {_prompt_json(combat_label_replay_audit)}")
    if gate_data_quality_replay_resolved_issues:
        sections.append(
            "Gate data quality replay-resolved issues: "
            f"{_prompt_json(gate_data_quality_replay_resolved_issues)}"
        )
    if agent_queue_counts:
        sections.append(f"Agent queue counts: {_prompt_json(agent_queue_counts)}")
    if agent_queue_actions:
        sections.append(f"Agent queue actions: {_prompt_json(agent_queue_actions)}")
    reason_line = _classification_reasons_prompt_text(classification_reasons)
    if reason_line:
        sections.append(f"Classification reasons: {reason_line}")
    gate_evidence = (
        gate_failure_evidence
        if isinstance(gate_failure_evidence, dict)
        else {}
    )
    if gate_evidence:
        sections.append(f"Gate failure evidence: {_prompt_json(gate_evidence)}")
    primary_evidence = _handoff_entry_failure_evidence(next_handoff.get("primary_worker"))
    if primary_evidence:
        sections.append(f"Primary worker failure evidence: {_prompt_json(primary_evidence)}")
    sections.append("")
    if primary_prompt_path:
        sections.append("Primary worker prompt")
        sections.append("=====================")
        sections.append(_read_text_if_present(primary_prompt_path))
    else:
        sections.append("Primary worker prompt")
        sections.append("=====================")
        sections.append("No primary worker task is queued.")
    if coordinator_prompt_path:
        sections.append("")
        sections.append("Coordinator prompt")
        sections.append("==================")
        sections.append(_read_text_if_present(coordinator_prompt_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")


def _handoff_entry_prompt_text(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "none"
    owner = str(entry.get("owner") or "unknown")
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    action = str(item.get("action") or "no_action")
    reason = item.get("reason") or item.get("gate_next_action")
    if reason:
        return f"{owner}:{action} ({reason})"
    return f"{owner}:{action}"


def _offline_batch_next_line(summary: dict[str, Any]) -> str:
    triage = summary.get("batch_triage") if isinstance(summary.get("batch_triage"), dict) else {}
    next_handoff = triage.get("next_handoff") if isinstance(triage.get("next_handoff"), dict) else {}
    counts = triage.get("agent_queue_counts") if isinstance(triage.get("agent_queue_counts"), dict) else {}
    recommended = summary.get("recommended_next_action") or triage.get("recommended_next_action") or "none"
    gate_next = summary.get("gate_next_action") or triage.get("gate_next_action") or "none"
    primary = next_handoff.get("primary_worker") if isinstance(next_handoff, dict) else None
    coordinator = next_handoff.get("coordinator") if isinstance(next_handoff, dict) else None
    return " ".join(
        [
            "offline_batch_next:",
            f"recommended={_cli_token(recommended)}",
            f"gate_next={_cli_token(gate_next)}",
            f"primary={_handoff_entry_status_text(primary)}",
            f"coordinator={_handoff_entry_status_text(coordinator)}",
            f"primary_live_mcp_required={str(_handoff_entry_requires_live_mcp(primary)).lower()}",
            f"any_live_mcp_required={str(_next_handoff_requires_live_mcp(next_handoff)).lower()}",
            f"queues={_prompt_json(counts)}",
        ]
    )


def _handoff_entry_status_text(entry: Any) -> str:
    if not isinstance(entry, dict):
        return "none"
    owner = _cli_token(entry.get("owner") or "unknown")
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    action = _cli_token(item.get("action") or "no_action")
    return f"{owner}:{action}"


def _handoff_entry_requires_live_mcp(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    execution = item.get("execution_contract") if isinstance(item.get("execution_contract"), dict) else {}
    return bool(execution.get("requires_live_mcp_ownership"))


def _next_handoff_requires_live_mcp(next_handoff: Any) -> bool:
    if not isinstance(next_handoff, dict):
        return False
    for key in ("primary_worker", "coordinator"):
        if _handoff_entry_requires_live_mcp(next_handoff.get(key)):
            return True
    by_agent = next_handoff.get("by_agent") if isinstance(next_handoff.get("by_agent"), dict) else {}
    return any(_handoff_entry_requires_live_mcp(entry) for entry in by_agent.values())


def _cli_token(value: Any) -> str:
    text = str(value or "none").strip()
    if not text:
        return "none"
    return "_".join(text.split())


def _handoff_entry_failure_evidence(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    evidence = item.get("failure_evidence") if isinstance(item.get("failure_evidence"), dict) else {}
    return evidence


def _read_text_if_present(path_text: str) -> str:
    path = Path(path_text)
    if not path.exists():
        return f"Prompt file missing: {path_text}"
    return path.read_text(encoding="utf-8").strip()


def _agent_ownership_contract(owner: str) -> dict[str, Any]:
    contract = AGENT_OWNERSHIP_CONTRACTS.get(owner)
    if isinstance(contract, dict):
        return dict(contract)
    return {
        "role": owner,
        "owns": [],
        "live_mcp_policy": "follow main-agent assignment",
    }


def _agent_assignment_prompt(payload: dict[str, Any]) -> str:
    owner = str(payload.get("owner") or "agent")
    ownership = payload.get("ownership_contract") if isinstance(payload.get("ownership_contract"), dict) else {}
    offline_refresh_command = (
        payload.get("offline_refresh_command")
        if isinstance(payload.get("offline_refresh_command"), dict)
        else {}
    )
    default_contract = (
        payload.get("default_execution_contract")
        if isinstance(payload.get("default_execution_contract"), dict)
        else {}
    )
    first_item = payload.get("first_item") if isinstance(payload.get("first_item"), dict) else {}
    artifact_paths = payload.get("artifact_paths") if isinstance(payload.get("artifact_paths"), dict) else {}
    advice_provenance = (
        payload.get("shadow_advice_provenance")
        if isinstance(payload.get("shadow_advice_provenance"), dict)
        else {}
    )
    classification_reasons = payload.get("classification_reasons")
    validation = payload.get("validation")
    label_quality = (
        payload.get("shadow_label_quality")
        if isinstance(payload.get("shadow_label_quality"), dict)
        else {}
    )
    combat_label_audit = (
        payload.get("combat_label_audit")
        if isinstance(payload.get("combat_label_audit"), dict)
        else {}
    )
    combat_label_replay_audit = (
        payload.get("combat_label_replay_audit")
        if isinstance(payload.get("combat_label_replay_audit"), dict)
        else {}
    )
    gate_data_quality_replay_resolved_issues = (
        payload.get("gate_data_quality_replay_resolved_issues")
        if isinstance(payload.get("gate_data_quality_replay_resolved_issues"), dict)
        else {}
    )
    failure_evidence_summary = (
        payload.get("failure_evidence")
        if isinstance(payload.get("failure_evidence"), dict)
        else {}
    )
    action_recovery = (
        payload.get("action_recovery")
        if isinstance(payload.get("action_recovery"), dict)
        else {}
    )
    terminal_recovery = (
        payload.get("terminal_recovery")
        if isinstance(payload.get("terminal_recovery"), dict)
        else {}
    )
    gate_execution_recovery = (
        payload.get("gate_execution_recovery")
        if isinstance(payload.get("gate_execution_recovery"), dict)
        else {}
    )
    gate_data_quality = (
        payload.get("gate_data_quality")
        if isinstance(payload.get("gate_data_quality"), dict)
        else {}
    )
    static_knowledge_gaps = (
        payload.get("static_knowledge_gaps")
        if isinstance(payload.get("static_knowledge_gaps"), dict)
        else {}
    )
    gate_failure_evidence = (
        payload.get("gate_failure_evidence")
        if isinstance(payload.get("gate_failure_evidence"), dict)
        else {}
    )
    batch_next_probe_goal = (
        payload.get("next_probe_goal")
        if isinstance(payload.get("next_probe_goal"), dict)
        else {}
    )
    batch_latest_run_acceptance = (
        payload.get("latest_run_acceptance")
        if isinstance(payload.get("latest_run_acceptance"), dict)
        else {}
    )
    gate_remaining_progress = (
        payload.get("gate_remaining_progress")
        if isinstance(payload.get("gate_remaining_progress"), list)
        else []
    )
    gate_primary_remaining_progress = (
        payload.get("gate_primary_remaining_progress")
        if isinstance(payload.get("gate_primary_remaining_progress"), dict)
        else {}
    )
    gate_status_text = str(payload.get("gate_status_line") or "")
    queue = payload.get("queue") if isinstance(payload.get("queue"), list) else []
    queue_count = len(queue)
    if payload.get("queue_count") is not None:
        try:
            queue_count = int(payload.get("queue_count") or 0)
        except (TypeError, ValueError):
            queue_count = len(queue)
    action = str(first_item.get("action") or "no queued task")
    reason = str(first_item.get("reason") or first_item.get("gate_next_action") or "no reason recorded")
    execution = (
        first_item.get("execution_contract")
        if isinstance(first_item.get("execution_contract"), dict)
        else default_contract
    )
    allowed = ", ".join(str(item) for item in execution.get("allowed_operations") or [])
    forbidden = ", ".join(str(item) for item in execution.get("forbidden_operations") or [])
    lines = [
        f"Agent: {owner}",
        f"Role: {ownership.get('role', owner)}",
        f"First task: {action} ({reason})",
    ]
    if first_item.get("count") is not None:
        lines.append(f"Task count: {first_item.get('count')}")
    if queue_count > 1:
        lines.append(f"Queue length: {queue_count}")
        queue_actions = _queue_actions_prompt_text(queue)
        if queue_actions:
            lines.append(f"Queued actions: {queue_actions}")
    if first_item.get("paths"):
        lines.append(f"Paths: {', '.join(str(path) for path in first_item.get('paths') or [])}")
    issues = _sorted_counts(first_item.get("issues"))
    if issues:
        lines.append(f"Task issues: {_prompt_json(issues)}")
    schema_missing_keys = _sorted_counts(first_item.get("schema_missing_summary_keys"))
    if schema_missing_keys:
        lines.append(f"Schema missing summary keys: {_prompt_json(schema_missing_keys)}")
    if first_item.get("schema_missing_key_total") is not None:
        lines.append(f"Schema missing key total: {first_item.get('schema_missing_key_total')}")
    if first_item.get("stale_manifest_targets"):
        lines.append(f"Stale manifest targets: {_prompt_json(first_item.get('stale_manifest_targets'))}")
    if first_item.get("stale_manifest_targets_truncated"):
        lines.append(
            "Stale manifest targets truncated: "
            f"{int(first_item.get('stale_manifest_targets_remaining') or 0)} remaining"
        )
    if first_item.get("refresh_manifest_schema_commands"):
        lines.append(
            "Refresh manifest schema commands: "
            f"{_prompt_json(first_item.get('refresh_manifest_schema_commands'))}"
        )
    failure_evidence = (
        first_item.get("failure_evidence")
        if isinstance(first_item.get("failure_evidence"), dict)
        else {}
    )
    if failure_evidence:
        lines.append(f"Failure evidence: {_prompt_json(failure_evidence)}")
    reason_line = _classification_reasons_prompt_text(classification_reasons)
    if reason_line:
        lines.append(f"Classification reasons: {reason_line}")
    validation_line = _validation_prompt_text(validation)
    if validation_line:
        lines.append(f"Validation summary: {validation_line}")
    if gate_status_text:
        lines.append(f"Gate status: {gate_status_text}")
    if gate_primary_remaining_progress:
        lines.append(f"Gate primary remaining progress: {_prompt_json(gate_primary_remaining_progress)}")
    if gate_remaining_progress:
        lines.append(f"Gate remaining progress: {_prompt_json(gate_remaining_progress)}")
    if first_item.get("acceptance_criteria"):
        lines.append(f"Acceptance criteria: {_prompt_json(first_item.get('acceptance_criteria'))}")
    latest_run_acceptance = (
        first_item.get("latest_run_acceptance")
        if isinstance(first_item.get("latest_run_acceptance"), dict)
        else batch_latest_run_acceptance
    )
    next_probe_goal = (
        first_item.get("next_probe_goal")
        if isinstance(first_item.get("next_probe_goal"), dict)
        else batch_next_probe_goal
    )
    if latest_run_acceptance:
        lines.append(f"Latest run acceptance: {_prompt_json(latest_run_acceptance)}")
    if next_probe_goal:
        lines.append(f"Next probe goal: {_prompt_json(next_probe_goal)}")
    lines.extend(
        [
            f"Execution mode: {execution.get('mode', 'unknown')}",
            f"Requires live MCP ownership: {bool(execution.get('requires_live_mcp_ownership'))}",
            f"Live MCP policy: {ownership.get('live_mcp_policy', 'follow main-agent assignment')}",
            f"Allowed operations: {allowed or 'read_artifacts'}",
            f"Forbidden operations: {forbidden or 'control_live_mcp_without_explicit_ownership'}",
            "Use offline_refresh_command.resolved_argv when available to replay the exact log set without touching live MCP.",
        ]
    )
    lines.extend(_offline_refresh_prompt_lines(offline_refresh_command))
    if advice_provenance.get("available"):
        lines.append(f"Shadow advice provenance: {_prompt_json(advice_provenance)}")
    if label_quality:
        lines.append(f"Shadow label quality: {_prompt_json(label_quality)}")
    if combat_label_audit:
        lines.append(f"Combat label audit: {_prompt_json(combat_label_audit)}")
    if combat_label_replay_audit:
        lines.append(f"Combat label replay audit: {_prompt_json(combat_label_replay_audit)}")
    if gate_data_quality_replay_resolved_issues:
        lines.append(
            "Gate data quality replay-resolved issues: "
            f"{_prompt_json(gate_data_quality_replay_resolved_issues)}"
        )
    if failure_evidence_summary:
        lines.append(f"Failure evidence summary: {_prompt_json(failure_evidence_summary)}")
    if action_recovery:
        lines.append(f"Action recovery: {_prompt_json(action_recovery)}")
    if terminal_recovery:
        lines.append(f"Terminal recovery: {_prompt_json(terminal_recovery)}")
    if gate_execution_recovery:
        lines.append(f"Gate execution recovery: {_prompt_json(gate_execution_recovery)}")
    if gate_data_quality:
        lines.append(f"Gate data quality: {_prompt_json(gate_data_quality)}")
    if static_knowledge_gaps:
        lines.append(f"Static knowledge gaps: {_prompt_json(static_knowledge_gaps)}")
    if gate_failure_evidence:
        lines.append(f"Gate failure evidence: {_prompt_json(gate_failure_evidence)}")
    for key in (
        "manifest_path",
        "diagnosis_path",
        "gate_path",
        "static_knowledge_gap_report_path",
        "combat_label_audit_path",
        "combat_label_replay_audit_path",
        "summary_path",
        "artifact_manifest_path",
        "handoff_dir",
    ):
        if artifact_paths.get(key):
            lines.append(f"{key}: {artifact_paths[key]}")
    return "\n".join(lines)


def _queue_actions_prompt_text(raw: Any, *, limit: int = 5) -> str:
    if not isinstance(raw, list):
        return ""
    parts: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        if not action:
            continue
        part = action
        if item.get("count") is not None:
            part = f"{part}[count={item.get('count')}]"
        reason = item.get("reason") or item.get("gate_next_action")
        if reason:
            part = f"{part}[reason={reason}]"
        parts.append(part)
        if len(parts) >= limit:
            break
    remaining = max(0, len(raw) - limit)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return ", ".join(parts)


def _prompt_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _offline_refresh_prompt_lines(command: dict[str, Any]) -> list[str]:
    if not command:
        return []
    metadata = {
        key: command.get(key)
        for key in (
            "kind",
            "resolved_argv_available",
            "resolved_log_count",
            "touches_live_mcp",
            "trains_models",
            "writes_models",
        )
        if key in command
    }
    lines: list[str] = []
    if metadata:
        lines.append(f"Offline refresh command: {_prompt_json(metadata)}")
    resolved_argv = command.get("resolved_argv")
    if isinstance(resolved_argv, list) and resolved_argv:
        lines.append(f"offline_refresh_command.resolved_argv: {_prompt_json(resolved_argv)}")
    else:
        argv = command.get("argv")
        if isinstance(argv, list) and argv:
            lines.append(f"offline_refresh_command.argv: {_prompt_json(argv)}")
    return lines


def _classification_reasons_prompt_text(raw: Any, *, limit_per_category: int = 3) -> str:
    if not isinstance(raw, dict):
        return ""
    parts: list[str] = []
    for category in ("clean_trainable", "diagnostic_excluded", "infra_blocked"):
        counts = _sorted_counts(raw.get(category))
        if not counts:
            continue
        shown = list(counts.items())[:limit_per_category]
        reason_bits = ",".join(f"{reason}:{count}" for reason, count in shown)
        remaining = len(counts) - len(shown)
        if remaining > 0:
            reason_bits = f"{reason_bits},+{remaining} more"
        parts.append(f"{category}={reason_bits}")
    return "; ".join(parts)


def _validation_prompt_text(raw: Any, *, limit_per_bucket: int = 3) -> str:
    if not isinstance(raw, dict):
        return ""
    parts: list[str] = []
    for key, label in (
        ("by_grade", "grades"),
        ("by_flag", "flags"),
        ("act1_boss_prefix_blockers", "prefix_blockers"),
        ("act1_boss_clear_blockers", "clear_blockers"),
    ):
        counts = _sorted_counts(raw.get(key))
        if not counts:
            continue
        shown = list(counts.items())[:limit_per_bucket]
        bits = ",".join(f"{reason}:{count}" for reason, count in shown)
        remaining = len(counts) - len(shown)
        if remaining > 0:
            bits = f"{bits},+{remaining} more"
        parts.append(f"{label}={bits}")
    return "; ".join(parts)


def _attach_execution_contracts_to_queues(queues: dict[str, list[dict[str, Any]]]) -> None:
    for owner, items in queues.items():
        for item in items:
            item["execution_contract"] = _execution_contract_for_item(owner, item)


def _execution_contract_for_item(owner: str, item: dict[str, Any]) -> dict[str, Any]:
    action = str(item.get("action") or "")
    if owner == "runner_agent":
        live_gated = action.startswith("collect") or action.startswith("exclude_and_collect")
        return {
            "mode": "live_mcp_gated_collection" if live_gated else "read_only_flow_monitoring",
            "requires_live_mcp_ownership": live_gated,
            "allowed_operations": [
                "read_logs",
                "refresh_offline_manifest_advice_gate",
                "prepare_probe_command",
            ],
            "forbidden_operations": ["edit_code", "train_models", "control_live_mcp_without_explicit_ownership"],
        }
    if owner == "engineering_agent":
        if action == "refresh_stale_manifest_schema":
            return {
                "mode": "offline_code_or_data_infra",
                "requires_live_mcp_ownership": False,
                "allowed_operations": [
                    "refresh_manifest_schema",
                    "run_offline_batch_replay",
                    "verify_gate_data_quality",
                    "add_tests",
                ],
                "forbidden_operations": ["control_live_mcp_without_explicit_ownership", "train_strategy_models"],
            }
        return {
            "mode": "offline_code_or_data_infra",
            "requires_live_mcp_ownership": False,
            "allowed_operations": ["edit_runner_or_data_layer", "add_tests", "run_offline_replay"],
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
        "requires_live_mcp_ownership": True,
        "allowed_operations": ["read_artifacts"],
        "forbidden_operations": ["control_live_mcp_without_explicit_ownership"],
    }


def _attach_artifact_paths_to_queues(
    queues: dict[str, list[dict[str, Any]]],
    artifact_paths: dict[str, str],
) -> None:
    for items in queues.values():
        for item in items:
            item["artifact_paths"] = artifact_paths


def _agent_queue_counts(queues: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {owner: len(items) for owner, items in queues.items()}


def _agent_queue_actions(queues: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for owner, items in queues.items():
        owner_actions: list[dict[str, Any]] = []
        for item in items:
            action = str(item.get("action") or "")
            if not action:
                continue
            summary: dict[str, Any] = {"action": action}
            for key in ("count", "reason", "priority"):
                if item.get(key) is not None:
                    summary[key] = item[key]
            if item.get("original_action") is not None:
                summary["original_action"] = item["original_action"]
            replay_resolution = (
                item.get("combat_label_replay_resolution")
                if isinstance(item.get("combat_label_replay_resolution"), dict)
                else {}
            )
            if replay_resolution:
                summary["current_policy_replay_resolved"] = bool(replay_resolution.get("current_policy_resolved"))
            runner_resolution = (
                item.get("gate_execution_recovery_resolution")
                if isinstance(item.get("gate_execution_recovery_resolution"), dict)
                else {}
            )
            if runner_resolution:
                summary["current_runner_recovery_resolved"] = bool(
                    runner_resolution.get("current_runner_resolved")
                )
            execution = item.get("execution_contract")
            if isinstance(execution, dict):
                summary["execution_mode"] = execution.get("mode")
                summary["requires_live_mcp_ownership"] = bool(execution.get("requires_live_mcp_ownership"))
            next_probe_goal = item.get("next_probe_goal")
            if isinstance(next_probe_goal, dict):
                if next_probe_goal.get("metric") is not None:
                    summary["gate_metric"] = next_probe_goal.get("metric")
                if next_probe_goal.get("deficit") is not None:
                    summary["gate_deficit"] = next_probe_goal.get("deficit")
            acceptance = item.get("acceptance_criteria")
            if isinstance(acceptance, dict):
                if acceptance.get("scope") is not None:
                    summary["acceptance_scope"] = acceptance.get("scope")
                if acceptance.get("counts_toward_metric") is not None:
                    summary["counts_toward_metric"] = acceptance.get("counts_toward_metric")
            latest_acceptance = item.get("latest_run_acceptance")
            if isinstance(latest_acceptance, dict):
                if latest_acceptance.get("accepted") is not None:
                    summary["latest_run_accepted"] = bool(latest_acceptance.get("accepted"))
                blockers = latest_acceptance.get("blockers")
                if blockers:
                    summary["latest_run_blockers"] = blockers
            owner_actions.append(summary)
        result[owner] = owner_actions
    return result


def _next_handoff(queues: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_agent = {owner: items[0] for owner, items in queues.items() if items}
    worker_candidates = [
        (owner, item)
        for owner, items in queues.items()
        if owner != "main_agent"
        for item in items
    ]
    primary = _best_handoff_item(worker_candidates)
    coordinator = by_agent.get("main_agent")
    return {
        "primary_worker": {"owner": primary[0], "item": primary[1]} if primary else None,
        "coordinator": {"owner": "main_agent", "item": coordinator} if coordinator else None,
        "by_agent": {owner: {"owner": owner, "item": item} for owner, item in by_agent.items()},
    }


def _best_handoff_item(candidates: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]] | None:
    if not candidates:
        return None
    owner_rank = {
        "engineering_agent": 0,
        "ai_agent": 1,
        "runner_agent": 2,
    }
    return min(
        candidates,
        key=lambda pair: (
            int(pair[1].get("priority", 9)),
            owner_rank.get(pair[0], 9),
            str(pair[1].get("action") or ""),
        ),
    )


def _runner_gate_queue_item(
    gate_next_action: str | None,
    next_probe_goal: dict[str, Any] | None,
    latest_run_acceptance: dict[str, Any] | None,
    gate_execution_recovery: dict[str, Any],
    gate_data_quality: dict[str, Any],
    gate_failure_evidence: dict[str, Any],
) -> dict[str, Any] | None:
    if not gate_next_action:
        return None
    mapping = RUNNER_GATE_ACTIONS.get(str(gate_next_action))
    if not mapping:
        return None
    action, reason = mapping
    priority = 0 if str(gate_next_action) == "collect_pristine_act1_boss_clears" else 1
    runner_item = _gate_queue_item(
        action,
        reason,
        priority,
        next_probe_goal,
        latest_run_acceptance,
        gate_execution_recovery,
        gate_data_quality,
        gate_failure_evidence,
    )
    return runner_item


def _ai_gate_queue_item(
    gate_next_action: str | None,
    next_probe_goal: dict[str, Any] | None,
    latest_run_acceptance: dict[str, Any] | None,
    gate_execution_recovery: dict[str, Any],
    gate_data_quality: dict[str, Any],
    gate_failure_evidence: dict[str, Any],
) -> dict[str, Any] | None:
    if not gate_next_action:
        return None
    mapping = AI_GATE_ACTIONS.get(str(gate_next_action))
    if not mapping:
        return None
    action, reason, priority = mapping
    return _gate_queue_item(
        action,
        reason,
        priority,
        next_probe_goal,
        latest_run_acceptance,
        gate_execution_recovery,
        gate_data_quality,
        gate_failure_evidence,
    )


def _gate_execution_recovery_queue_item(
    gate_next_action: str | None,
    gate_execution_recovery: dict[str, Any],
    gate_failure_evidence: dict[str, Any],
) -> dict[str, Any] | None:
    if gate_next_action != "collect_pristine_act1_boss_clears":
        return None
    action_recovery = (
        gate_execution_recovery.get("action_recovery")
        if isinstance(gate_execution_recovery.get("action_recovery"), dict)
        else {}
    )
    terminal_recovery = (
        gate_execution_recovery.get("terminal_recovery")
        if isinstance(gate_execution_recovery.get("terminal_recovery"), dict)
        else {}
    )
    action_total = int(action_recovery.get("total") or 0)
    synthetic_total = int(terminal_recovery.get("synthetic_terminals") or 0)
    if action_total <= 0 and synthetic_total <= 0:
        return None
    runner_resolution = _current_runner_gate_execution_resolution(gate_execution_recovery)
    current_runner_resolved = bool(runner_resolution.get("current_runner_resolved"))
    action = "stabilize_pristine_gate_execution"
    reason = "pristine_gate_execution_recovery"
    priority = 0
    if current_runner_resolved and synthetic_total <= 0:
        action = "verify_current_runner_for_gate_execution_recovery"
        reason = "current_runner_rewrite_resolved_gate_execution_recovery"
        priority = 2
    item: dict[str, Any] = {
        "action": action,
        "count": action_total + synthetic_total,
        "reason": reason,
        "priority": priority,
        "gate_execution_recovery": gate_execution_recovery,
    }
    if runner_resolution:
        item["gate_execution_recovery_resolution"] = runner_resolution
    if gate_failure_evidence:
        item["gate_failure_evidence"] = gate_failure_evidence
    return item


def _current_runner_gate_execution_resolution(gate_execution_recovery: dict[str, Any]) -> dict[str, Any]:
    action_recovery = (
        gate_execution_recovery.get("action_recovery")
        if isinstance(gate_execution_recovery.get("action_recovery"), dict)
        else {}
    )
    total = int(action_recovery.get("total") or 0)
    if total <= 0:
        return {}
    examples = action_recovery.get("examples") if isinstance(action_recovery.get("examples"), list) else []
    resolved_examples: list[dict[str, Any]] = []
    unresolved_examples: list[dict[str, Any]] = []
    by_resolution: dict[str, int] = {}
    for example in examples:
        if not isinstance(example, dict):
            continue
        resolution = _current_runner_action_recovery_resolution(example)
        compact = {
            key: example.get(key)
            for key in ("source_log", "step", "kind", "action_status", "actions", "available_commands", "last_state")
            if example.get(key) not in (None, "", [], {})
        }
        if resolution:
            compact["resolution"] = resolution
            resolved_examples.append(compact)
            by_resolution[resolution] = by_resolution.get(resolution, 0) + 1
        else:
            unresolved_examples.append(compact)
    uncovered = max(0, total - len(resolved_examples) - len(unresolved_examples))
    unrecovered = int(action_recovery.get("unrecovered") or 0)
    current_runner_resolved = (
        total > 0
        and unrecovered == 0
        and len(resolved_examples) == total
        and not unresolved_examples
        and uncovered == 0
    )
    result: dict[str, Any] = {
        "current_runner_resolved": current_runner_resolved,
        "action_recovery_total": total,
        "resolved_examples": len(resolved_examples),
        "unresolved_examples": len(unresolved_examples),
        "uncovered_events": uncovered,
    }
    if by_resolution:
        result["by_resolution"] = dict(sorted(by_resolution.items()))
    if resolved_examples:
        result["examples"] = resolved_examples[:5]
    if unresolved_examples:
        result["unresolved"] = unresolved_examples[:5]
    return result


def _current_runner_action_recovery_resolution(example: dict[str, Any]) -> str | None:
    actions = example.get("actions") if isinstance(example.get("actions"), list) else []
    first_action = actions[0] if actions and isinstance(actions[0], dict) else {}
    action_name = str(first_action.get("action") or "").lower()
    available = {str(command).lower() for command in (example.get("available_commands") or [])}
    state = example.get("last_state") if isinstance(example.get("last_state"), dict) else {}
    screen = str(state.get("screen_type") or "")
    room_phase = str(state.get("room_phase") or "")
    error = str(example.get("last_error") or "").lower()

    if screen == "MAP" and action_name == "choose" and "proceed" in available and "choose" not in available:
        return "current_runner_waits_stale_map_choose_proceed"
    if screen == "GRID" and action_name == "choose" and "proceed" in available and "choose" not in available:
        return "current_runner_waits_stale_grid_choose_surface"
    if screen == "CHEST" and action_name == "choose" and "proceed" in available and "choose" not in available:
        return "current_runner_rewrites_chest_choose_to_proceed"
    if (
        screen == "CHEST"
        and room_phase == "COMPLETE"
        and action_name == "proceed"
        and ("choose" in available or "possible commands" in error and "choose" in error)
    ):
        return "current_runner_rewrites_chest_proceed_to_choose"
    if screen == "HAND_SELECT" and action_name == "select_cards" and "choose" in available:
        return "current_runner_rewrites_hand_select_to_choose"
    return None


def _gate_data_quality_replay_resolved_issues(
    gate_data_quality: dict[str, Any],
    combat_label_replay_audit: dict[str, Any],
) -> dict[str, Any]:
    if not gate_data_quality or not _combat_label_replay_current_policy_resolved(combat_label_replay_audit):
        return {}
    issues = {
        field: int(gate_data_quality.get(field) or 0)
        for field in COMBAT_LABEL_GATE_DATA_QUALITY_FIELDS
        if int(gate_data_quality.get(field) or 0) > 0
    }
    if not issues:
        return {}
    return {
        "reason": "current_policy_replay_resolved",
        "issues": issues,
        "combat_label_replay_resolution": _combat_label_replay_resolution(combat_label_replay_audit),
    }


def _gate_data_quality_queue_item(
    gate_data_quality: dict[str, Any],
    *,
    static_knowledge_gaps: dict[str, Any] | None = None,
    combat_label_replay_audit: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not gate_data_quality:
        return None
    replay_resolved = _combat_label_replay_current_policy_resolved(combat_label_replay_audit)
    replay_resolution = _combat_label_replay_resolution(combat_label_replay_audit)
    issue_fields = (
        "missing_coverage_manifests",
        "feature_gap_manifests",
        "feature_zero_manifests",
        "unknown_static_manifests",
        "label_exclusion_manifests",
        "shadow_label_excluded",
        "missed_single_card_search_labels",
    )
    issues: dict[str, int] = {}
    resolved_issues: dict[str, int] = {}
    for field in issue_fields:
        count = int(gate_data_quality.get(field) or 0)
        if count <= 0:
            continue
        if replay_resolved and field in COMBAT_LABEL_GATE_DATA_QUALITY_FIELDS:
            resolved_issues[field] = count
            continue
        issues[field] = count
    if not issues:
        return None
    item: dict[str, Any] = {
        "action": "review_gate_data_quality",
        "count": sum(issues.values()),
        "reason": "gate_data_quality_issues_with_replay_resolved_labels"
        if resolved_issues
        else "gate_data_quality_issues",
        "issues": issues,
        "gate_data_quality": gate_data_quality,
        "priority": 1,
    }
    if resolved_issues:
        item["resolved_issues"] = resolved_issues
        item["combat_label_replay_resolution"] = replay_resolution
    if static_knowledge_gaps:
        item["static_knowledge_gaps"] = static_knowledge_gaps
    return item


def _manifest_schema_queue_item(gate_data_quality: dict[str, Any]) -> dict[str, Any] | None:
    if not gate_data_quality:
        return None
    count = int(gate_data_quality.get("schema_missing_manifests") or 0)
    if count <= 0:
        return None
    item: dict[str, Any] = {
        "action": "refresh_stale_manifest_schema",
        "count": count,
        "reason": "manifest_schema_missing_summary_keys",
        "issues": {"schema_missing_manifests": count},
        "schema_missing_key_total": int(gate_data_quality.get("schema_missing_key_total") or 0),
        "gate_data_quality": gate_data_quality,
        "acceptance_criteria": {
            "scope": "offline_manifest_refresh",
            "schema_missing_manifests": 0,
            "touches_live_mcp": False,
            "preserve_act1_boss_gate_status": True,
        },
        "priority": 1,
    }
    missing_keys = _sorted_counts(gate_data_quality.get("schema_missing_summary_keys"))
    if missing_keys:
        item["schema_missing_summary_keys"] = missing_keys
    stale_targets = gate_data_quality.get("stale_manifest_targets")
    if isinstance(stale_targets, list) and stale_targets:
        item["stale_manifest_targets"] = stale_targets
        commands = [
            _manifest_schema_refresh_command(str(target.get("manifest_path") or ""))
            for target in stale_targets
            if isinstance(target, dict) and str(target.get("manifest_path") or "").strip()
        ]
        if commands:
            item["refresh_manifest_schema_commands"] = commands
    if gate_data_quality.get("stale_manifest_targets_truncated"):
        item["stale_manifest_targets_truncated"] = True
        item["stale_manifest_targets_remaining"] = int(
            gate_data_quality.get("stale_manifest_targets_remaining") or 0
        )
    return item


def _manifest_schema_refresh_command(manifest_path: str) -> dict[str, Any]:
    return {
        "kind": "training_manifest_schema_refresh",
        "manifest_path": manifest_path,
        "argv": [
            "python",
            "-m",
            "slay_ai.training_manifest",
            "--refresh-from",
            manifest_path,
            "--output",
            manifest_path,
            "--knowledge-dir",
            str(Path("data") / "static_knowledge"),
        ],
        "touches_live_mcp": False,
        "trains_models": False,
        "writes_models": False,
    }


def _gate_queue_item(
    action: str,
    reason: str,
    priority: int,
    next_probe_goal: dict[str, Any] | None,
    latest_run_acceptance: dict[str, Any] | None,
    gate_execution_recovery: dict[str, Any],
    gate_data_quality: dict[str, Any],
    gate_failure_evidence: dict[str, Any],
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "action": action,
        "reason": reason,
        "priority": priority,
    }
    return _attach_gate_context(
        item,
        next_probe_goal,
        latest_run_acceptance,
        gate_execution_recovery,
        gate_data_quality,
        gate_failure_evidence,
    )


def _attach_gate_context(
    item: dict[str, Any],
    next_probe_goal: dict[str, Any] | None,
    latest_run_acceptance: dict[str, Any] | None,
    gate_execution_recovery: dict[str, Any] | None = None,
    gate_data_quality: dict[str, Any] | None = None,
    gate_failure_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if next_probe_goal:
        item["next_probe_goal"] = next_probe_goal
        acceptance_criteria = next_probe_goal.get("acceptance_criteria")
        if isinstance(acceptance_criteria, dict):
            item["acceptance_criteria"] = acceptance_criteria
    if latest_run_acceptance:
        item["latest_run_acceptance"] = latest_run_acceptance
    if gate_execution_recovery:
        item["gate_execution_recovery"] = gate_execution_recovery
    if gate_data_quality:
        item["gate_data_quality"] = gate_data_quality
    if gate_failure_evidence:
        item["gate_failure_evidence"] = gate_failure_evidence
    return item


def _group_runs_for_queue(runs: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        owner = str(run.get("owner") or "main_agent")
        action = str(run.get("action") or "")
        if not action or action in {"keep_for_training", "preserve_boss_validation_evidence"}:
            continue
        grouped.setdefault((owner, action), []).append(run)
    return [
        (owner, action, matching_runs)
        for (owner, action), matching_runs in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


def _priority_for_run_action(action: str, owner: str) -> int:
    if action == "fix_execution_layer":
        return 0
    if action in {"fix_combat_lethal_priority", "fix_combat_search_priority"}:
        return 0
    if action.startswith("exclude_and_collect"):
        return 1
    if owner == "ai_agent":
        return 1
    return 2


def _owner_for_attribution(attribution: str) -> str:
    if attribution in AI_ATTRIBUTIONS:
        return "ai_agent"
    if attribution in ENGINEERING_ATTRIBUTIONS:
        return "engineering_agent"
    return "main_agent"


def _write_artifact_manifest(
    path: Path,
    *,
    batch_name: str,
    inputs: list[str],
    resolved_logs: list[str],
    artifact_paths: dict[str, str],
    agent_handoff_paths: dict[str, str],
    agent_prompt_paths: dict[str, str],
) -> dict[str, Any]:
    input_health: list[dict[str, Any]] = []
    for index, input_path in enumerate(inputs):
        item = _path_health(input_path)
        item["index"] = index
        input_health.append(item)

    resolved_log_health: list[dict[str, Any]] = []
    for index, log_path in enumerate(resolved_logs):
        item = _path_health(log_path)
        item["index"] = index
        resolved_log_health.append(item)

    files: dict[str, dict[str, Any]] = {}
    for key, path_text in sorted(artifact_paths.items()):
        if key == "artifact_manifest_path":
            continue
        files[key] = _path_health(path_text)
    for owner, path_text in sorted(agent_handoff_paths.items()):
        files[f"{owner}_handoff"] = _path_health(path_text)
    for owner, path_text in sorted(agent_prompt_paths.items()):
        files[f"{owner}_prompt"] = _path_health(path_text)

    missing = [f"input[{item['index']}]" for item in input_health if not item.get("exists")]
    missing.extend(f"resolved_log[{item['index']}]" for item in resolved_log_health if not item.get("exists"))
    missing.extend(key for key, item in files.items() if not item.get("exists"))
    errors = [f"input[{item['index']}]" for item in input_health if item.get("error")]
    errors.extend(f"resolved_log[{item['index']}]" for item in resolved_log_health if item.get("error"))
    errors.extend(key for key, item in files.items() if item.get("error"))
    warnings: list[str] = []
    if not resolved_logs:
        warnings.append("no_resolved_logs")
    payload = {
        "version": 1,
        "name": batch_name,
        "artifact_manifest_path": str(path),
        "inputs": inputs,
        "input_count": len(inputs),
        "input_health": input_health,
        "resolved_logs": resolved_logs,
        "resolved_log_count": len(resolved_logs),
        "resolved_log_health": resolved_log_health,
        "artifact_paths": artifact_paths,
        "agent_handoff_paths": agent_handoff_paths,
        "agent_prompt_paths": agent_prompt_paths,
        "files": files,
        "missing": missing,
        "errors": errors,
        "warnings": warnings,
        "ok": not missing and not errors,
    }
    _write_json(path, payload)
    return payload


def _path_health(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    try:
        exists = path.exists()
        if not exists:
            return {"path": path_text, "exists": False, "kind": "missing"}
        if path.is_file():
            return {
                "path": path_text,
                "exists": True,
                "kind": "file",
                "bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        if path.is_dir():
            file_count, total_bytes = _directory_stats(path)
            return {
                "path": path_text,
                "exists": True,
                "kind": "directory",
                "file_count": file_count,
                "bytes": total_bytes,
            }
        return {
            "path": path_text,
            "exists": True,
            "kind": "other",
            "bytes": path.stat().st_size,
        }
    except OSError as exc:
        return {"path": path_text, "exists": path.exists(), "kind": "error", "error": str(exc)}


def _directory_stats(path: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        file_count += 1
        try:
            total_bytes += child.stat().st_size
        except OSError:
            pass
    return file_count, total_bytes


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_artifact_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name.strip())
    return cleaned or "batch"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_ascii_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
