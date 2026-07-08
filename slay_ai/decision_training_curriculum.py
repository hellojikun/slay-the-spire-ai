"""Build data-collection curriculum targets for shadow decision training."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_TARGETS = {
    "reward_total": 50,
    "reward_skip": 10,
    "purge_total": 20,
    "purge_remove": 10,
    "purge_keep": 10,
}

BOUNDARY = {
    "runtime_authority": False,
    "runtime_default_enabled": False,
    "runtime_authority_level": "shadow",
    "does_not_control_live_mcp": True,
    "direct_mcp_control": False,
    "requires_audited_promotion": True,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create decision-model evidence collection curriculum.")
    parser.add_argument("decision_summary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    summary = json.loads(args.decision_summary.read_text(encoding="utf-8"))
    curriculum = build_decision_training_curriculum(summary)
    write_decision_training_curriculum(summary, output_path=args.output, curriculum=curriculum)
    print(
        "decision_training_curriculum: "
        f"status={curriculum['status']} active_targets={curriculum['active_target_count']} output={args.output}"
    )
    return 0


def write_decision_training_curriculum(
    decision_summary: dict[str, Any],
    *,
    output_path: Path,
    curriculum: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = curriculum or build_decision_training_curriculum(decision_summary)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload["output_path"] = str(output_path)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def build_decision_training_curriculum(decision_summary: dict[str, Any]) -> dict[str, Any]:
    live = decision_summary.get("live_shadow_disagreement") if isinstance(decision_summary.get("live_shadow_disagreement"), dict) else {}
    promotion = decision_summary.get("promotion_readiness") if isinstance(decision_summary.get("promotion_readiness"), dict) else {}
    live_promotion = live.get("promotion_readiness") if isinstance(live.get("promotion_readiness"), dict) else {}
    task_counts = live.get("task_counts") if isinstance(live.get("task_counts"), dict) else {}
    actual_by_task = live.get("actual_counts_by_task") if isinstance(live.get("actual_counts_by_task"), dict) else {}
    take_counts = actual_by_task.get("take_skip") if isinstance(actual_by_task.get("take_skip"), dict) else {}
    purge_counts = actual_by_task.get("purge_remove") if isinstance(actual_by_task.get("purge_remove"), dict) else {}

    observed = {
        "reward_total": int(task_counts.get("take_skip") or 0),
        "reward_skip": int(take_counts.get("skip") or 0),
        "purge_total": int(task_counts.get("purge_remove") or 0),
        "purge_remove": int(purge_counts.get("remove") or 0),
        "purge_keep": int(purge_counts.get("keep_candidate") or 0),
    }
    targets = [
        _target(
            key="reward_total",
            title="Collect live card reward decisions",
            surface="card_reward_take_skip",
            observed=observed["reward_total"],
            target=DEFAULT_TARGETS["reward_total"],
            rationale="Need enough local MCP card reward examples before reward model assist.",
            acceptance="CARD_REWARD records with decision learn_card_pick or action=skip.",
        ),
        _target(
            key="reward_skip",
            title="Collect bad-reward skip examples",
            surface="card_reward_take_skip",
            observed=observed["reward_skip"],
            target=DEFAULT_TARGETS["reward_skip"],
            rationale="User explicitly wants the AI to skip rewards when no good card exists.",
            acceptance="CARD_REWARD records where the actual decision action is skip.",
        ),
        _target(
            key="purge_total",
            title="Collect live purge decision contexts",
            surface="purge_remove",
            observed=observed["purge_total"],
            target=DEFAULT_TARGETS["purge_total"],
            rationale="Purge/remove needs local shop or grid contexts before assist.",
            acceptance="SHOP_SCREEN with purge_available or GRID for_purge records scored by shadow model.",
        ),
        _target(
            key="purge_remove",
            title="Collect actual remove examples",
            surface="purge_remove",
            observed=observed["purge_remove"],
            target=DEFAULT_TARGETS["purge_remove"],
            rationale="Positive purge labels must come from real removed cards, not weak negatives.",
            acceptance="SHOP_SCREEN purge selected or GRID for_purge selected-card records.",
        ),
        _target(
            key="purge_keep",
            title="Collect purge keep examples",
            surface="purge_remove",
            observed=observed["purge_keep"],
            target=DEFAULT_TARGETS["purge_keep"],
            rationale="Need keep_candidate coverage so the model does not over-remove cards.",
            acceptance="SHOP_SCREEN where purge is available but the actual decision buys/declines instead.",
        ),
    ]
    active_targets = [target for target in targets if target["deficit"] > 0]
    blocking_reasons = _dedupe(
        list(promotion.get("blocking_reasons") or []) + list(live_promotion.get("blocking_reasons") or [])
    )
    return {
        "version": 1,
        "status": "active" if active_targets else "satisfied",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "objective": "collect_live_shadow_evidence_before_decision_model_assist",
        "active_target_count": len(active_targets),
        "targets": targets,
        "active_targets": active_targets,
        "blocking_reasons": blocking_reasons,
        "next_training_actions": _next_training_actions(active_targets),
        "source_paths": {
            "decision_model": decision_summary.get("model_path"),
            "live_shadow_disagreement": live.get("summary_output") or live.get("output_path"),
            "external_predictions": decision_summary.get("prediction_path"),
        },
        "authority_boundary": dict(BOUNDARY),
    }


def _target(
    *,
    key: str,
    title: str,
    surface: str,
    observed: int,
    target: int,
    rationale: str,
    acceptance: str,
) -> dict[str, Any]:
    deficit = max(0, int(target) - int(observed))
    return {
        "key": key,
        "title": title,
        "surface": surface,
        "observed": int(observed),
        "target": int(target),
        "deficit": deficit,
        "status": "satisfied" if deficit == 0 else "needs_data",
        "priority": _priority(key, deficit),
        "execution_contract": "live_mcp_gated_collection",
        "rationale": rationale,
        "acceptance": acceptance,
        "forbidden_changes": [
            "do_not_add_hardcoded_strategy_rules",
            "do_not_change_live_policy_to_force_this_sample",
            "do_not_promote_external_prior_to_runtime_authority",
        ],
    }


def _priority(key: str, deficit: int) -> int:
    if deficit <= 0:
        return 0
    if key in {"reward_skip", "purge_remove", "purge_keep"}:
        return 1
    return 2


def _next_training_actions(active_targets: list[dict[str, Any]]) -> list[str]:
    if not active_targets:
        return ["rerun_live_shadow_disagreement_after_next_training_cycle"]
    actions = ["collect_targeted_live_mcp_logs_for_active_curriculum_targets"]
    surfaces = {target.get("surface") for target in active_targets}
    if "card_reward_take_skip" in surfaces:
        actions.append("prefer_reviewing_card_reward_skip_coverage_before_assist")
    if "purge_remove" in surfaces:
        actions.append("prefer_runs_that_visit_shop_or_grid_purge_contexts")
    actions.append("retrain_external_structure_decision_model_after_new_live_shadow_rows")
    actions.append("keep_model_authority_shadow_until_curriculum_targets_are_satisfied")
    return actions


def _dedupe(items: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


if __name__ == "__main__":
    raise SystemExit(main())