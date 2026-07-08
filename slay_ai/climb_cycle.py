"""Run the climb -> replay -> learn cycle as one auditable command."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

from .campaign import Target, hardware_snapshot, load_progress, run_target_attempts
from .learn import LEARNED_MEMORY, read_log, record_learning_replay
from .memory import StrategyMemory
from .mcp.client import MCPClient, MCPError
from .model import (
    COMBAT_SEARCH_MODEL_PATH,
    COMBAT_VALUE_MODEL_PATH,
    DECK_QUALITY_MODEL_PATH,
    MODEL_PATH,
    POTION_TEMPO_MODEL_PATH,
    ROUTE_RISK_MODEL_PATH,
    CardValueModel,
)
from .offline_batch import run_offline_batch
from .runner import ROOT
from .shadow_quality import DEFAULT_SOURCE_QUALITY, SOURCE_QUALITY_CHOICES, source_quality_allowed
from .train_card_model import hardware_metadata, load_examples as load_card_examples, resolve_training_source, train_stats_model
from .train_combat_value_model import load_examples as load_combat_value_examples
from .train_combat_value_model import train_torch_model as train_combat_value_torch_model
from .shadow_advice import ShadowModels, score_shadow_inputs, write_advice
from .shadow_inputs import category_training_source, resolve_shadow_training_source
from .train_combat_search_model import load_examples_with_stats as load_combat_examples_with_stats
from .train_combat_search_model import train_stats_model as train_combat_model
from .train_deck_quality_model import load_examples_with_stats as load_deck_examples_with_stats
from .train_deck_quality_model import train_stats_model as train_deck_model
from .import_external_runs import EXTERNAL_PRIOR_GRADE
from .train_external_priors import external_prior_runtime_output_risk
from .train_external_priors import load_examples_with_stats as load_external_prior_examples_with_stats
from .train_external_priors import train_external_card_prior_model
from .train_external_structure_priors import train_external_structure_priors
from .train_decision_multitask_model import train_decision_multitask_model
from .decision_shadow_disagreement import evaluate_live_shadow_disagreements
from .decision_training_curriculum import write_decision_training_curriculum
from .train_potion_tempo_model import load_examples_with_stats as load_potion_examples_with_stats
from .train_potion_tempo_model import train_stats_model as train_potion_model
from .train_route_risk_model import load_examples_with_stats as load_route_examples_with_stats
from .train_route_risk_model import train_stats_model as train_route_model


SHADOW_ROW_FILES = (
    "route_risk.jsonl",
    "potion_tempo.jsonl",
    "pre_boss_deck_quality.jsonl",
    "combat_search_labels.jsonl",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a real climb batch, rebuild offline evidence, and train gated learning artifacts."
    )
    parser.add_argument("--name", help="Stable cycle name. Defaults to a timestamped climb_cycle_* name.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/mcp")
    parser.add_argument("--characters", nargs="+", default=["IRONCLAD"])
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--attempts-per-target", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--interval", type=float, default=0.08)
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument("--cooldown", type=float, default=0.5)
    parser.add_argument(
        "--existing-save",
        choices=["fail", "continue", "abandon"],
        default="fail",
        help="What to do if a fresh live attempt is blocked by an existing save.",
    )
    parser.add_argument("--skip-live", action="store_true", help="Do not control MCP; only replay supplied logs.")
    parser.add_argument("--logs", nargs="*", type=Path, help="Extra JSONL logs or directories to include in replay.")
    parser.add_argument("--log-dir", type=Path, help="Live run log directory. Defaults under runs/.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "climb_cycles")
    parser.add_argument("--knowledge-dir", type=Path, default=ROOT / "data" / "static_knowledge")
    parser.add_argument("--skip-offline", action="store_true", help="Do not rebuild manifest/advice/gate artifacts.")
    parser.add_argument("--skip-train", action="store_true", help="Do not train card/shadow models or learned memory.")
    parser.add_argument("--model-dir", type=Path, help="Write all learned JSON models into this directory.")
    parser.add_argument(
        "--promote-models",
        action="store_true",
        help="Write trained models to the default models/ paths. By default models stay under the cycle output.",
    )
    parser.add_argument("--card-model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--learned-path", type=Path, default=LEARNED_MEMORY)
    parser.add_argument("--reset-learned-memory", action="store_true")
    parser.add_argument("--source-quality", choices=SOURCE_QUALITY_CHOICES, default=DEFAULT_SOURCE_QUALITY)
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--max-weight", type=float, default=2.0)
    parser.add_argument("--card-min-count", type=int, default=1)
    parser.add_argument("--card-max-delta", type=float, default=10.0)
    parser.add_argument(
        "--external-prior-input",
        action="append",
        type=Path,
        default=[],
        help="External prior manifest, card_reward_priors.jsonl, or directory. Trains scratch external priors only.",
    )
    parser.add_argument(
        "--external-prior-model-dir",
        type=Path,
        help="Directory for cycle external-prior models. Defaults under the cycle training directory.",
    )
    parser.add_argument("--external-prior-min-count", type=int, default=2)
    parser.add_argument("--external-prior-max-delta", type=float, default=4.0)
    parser.add_argument("--external-prior-scale", type=float, default=0.35)
    parser.add_argument("--external-prior-blend-scale", type=float, default=0.25)
    parser.add_argument("--external-prior-blend-max-delta", type=float, default=0.75)
    parser.add_argument(
        "--model-authority",
        choices=["shadow", "assist", "pilot"],
        default="shadow",
        help=(
            "How much learned signals may influence live decisions. assist/pilot can affect "
            "route-risk scoring, card reward tie-breaks, and high-confidence potion tempo, "
            "but do not directly control MCP outside policy decisions."
        ),
    )
    parser.add_argument("--use-all-hardware", action="store_true")
    parser.add_argument("--gate-min-reached", type=int, default=3)
    parser.add_argument("--gate-min-cleared", type=int, default=2)
    parser.add_argument("--gate-min-pristine-cleared", type=int, default=2)
    args = parser.parse_args(argv)

    result = run_climb_cycle(
        name=args.name,
        endpoint=args.endpoint,
        characters=args.characters,
        ascension=args.ascension,
        attempts_per_target=args.attempts_per_target,
        max_steps=args.max_steps,
        interval=args.interval,
        startup_timeout=args.startup_timeout,
        cooldown=args.cooldown,
        existing_save=args.existing_save,
        run_live=not args.skip_live,
        logs=args.logs,
        log_dir=args.log_dir,
        output_dir=args.output_dir,
        knowledge_dir=args.knowledge_dir,
        run_offline=not args.skip_offline,
        train=not args.skip_train,
        model_dir=args.model_dir,
        promote_models=args.promote_models,
        card_model_path=args.card_model_path,
        learned_path=args.learned_path,
        reset_learned_memory=args.reset_learned_memory,
        source_quality=args.source_quality,
        min_count=args.min_count,
        max_weight=args.max_weight,
        card_min_count=args.card_min_count,
        card_max_delta=args.card_max_delta,
        external_prior_inputs=args.external_prior_input,
        external_prior_model_dir=args.external_prior_model_dir,
        external_prior_min_count=args.external_prior_min_count,
        external_prior_max_delta=args.external_prior_max_delta,
        external_prior_scale=args.external_prior_scale,
        external_prior_blend_scale=args.external_prior_blend_scale,
        external_prior_blend_max_delta=args.external_prior_blend_max_delta,
        model_authority=args.model_authority,
        use_all_hardware=args.use_all_hardware,
        gate_min_reached=args.gate_min_reached,
        gate_min_cleared=args.gate_min_cleared,
        gate_min_pristine_cleared=args.gate_min_pristine_cleared,
    )
    print(result["status_line"])
    print(f"Cycle summary: {result['summary_path']}")
    offline = result.get("offline_batch") or {}
    if offline.get("gate_status_line"):
        print(offline["gate_status_line"])
    if offline.get("offline_batch_next_line"):
        print(offline["offline_batch_next_line"])
    training = result.get("training") or {}
    if training.get("status_line"):
        print(training["status_line"])
    return 0


def run_climb_cycle(
    *,
    name: str | None = None,
    endpoint: str = "http://127.0.0.1:8080/mcp",
    characters: list[str] | None = None,
    ascension: int = 0,
    attempts_per_target: int = 1,
    max_steps: int = 500,
    interval: float = 0.08,
    startup_timeout: float = 20.0,
    cooldown: float = 0.5,
    existing_save: str = "fail",
    run_live: bool = True,
    logs: Iterable[Path] | None = None,
    log_dir: Path | None = None,
    output_dir: Path = ROOT / "data" / "climb_cycles",
    knowledge_dir: Path | None = ROOT / "data" / "static_knowledge",
    run_offline: bool = True,
    train: bool = True,
    model_dir: Path | None = None,
    promote_models: bool = False,
    card_model_path: Path = MODEL_PATH,
    learned_path: Path = LEARNED_MEMORY,
    reset_learned_memory: bool = False,
    source_quality: str = DEFAULT_SOURCE_QUALITY,
    min_count: int = 2,
    max_weight: float = 2.0,
    card_min_count: int = 1,
    card_max_delta: float = 10.0,
    external_prior_inputs: Iterable[Path] | None = None,
    external_prior_model_dir: Path | None = None,
    external_prior_min_count: int = 2,
    external_prior_max_delta: float = 4.0,
    external_prior_scale: float = 0.35,
    external_prior_blend_scale: float = 0.25,
    external_prior_blend_max_delta: float = 0.75,
    model_authority: str = "shadow",
    use_all_hardware: bool = False,
    gate_min_reached: int = 3,
    gate_min_cleared: int = 2,
    gate_min_pristine_cleared: int = 2,
) -> dict[str, Any]:
    cycle_name = _safe_artifact_name(name or f"climb_cycle_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    cycle_dir = output_dir / cycle_name
    cycle_dir.mkdir(parents=True, exist_ok=True)
    characters = [character.upper() for character in (characters or ["IRONCLAD"])]
    live_log_dir = log_dir or (ROOT / "runs" / f"ai_runs_{cycle_name}")
    progress_file = cycle_dir / f"campaign_{cycle_name}.json"
    live_manifest_dir = cycle_dir / "live_manifests"
    live_shadow_dir = cycle_dir / "live_shadow"
    live_advice_dir = cycle_dir / "live_advice"
    live_gate_path = cycle_dir / f"act1_boss_gate_live_{cycle_name}.json"

    live_summary = {
        "ran": False,
        "log_dir": str(live_log_dir),
        "progress_file": str(progress_file),
        "characters": characters,
        "ascension": ascension,
        "attempts_per_target": attempts_per_target,
        "model_authority": model_authority,
    }
    if run_live and attempts_per_target > 0:
        try:
            live_summary = _run_live_batch(
                endpoint=endpoint,
                characters=characters,
                ascension=ascension,
                attempts_per_target=attempts_per_target,
                max_steps=max_steps,
                interval=interval,
                startup_timeout=startup_timeout,
                cooldown=cooldown,
                existing_save=existing_save,
                log_dir=live_log_dir,
                progress_file=progress_file,
                manifest_dir=live_manifest_dir,
                shadow_dir=live_shadow_dir,
                advice_dir=live_advice_dir,
                knowledge_dir=knowledge_dir,
                gate_output=live_gate_path,
                model_authority=model_authority,
                use_all_hardware=use_all_hardware,
                gate_min_reached=gate_min_reached,
                gate_min_cleared=gate_min_cleared,
                gate_min_pristine_cleared=gate_min_pristine_cleared,
            )
        except MCPError as exc:
            live_summary = {
                "ran": True,
                "completed": False,
                "error": str(exc),
                "log_dir": str(live_log_dir),
                "progress_file": str(progress_file),
                "manifest_dir": str(live_manifest_dir),
                "shadow_dir": str(live_shadow_dir),
                "advice_dir": str(live_advice_dir),
                "act1_boss_gate_output": str(live_gate_path),
                "characters": characters,
                "ascension": ascension,
                "attempts_per_target": attempts_per_target,
                "model_authority": model_authority,
            }

    replay_inputs = _replay_inputs(logs, include_live_dir=live_log_dir if live_summary.get("ran") else None)
    offline_summary: dict[str, Any] | None = None
    if run_offline:
        offline_summary = run_offline_batch(
            replay_inputs,
            output_dir=cycle_dir / "offline",
            name=cycle_name,
            knowledge_dir=knowledge_dir,
            write_shadow_advice=True,
            write_diagnosis=True,
            write_gate=True,
            character=characters[0],
            ascension=ascension,
            min_reached=gate_min_reached,
            min_cleared=gate_min_cleared,
            min_pristine_cleared=gate_min_pristine_cleared,
        )

    training_summary: dict[str, Any] = {"ran": False, "skipped": "train_disabled"}
    if train and offline_summary is not None:
        training_summary = _train_from_offline_batch(
            offline_summary,
            cycle_dir=cycle_dir,
            model_dir=model_dir,
            promote_models=promote_models,
            card_model_path=card_model_path,
            learned_path=learned_path,
            reset_learned_memory=reset_learned_memory,
            source_quality=source_quality,
            min_count=min_count,
            max_weight=max_weight,
            card_min_count=card_min_count,
            card_max_delta=card_max_delta,
            external_prior_inputs=external_prior_inputs,
            external_prior_model_dir=external_prior_model_dir,
            external_prior_min_count=external_prior_min_count,
            external_prior_max_delta=external_prior_max_delta,
            external_prior_scale=external_prior_scale,
            external_prior_blend_scale=external_prior_blend_scale,
            external_prior_blend_max_delta=external_prior_blend_max_delta,
        )
    review_summary: dict[str, Any] | None = None
    if offline_summary is not None:
        review_summary = _write_run_reviews(
            offline_summary,
            training_summary=training_summary,
            output_dir=cycle_dir / "run_reviews",
        )

    summary = {
        "version": 1,
        "stage": "climb_review_learn",
        "name": cycle_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "summary_path": str(cycle_dir / f"climb_cycle_{cycle_name}.json"),
        "cycle_dir": str(cycle_dir),
        "policy_boundary": {
            "heuristic_strategy_investment": "frozen",
            "runtime_controller": "existing_policy_and_search",
            "learning_authority": "offline_shadow_models_and_card_memory",
            "model_authority": model_authority,
            "model_authority_scope": _model_authority_scope(model_authority),
        },
        "live": live_summary,
        "replay_inputs": [str(path) for path in replay_inputs],
        "offline_batch": _compact_offline_summary(offline_summary),
        "training": training_summary,
        "run_reviews": review_summary,
    }
    summary["status_line"] = _cycle_status_line(summary)
    _write_json(Path(summary["summary_path"]), summary)
    return summary


def _model_authority_scope(model_authority: str) -> str:
    if model_authority in {"assist", "pilot"}:
        return "route_risk_card_reward_and_potion_tempo_assist"
    return "card_reward_tiebreaker_only"


def _run_live_batch(
    *,
    endpoint: str,
    characters: list[str],
    ascension: int,
    attempts_per_target: int,
    max_steps: int,
    interval: float,
    startup_timeout: float,
    cooldown: float,
    existing_save: str,
    log_dir: Path,
    progress_file: Path,
    manifest_dir: Path,
    shadow_dir: Path,
    advice_dir: Path,
    knowledge_dir: Path | None,
    gate_output: Path,
    model_authority: str,
    use_all_hardware: bool,
    gate_min_reached: int,
    gate_min_cleared: int,
    gate_min_pristine_cleared: int,
) -> dict[str, Any]:
    args = SimpleNamespace(
        endpoint=endpoint,
        existing_save=existing_save,
        max_steps=max_steps,
        interval=interval,
        startup_timeout=startup_timeout,
        log_dir=log_dir,
        no_manifest=False,
        manifest_dir=manifest_dir,
        manifest_knowledge_dir=knowledge_dir,
        manifest_shadow_dir=shadow_dir,
        manifest_advice_dir=advice_dir,
        route_risk_model_path=ROUTE_RISK_MODEL_PATH,
        potion_tempo_model_path=POTION_TEMPO_MODEL_PATH,
        deck_quality_model_path=DECK_QUALITY_MODEL_PATH,
        combat_search_model_path=COMBAT_SEARCH_MODEL_PATH,
        model_authority=model_authority,
        act1_boss_gate_output=gate_output,
        act1_boss_gate_min_reached=gate_min_reached,
        act1_boss_gate_min_cleared=gate_min_cleared,
        act1_boss_gate_min_pristine_cleared=gate_min_pristine_cleared,
        progress_file=progress_file,
        attempts_per_target=attempts_per_target,
        cooldown=cooldown,
        dry_run=False,
    )
    progress = load_progress(progress_file)
    progress.setdefault("hardware", hardware_snapshot(use_all_hardware))
    progress.setdefault("targets", {})
    client = MCPClient(endpoint)
    client.ensure_initialized()
    memory = StrategyMemory.load()
    for character in characters:
        run_target_attempts(args, Target(character, ascension), progress, client, memory)
    return {
        "ran": True,
        "log_dir": str(log_dir),
        "progress_file": str(progress_file),
        "manifest_dir": str(manifest_dir),
        "shadow_dir": str(shadow_dir),
        "advice_dir": str(advice_dir),
        "act1_boss_gate_output": str(gate_output),
        "model_authority": model_authority,
        "targets": progress.get("targets", {}),
        "hardware": progress.get("hardware", {}),
    }


def _train_from_offline_batch(
    offline_summary: dict[str, Any],
    *,
    cycle_dir: Path,
    model_dir: Path | None,
    promote_models: bool,
    card_model_path: Path,
    learned_path: Path,
    reset_learned_memory: bool,
    source_quality: str,
    min_count: int,
    max_weight: float,
    card_min_count: int,
    card_max_delta: float,
    external_prior_inputs: Iterable[Path] | None,
    external_prior_model_dir: Path | None,
    external_prior_min_count: int,
    external_prior_max_delta: float,
    external_prior_scale: float,
    external_prior_blend_scale: float,
    external_prior_blend_max_delta: float,
) -> dict[str, Any]:
    manifest_path = Path(str(offline_summary["manifest_path"]))
    shadow_dir = Path(str(offline_summary["shadow_dir"]))
    effective_model_dir = model_dir
    model_destination = "explicit_model_dir"
    if effective_model_dir is None and not promote_models:
        effective_model_dir = cycle_dir / "training" / "models"
        model_destination = "cycle_scratch"
    elif effective_model_dir is None:
        model_destination = "default_models"
    model_paths = _shadow_model_paths(effective_model_dir)
    effective_card_model_path = (
        card_model_path if effective_model_dir is None else effective_model_dir / MODEL_PATH.name
    )
    combat_value_model_path = (
        COMBAT_VALUE_MODEL_PATH if effective_model_dir is None else effective_model_dir / COMBAT_VALUE_MODEL_PATH.name
    )
    summary: dict[str, Any] = {
        "ran": True,
        "source_quality": source_quality,
        "model_destination": model_destination,
        "promote_models": promote_models,
        "manifest_path": str(manifest_path),
        "shadow_dir": str(shadow_dir),
    }

    shadow_summary_path = cycle_dir / "training" / f"shadow_training_{cycle_dir.name}.json"
    shadow_summary = _train_shadow_models_by_category(
        shadow_dir,
        model_paths=model_paths,
        source_quality=source_quality,
        min_count=min_count,
        max_weight=max_weight,
        advice_output_dir=cycle_dir / "training" / "shadow_advice",
    )
    _write_json(shadow_summary_path, shadow_summary)
    shadow_status = "trained" if shadow_summary["trained_model_count"] > 0 else "skipped"
    summary["shadow_models"] = {
        "status": shadow_status,
        "reason": None if shadow_status == "trained" else "no_shadow_rows_allowed_by_source_quality",
        "accepted_rows": shadow_summary["accepted_rows"],
        "summary_path": str(shadow_summary_path),
        "model_paths": {key: str(path) for key, path in model_paths.items()},
        "models": shadow_summary.get("models", {}),
    }

    card_summary = _train_card_model_from_manifest(
        manifest_path,
        card_model_path=effective_card_model_path,
        min_count=card_min_count,
        max_delta=card_max_delta,
    )
    summary["card_model"] = card_summary
    external_summary = _train_external_priors_from_inputs(
        external_prior_inputs or [],
        cycle_dir=cycle_dir,
        model_dir=external_prior_model_dir,
        min_count=external_prior_min_count,
        max_delta=external_prior_max_delta,
        prior_scale=external_prior_scale,
    )
    summary["external_priors"] = external_summary
    external_structure_summary = _train_external_structure_priors_from_inputs(
        external_prior_inputs or [],
        cycle_dir=cycle_dir,
        model_dir=external_prior_model_dir,
    )
    summary["external_structure_priors"] = external_structure_summary
    external_decision_summary = _train_external_structure_decision_model(
        external_structure_summary,
        cycle_dir=cycle_dir,
    )
    external_decision_summary["live_shadow_disagreement"] = _evaluate_external_decision_live_shadow(
        external_decision_summary,
        manifest_path=manifest_path,
        cycle_dir=cycle_dir,
    )
    external_decision_summary["training_curriculum"] = _write_external_decision_training_curriculum(
        external_decision_summary,
        cycle_dir=cycle_dir,
    )
    summary["external_structure_decision_model"] = external_decision_summary
    summary["card_external_prior_blend"] = _blend_card_model_with_external_prior(
        card_summary,
        external_summary,
        output_path=cycle_dir / "training" / "models" / "card_value_model_external_prior_blend.json",
        external_scale=external_prior_blend_scale,
        max_external_delta=external_prior_blend_max_delta,
    )
    summary["combat_value_model"] = _train_combat_value_model_from_shadow(
        shadow_dir,
        model_path=combat_value_model_path,
        summary_output=cycle_dir / "training" / "combat_value_training_summary.json",
        source_quality=source_quality,
    )
    memory_summary = _replay_learned_memory(
        manifest_path,
        learned_path=learned_path,
        reset=reset_learned_memory,
    )
    summary["learned_memory"] = memory_summary
    summary["status_line"] = _training_status_line(summary)
    return summary


def _train_shadow_models_by_category(
    shadow_dir: Path,
    *,
    model_paths: dict[str, Path],
    source_quality: str,
    min_count: int,
    max_weight: float,
    advice_output_dir: Path | None = None,
) -> dict[str, Any]:
    training_source = resolve_shadow_training_source([shadow_dir])
    route_load = load_route_examples_with_stats([shadow_dir], source_quality=source_quality)
    potion_load = load_potion_examples_with_stats([shadow_dir], source_quality=source_quality)
    deck_load = load_deck_examples_with_stats([shadow_dir], source_quality=source_quality)
    combat_load = load_combat_examples_with_stats([shadow_dir], source_quality=source_quality)
    loads = {
        "route_risk": route_load,
        "potion_tempo": potion_load,
        "pre_boss_deck_quality": deck_load,
        "combat_search": combat_load,
    }
    models: dict[str, Any] = {}
    trained_model_count = 0
    accepted_rows = 0

    if route_load.examples:
        route_model = train_route_model(
            route_load.examples,
            model_paths["route_risk"],
            min_count=min_count,
            max_weight=max_weight,
            load_quality=route_load.stats,
        )
        _finish_shadow_model(route_model, training_source, "route_risk", source_quality)
        models["route_risk"] = _shadow_stats_model_summary(
            route_model.path,
            len(route_load.examples),
            route_model.metadata,
            route_model.feature_weights,
        )
        trained_model_count += 1
        accepted_rows += len(route_load.examples)
    else:
        models["route_risk"] = _skipped_shadow_model_summary(model_paths["route_risk"], route_load.stats)

    if potion_load.examples:
        potion_model = train_potion_model(
            potion_load.examples,
            model_paths["potion_tempo"],
            min_count=min_count,
            max_weight=max_weight,
            load_quality=potion_load.stats,
        )
        _finish_shadow_model(potion_model, training_source, "potion_tempo", source_quality)
        models["potion_tempo"] = _shadow_stats_model_summary(
            potion_model.path,
            len(potion_load.examples),
            potion_model.metadata,
            potion_model.feature_weights,
        )
        trained_model_count += 1
        accepted_rows += len(potion_load.examples)
    else:
        models["potion_tempo"] = _skipped_shadow_model_summary(model_paths["potion_tempo"], potion_load.stats)

    if deck_load.examples:
        deck_model = train_deck_model(
            deck_load.examples,
            model_paths["pre_boss_deck_quality"],
            min_count=min_count,
            max_weight=max_weight,
            load_quality=deck_load.stats,
        )
        _finish_shadow_model(deck_model, training_source, "pre_boss_deck_quality", source_quality)
        models["pre_boss_deck_quality"] = _shadow_stats_model_summary(
            deck_model.path,
            len(deck_load.examples),
            deck_model.metadata,
            deck_model.feature_weights,
        )
        trained_model_count += 1
        accepted_rows += len(deck_load.examples)
    else:
        models["pre_boss_deck_quality"] = _skipped_shadow_model_summary(
            model_paths["pre_boss_deck_quality"],
            deck_load.stats,
        )

    if combat_load.examples:
        combat_model = train_combat_model(
            combat_load.examples,
            model_paths["combat_search"],
            min_count=min_count,
            max_weight=max_weight,
            load_quality=combat_load.stats,
        )
        _finish_shadow_model(combat_model, training_source, "combat_search", source_quality)
        models["combat_search"] = _combat_shadow_model_summary(
            combat_model.path,
            len(combat_load.examples),
            combat_model.metadata,
            combat_model.card_priors,
            combat_model.context_card_scores,
        )
        trained_model_count += 1
        accepted_rows += len(combat_load.examples)
    else:
        models["combat_search"] = _skipped_shadow_model_summary(model_paths["combat_search"], combat_load.stats)

    summary: dict[str, Any] = {
        "version": 1,
        "shadow_inputs": [str(shadow_dir)],
        "shadow_training_source": {
            **training_source,
            "source_quality_policy": source_quality,
        },
        "source_quality": source_quality,
        "min_count": min_count,
        "max_weight": max_weight,
        "accepted_rows": accepted_rows,
        "trained_model_count": trained_model_count,
        "models": models,
        "load_quality": {category: load.stats for category, load in loads.items()},
    }
    if trained_model_count > 0 and advice_output_dir is not None:
        shadow_models = ShadowModels.load(
            route_model_path=model_paths["route_risk"],
            potion_model_path=model_paths["potion_tempo"],
            deck_model_path=model_paths["pre_boss_deck_quality"],
            combat_model_path=model_paths["combat_search"],
        )
        advice = score_shadow_inputs([shadow_dir], models=shadow_models)
        summary["shadow_advice"] = {
            **write_advice(
                advice_output_dir,
                advice,
                shadow_inputs={**training_source, "source_quality_policy": source_quality},
            ),
            "path": str(advice_output_dir),
        }
    return summary


def _finish_shadow_model(model: Any, training_source: dict[str, Any], category: str, source_quality: str) -> None:
    model.metadata["training_source"] = category_training_source(
        training_source,
        category,
        source_quality=source_quality,
    )
    model.metadata["training_source_quality"] = source_quality
    model.save()


def _shadow_stats_model_summary(
    path: Path,
    examples: int,
    metadata: dict[str, Any],
    weights: dict[str, float],
) -> dict[str, Any]:
    return {
        "status": "trained",
        "path": str(path),
        "examples": examples,
        "feature_weights": len(weights),
        "training_source_quality": metadata.get("training_source_quality"),
        "training_source": metadata.get("training_source") or {},
        "load_quality": metadata.get("load_quality") or {},
    }


def _combat_shadow_model_summary(
    path: Path,
    examples: int,
    metadata: dict[str, Any],
    card_priors: dict[str, float],
    context_card_scores: dict[str, dict[str, float]],
) -> dict[str, Any]:
    return {
        "status": "trained",
        "path": str(path),
        "examples": examples,
        "card_priors": len(card_priors),
        "context_buckets": len(context_card_scores),
        "training_source_quality": metadata.get("training_source_quality"),
        "training_source": metadata.get("training_source") or {},
        "load_quality": metadata.get("load_quality") or {},
    }


def _skipped_shadow_model_summary(path: Path, load_quality: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": "no_examples_allowed_by_source_quality",
        "path": str(path),
        "examples": 0,
        "load_quality": load_quality,
    }


def _train_card_model_from_manifest(
    manifest_path: Path,
    *,
    card_model_path: Path,
    min_count: int,
    max_delta: float,
) -> dict[str, Any]:
    training_source = resolve_training_source([], manifest_path)
    examples = load_card_examples(Path(path) for path in training_source["resolved_logs"])
    if not examples:
        return {
            "status": "skipped",
            "reason": "no_card_pick_examples",
            "manifest_path": str(manifest_path),
            "resolved_logs": training_source["resolved_log_count"],
            "model_path": str(card_model_path),
        }
    model = train_stats_model(examples, card_model_path, min_count=min_count, max_delta=max_delta)
    model.metadata.update(
        {
            "training_source": training_source,
            "training_source_quality": training_source["quality_policy"],
        }
    )
    model.metadata.update(hardware_metadata("auto"))
    model.save()
    return {
        "status": "trained",
        "examples": len(examples),
        "deltas": len(model.card_deltas),
        "manifest_path": str(manifest_path),
        "resolved_logs": training_source["resolved_log_count"],
        "model_path": str(model.path),
    }


def _blend_card_model_with_external_prior(
    card_summary: dict[str, Any],
    external_summary: dict[str, Any],
    *,
    output_path: Path,
    external_scale: float = 0.25,
    max_external_delta: float = 0.75,
) -> dict[str, Any]:
    if card_summary.get("status") != "trained":
        return {
            "status": "skipped",
            "reason": "local_card_model_not_trained",
            "runtime_authority": False,
            "model_path": str(output_path),
        }
    if external_summary.get("status") != "trained":
        return {
            "status": "skipped",
            "reason": "external_prior_not_trained",
            "external_prior_status": external_summary.get("status"),
            "runtime_authority": False,
            "model_path": str(output_path),
        }
    base_path = Path(str(card_summary.get("model_path") or ""))
    external_path = Path(str(external_summary.get("model_path") or ""))
    if not base_path.exists() or not external_path.exists():
        return {
            "status": "skipped",
            "reason": "missing_model_input",
            "base_model_path": str(base_path),
            "external_prior_model_path": str(external_path),
            "runtime_authority": False,
            "model_path": str(output_path),
        }

    base_model = CardValueModel.load(base_path)
    external_model = CardValueModel.load(external_path)
    merged = dict(base_model.card_deltas)
    external_only = 0
    adjusted = 0
    for key, external_delta in external_model.card_deltas.items():
        contribution = max(-max_external_delta, min(max_external_delta, external_delta * external_scale))
        if abs(contribution) <= 1e-9:
            continue
        if key not in merged:
            external_only += 1
        else:
            adjusted += 1
        merged[key] = round(merged.get(key, 0.0) + contribution, 3)

    model = CardValueModel(
        path=output_path,
        card_deltas=merged,
        metadata={
            "version": 1,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "model_kind": "card_value_external_prior_blend",
            "base_model_path": str(base_path),
            "external_prior_model_path": str(external_path),
            "external_prior_source_quality": EXTERNAL_PRIOR_GRADE,
            "external_prior_blend_scale": external_scale,
            "external_prior_max_contribution": max_external_delta,
            "base_deltas": len(base_model.card_deltas),
            "external_prior_deltas": len(external_model.card_deltas),
            "external_only_deltas": external_only,
            "adjusted_local_deltas": adjusted,
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "does_not_control_live_mcp": True,
            "requires_audited_promotion": True,
            "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"],
        },
    )
    model.save()
    return {
        "status": "trained",
        "model_path": str(model.path),
        "deltas": len(model.card_deltas),
        "base_deltas": len(base_model.card_deltas),
        "external_prior_deltas": len(external_model.card_deltas),
        "external_only_deltas": external_only,
        "adjusted_local_deltas": adjusted,
        "external_prior_blend_scale": external_scale,
        "external_prior_max_contribution": max_external_delta,
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "does_not_control_live_mcp": True,
    }


def _train_combat_value_model_from_shadow(
    shadow_dir: Path,
    *,
    model_path: Path,
    summary_output: Path,
    source_quality: str,
) -> dict[str, Any]:
    quality_policy = "pristine" if source_quality == "pristine" else "weighted"
    if source_quality not in {"pristine", "usable", "diagnostic", "all"}:
        return {
            "status": "skipped",
            "reason": "combat_value_mlp_unsupported_source_quality",
            "source_quality": source_quality,
            "quality_policy": quality_policy,
            "model_path": str(model_path),
        }
    examples = load_combat_value_examples([shadow_dir], quality_policy=quality_policy)
    if not examples:
        reason_quality = "pristine" if quality_policy == "pristine" else "weighted"
        return {
            "status": "skipped",
            "reason": f"no_{reason_quality}_combat_value_examples",
            "source_quality": source_quality,
            "quality_policy": quality_policy,
            "shadow_dir": str(shadow_dir),
            "model_path": str(model_path),
        }
    try:
        result = train_combat_value_torch_model(
            examples,
            model_path=model_path,
            epochs=40,
            hidden_dim=32,
            batch_size=16,
            device_name="auto",
            quality_policy=quality_policy,
        )
    except (ImportError, RuntimeError, SystemExit) as exc:
        return {
            "status": "skipped",
            "reason": "combat_value_mlp_training_failed",
            "error": str(exc),
            "source_quality": source_quality,
            "quality_policy": quality_policy,
            "examples": len(examples),
            "model_path": str(model_path),
        }
    summary = dict(result.get("summary") or {})
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "status": "trained",
        "backend": summary.get("backend"),
        "examples": summary.get("examples", len(examples)),
        "validation_examples": summary.get("validation_examples", 0),
        "device": summary.get("device"),
        "train_loss": summary.get("train_loss"),
        "val_loss": summary.get("val_loss"),
        "source_quality": source_quality,
        "quality_policy": quality_policy,
        "source_quality_counts": summary.get("source_quality_counts", {}),
        "model_path": str(model_path),
        "summary_path": str(summary_output),
        "runtime_authority": False,
    }


def _train_external_priors_from_inputs(
    inputs: Iterable[Path],
    *,
    cycle_dir: Path,
    model_dir: Path | None,
    min_count: int,
    max_delta: float,
    prior_scale: float,
) -> dict[str, Any]:
    input_paths = [Path(path) for path in inputs]
    if not input_paths:
        return {
            "status": "skipped",
            "reason": "external_prior_inputs_not_configured",
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
        }
    load_result = load_external_prior_examples_with_stats(input_paths)
    source_id = str(load_result.training_source.get("source_id") or "external_prior")
    output_dir = model_dir or (cycle_dir / "training" / "external_models" / _safe_artifact_name(source_id))
    model_path = output_dir / "card_prior_model.json"
    risk = external_prior_runtime_output_risk(model_path)
    if risk:
        return {
            "status": "skipped",
            "reason": "external_prior_runtime_output_refused",
            "risk": risk,
            "model_path": str(model_path),
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
            "training_source": load_result.training_source,
            "load_quality": load_result.stats,
        }
    if not load_result.examples:
        return {
            "status": "skipped",
            "reason": "no_external_prior_examples",
            "model_path": str(model_path),
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
            "training_source": load_result.training_source,
            "load_quality": load_result.stats,
        }
    model = train_external_card_prior_model(
        load_result.examples,
        model_path,
        min_count=min_count,
        max_delta=max_delta,
        prior_scale=prior_scale,
        load_quality=load_result.stats,
    )
    model.metadata["training_source"] = load_result.training_source
    model.metadata["training_source_quality"] = EXTERNAL_PRIOR_GRADE
    model.metadata.update(hardware_metadata("auto"))
    model.save()
    summary = {
        "version": 1,
        "status": "trained",
        "source_quality": EXTERNAL_PRIOR_GRADE,
        "model_kind": "external_card_prior",
        "model_path": str(model_path),
        "examples": len(load_result.examples),
        "deltas": len(model.card_deltas),
        "min_count": min_count,
        "max_delta": max_delta,
        "prior_scale": prior_scale,
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "does_not_control_live_mcp": True,
        "training_source": load_result.training_source,
        "load_quality": load_result.stats,
        "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"],
    }
    summary_path = model_path.parent / "external_prior_training_summary.json"
    _write_json(summary_path, summary)
    return {**summary, "summary_path": str(summary_path)}


def _train_external_structure_priors_from_inputs(
    inputs: Iterable[Path],
    *,
    cycle_dir: Path,
    model_dir: Path | None,
) -> dict[str, Any]:
    input_paths = [Path(path) for path in inputs]
    if not input_paths:
        return {
            "status": "skipped",
            "reason": "external_prior_inputs_not_configured",
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
        }
    output_model_dir = model_dir or (cycle_dir / "training" / "external_structure_models")
    rows_dir = cycle_dir / "training" / "external_structure_rows"
    summary = train_external_structure_priors(
        input_paths,
        rows_dir=rows_dir,
        model_dir=output_model_dir,
        backend="auto",
    )
    return {
        **summary,
        "source_quality": EXTERNAL_PRIOR_GRADE,
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "does_not_control_live_mcp": True,
    }


def _train_external_structure_decision_model(
    structure_summary: dict[str, Any],
    *,
    cycle_dir: Path,
) -> dict[str, Any]:
    if structure_summary.get("status") != "trained":
        return {
            "status": "skipped",
            "reason": "external_structure_priors_not_trained",
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
    rows = structure_summary.get("rows") if isinstance(structure_summary.get("rows"), dict) else {}
    row_paths = [Path(str(path)) for path in rows.values() if path]
    existing_rows = [path for path in row_paths if path.exists()]
    if not existing_rows:
        return {
            "status": "skipped",
            "reason": "external_structure_rows_missing",
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
            "structure_summary_path": structure_summary.get("summary_path"),
        }
    output_dir = cycle_dir / "training" / "external_structure_decision_model"
    model_path = output_dir / "decision_multitask_model.pt"
    summary_output = output_dir / "decision_multitask_training_summary.json"
    prediction_output = output_dir / "decision_multitask_predictions.jsonl"
    try:
        result = train_decision_multitask_model(
            existing_rows,
            model_path=model_path,
            summary_output=summary_output,
            prediction_output=prediction_output,
            epochs=20,
            hidden_dim=48,
            batch_size=128,
            device_name="auto",
        )
    except (ImportError, RuntimeError, SystemExit, ValueError) as exc:
        return {
            "status": "skipped",
            "reason": "external_structure_decision_training_failed",
            "error": str(exc),
            "source_quality": EXTERNAL_PRIOR_GRADE,
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
            "structure_summary_path": structure_summary.get("summary_path"),
        }
    trained = result["summary"]
    return {
        "status": "trained",
        "source_quality": EXTERNAL_PRIOR_GRADE,
        "model_kind": trained.get("model_kind"),
        "model_path": str(model_path),
        "summary_path": str(summary_output),
        "prediction_path": str(prediction_output),
        "examples": trained.get("examples", 0),
        "task_counts": trained.get("task_counts", {}),
        "row_counts": trained.get("row_counts", {}),
        "train_loss": trained.get("train_loss"),
        "val_loss": trained.get("val_loss"),
        "evaluation": trained.get("evaluation", {}),
        "prediction_audit": trained.get("prediction_audit", {}),
        "promotion_readiness": trained.get("promotion_readiness", {}),
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "runtime_authority_level": "shadow",
        "does_not_control_live_mcp": True,
        "direct_mcp_control": False,
        "requires_audited_promotion": True,
        "forbidden_uses": trained.get("forbidden_uses", []),
        "training_source": trained.get("training_source", {}),
        "structure_summary_path": structure_summary.get("summary_path"),
    }

def _evaluate_external_decision_live_shadow(
    decision_summary: dict[str, Any],
    *,
    manifest_path: Path,
    cycle_dir: Path,
) -> dict[str, Any]:
    if decision_summary.get("status") != "trained":
        return {
            "status": "skipped",
            "reason": "external_structure_decision_model_not_trained",
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
    model_path_text = str(decision_summary.get("model_path") or "")
    model_path = Path(model_path_text) if model_path_text else None
    if model_path is None or not model_path.exists():
        return {
            "status": "skipped",
            "reason": "external_structure_decision_model_missing",
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
    training_source = resolve_training_source([], manifest_path)
    logs = [Path(path) for path in training_source.get("resolved_logs", [])]
    existing_logs = [path for path in logs if path.exists()]
    if not existing_logs:
        return {
            "status": "skipped",
            "reason": "no_replay_logs_for_live_shadow_disagreement",
            "manifest_path": str(manifest_path),
            "resolved_logs": training_source.get("resolved_log_count", 0),
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
    output_dir = cycle_dir / "training" / "external_structure_decision_model"
    try:
        summary = evaluate_live_shadow_disagreements(
            existing_logs,
            model_path=model_path,
            output_path=output_dir / "live_shadow_disagreements.jsonl",
            summary_output=output_dir / "live_shadow_disagreement_summary.json",
        )
    except (ImportError, RuntimeError, OSError, ValueError) as exc:
        return {
            "status": "skipped",
            "reason": "live_shadow_disagreement_failed",
            "error": str(exc),
            "manifest_path": str(manifest_path),
            "resolved_logs": len(existing_logs),
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
    summary["manifest_path"] = str(manifest_path)
    summary["resolved_logs"] = len(existing_logs)
    return summary


def _write_external_decision_training_curriculum(
    decision_summary: dict[str, Any],
    *,
    cycle_dir: Path,
) -> dict[str, Any]:
    if decision_summary.get("status") != "trained":
        return {
            "status": "skipped",
            "reason": "external_structure_decision_model_not_trained",
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
    output_path = cycle_dir / "training" / "external_structure_decision_model" / "decision_training_curriculum.json"
    try:
        return write_decision_training_curriculum(decision_summary, output_path=output_path)
    except (OSError, ValueError, TypeError) as exc:
        return {
            "status": "skipped",
            "reason": "decision_training_curriculum_failed",
            "error": str(exc),
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "runtime_authority_level": "shadow",
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
        }
def _replay_learned_memory(manifest_path: Path, *, learned_path: Path, reset: bool) -> dict[str, Any]:
    training_source = resolve_training_source([], manifest_path)
    logs = [Path(path) for path in training_source["resolved_logs"]]
    if not logs:
        return {
            "status": "skipped",
            "reason": "no_clean_trainable_logs",
            "manifest_path": str(manifest_path),
            "learned_path": str(learned_path),
        }
    memory = StrategyMemory.load(learned_path=learned_path)
    if reset:
        memory.learned = {
            "version": 1,
            "runs": {"victories": 0, "deaths": 0, "total": 0},
            "card_picks": {},
            "recent_outcomes": [],
            "learning_replays": [],
        }
    applied = 0
    skipped = 0
    for item in (read_log(path) for path in logs):
        for pick in item.picks:
            memory.record_card_pick(pick)
        if item.victory is None:
            skipped += 1
            continue
        memory.record_outcome_summary(
            victory=item.victory,
            floor=item.floor,
            score=item.score,
            character=item.character,
            ascension=item.ascension,
            episode_picks=item.picks,
        )
        applied += 1
    record_learning_replay(memory, training_source, applied=applied, skipped=skipped)
    memory.save()
    return {
        "status": "updated",
        "manifest_path": str(manifest_path),
        "read_logs": len(logs),
        "applied_completed_runs": applied,
        "skipped_incomplete_runs": skipped,
        "learned_path": str(memory.learned_path),
        "reset": reset,
    }


def _accepted_shadow_row_count(shadow_dir: Path, *, source_quality: str) -> int:
    total = 0
    for file_name in SHADOW_ROW_FILES:
        path = shadow_dir / file_name
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict) and source_quality_allowed(row, source_quality):
                    total += 1
    return total


def _shadow_model_paths(model_dir: Path | None) -> dict[str, Path]:
    if model_dir is None:
        return {
            "route_risk": ROUTE_RISK_MODEL_PATH,
            "potion_tempo": POTION_TEMPO_MODEL_PATH,
            "pre_boss_deck_quality": DECK_QUALITY_MODEL_PATH,
            "combat_search": COMBAT_SEARCH_MODEL_PATH,
        }
    return {
        "route_risk": model_dir / ROUTE_RISK_MODEL_PATH.name,
        "potion_tempo": model_dir / POTION_TEMPO_MODEL_PATH.name,
        "pre_boss_deck_quality": model_dir / DECK_QUALITY_MODEL_PATH.name,
        "combat_search": model_dir / COMBAT_SEARCH_MODEL_PATH.name,
    }


def _replay_inputs(logs: Iterable[Path] | None, *, include_live_dir: Path | None) -> list[Path]:
    inputs = [Path(path) for path in (logs or [])]
    if include_live_dir is not None:
        inputs.append(include_live_dir)
    if not inputs:
        inputs.append(ROOT / "runs" / "ai_runs")
    return inputs


def _compact_offline_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    keys = (
        "summary_path",
        "manifest_path",
        "shadow_dir",
        "advice_dir",
        "diagnosis_path",
        "gate_path",
        "artifact_manifest_path",
        "offline_batch_next_line",
        "diagnosis_status_line",
        "gate_status_line",
        "gate_passed",
        "gate_next_action",
        "next_probe_goal",
        "recommended_next_action",
    )
    compact = {key: summary.get(key) for key in keys if summary.get(key) is not None}
    compact["manifest_summary"] = summary.get("manifest_summary") or {}
    compact["shadow_advice"] = summary.get("shadow_advice") or {}
    return compact


def _write_run_reviews(
    offline_summary: dict[str, Any],
    *,
    training_summary: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    manifest_path = Path(str(offline_summary["manifest_path"]))
    if not manifest_path.exists():
        return {"status": "skipped", "reason": "manifest_missing", "output_dir": str(output_dir)}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)
    reviews: list[dict[str, Any]] = []
    for category in ("clean_trainable", "diagnostic_excluded", "infra_blocked"):
        for item in (manifest.get("categories") or {}).get(category, []) or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            log_path = Path(str(item["path"]))
            if not log_path.exists():
                continue
            records = _read_jsonl_records(log_path)
            review_path = output_dir / f"{log_path.stem}.md"
            review_path.write_text(
                _run_review_markdown_v2(
                    log_path,
                    item,
                    category=category,
                    records=records,
                    offline_summary=offline_summary,
                    training_summary=training_summary,
                ),
                encoding="utf-8",
            )
            reviews.append(
                {
                    "path": str(review_path),
                    "source_log": str(log_path),
                    "category": category,
                    "reason": item.get("reason"),
                }
            )
    return {
        "status": "written" if reviews else "skipped",
        "reason": None if reviews else "no_reviewable_logs",
        "output_dir": str(output_dir),
        "count": len(reviews),
        "reviews": reviews,
    }


def _run_review_markdown(
    log_path: Path,
    item: dict[str, Any],
    *,
    category: str,
    records: list[dict[str, Any]],
    offline_summary: dict[str, Any],
    training_summary: dict[str, Any],
) -> str:
    title = f"# 单局复盘：{log_path.stem}"
    notable = _notable_decisions(records)
    card_picks = _card_picks(records)
    search_count = sum(1 for row in notable if row.get("kind") in {"search", "direct_lethal"})
    route_count = sum(1 for row in notable if row.get("kind") == "route")
    boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    action_recovery = item.get("action_recovery_summary") if isinstance(item.get("action_recovery_summary"), dict) else {}
    training = _review_training_impact(item, category, training_summary)
    next_action = offline_summary.get("recommended_next_action") or offline_summary.get("gate_next_action")
    lines = [
        title,
        "",
        "## 本局概况",
        "",
        f"- 源日志：`{log_path}`",
        f"- 分类：`{category}` / `{item.get('reason')}`",
        f"- 角色/进阶：`{item.get('character')}` A{item.get('ascension')}",
        f"- 最终楼层：`{item.get('floor')}`；胜利：`{item.get('victory')}`",
        f"- 失败归因：`{item.get('failure_attribution')}`",
        f"- 验证等级：`{item.get('validation_grade')}` flags={item.get('validation_flags') or []}",
        f"- 动作恢复：total={action_recovery.get('total', item.get('recovered_actions', 0))} "
        f"recovered={action_recovery.get('recovered', item.get('recovered_actions', 0))} "
        f"unrecovered={action_recovery.get('unrecovered', 0)}",
        f"- 第一幕 Boss：reached={boss.get('reached')} cleared={boss.get('cleared')} "
        f"prefix_pristine={boss.get('prefix_pristine_clear')} blockers={boss.get('prefix_blockers') or []}",
        "",
        "## 启发式与搜索的影响",
        "",
        "- 本局 live 动作由现有启发式策略、执行保护和 one-turn combat search 控制。",
        f"- 扫描到的关键启发式/搜索决策：search/direct-lethal={search_count}，route={route_count}。",
        "- 这些是控制器决策，不代表学习模型直接选择了动作。",
    ]
    for row in notable[:18]:
        lines.append(f"- Step {row['step']} F{row.get('floor')}：{row['text']}")
    if not notable:
        lines.append("- 简要扫描没有发现可归类的关键决策文本。")
    lines.extend(
        [
            "",
            "## 学习与模型的影响",
            "",
            "- 当前学习主要是赛后复盘和建议；除非 `StrategyMemory.card_score()` 已加载卡牌 delta，否则不会直接接管动作。",
            "- shadow 模型产出 advice 和训练信号，但不控制整局。",
        ]
    )
    if card_picks:
        lines.append("- 本局记录到的卡牌奖励选择，可用于卡牌价值学习：")
        for pick in card_picks[:12]:
            lines.append(f"  - Step {pick['step']} F{pick.get('floor')}：`{pick['pick']}`")
    else:
        lines.append("- 本局没有记录到可用于卡牌价值学习的卡牌奖励选择。")
    shadow = training_summary.get("shadow_models") if isinstance(training_summary.get("shadow_models"), dict) else {}
    card_model = training_summary.get("card_model") if isinstance(training_summary.get("card_model"), dict) else {}
    combat_value = (
        training_summary.get("combat_value_model")
        if isinstance(training_summary.get("combat_value_model"), dict)
        else {}
    )
    lines.extend(
        [
            f"- 本轮 shadow 训练状态：`{shadow.get('status')}` rows={shadow.get('accepted_rows', 0)}.",
            f"- 本轮卡牌模型训练状态：`{card_model.get('status')}` examples={card_model.get('examples', 0)}.",
            f"- 本轮神经网络 combat value shadow 状态：`{combat_value.get('status')}` "
            f"examples={combat_value.get('examples', 0)}；runtime_authority={combat_value.get('runtime_authority', False)}.",
            "",
            "## 训练影响",
            "",
        ]
    )
    lines.extend(f"- {line}" for line in training)
    lines.extend(
        [
            "",
            "## 对后续爬塔的影响",
            "",
            f"- 批次建议：`{next_action}`。",
            "- 不因为单局异常直接新增启发式例外；需要先看 manifest、diagnosis、gate 和重复证据。",
            "- 如果本局是 infra-blocked 或 diagnostic，保留为执行层证据，并继续收集干净终局样本。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _review_training_impact(
    item: dict[str, Any],
    category: str,
    training_summary: dict[str, Any],
) -> list[str]:
    grade = str(item.get("validation_grade") or "unknown")
    if category == "clean_trainable":
        lines = [
            "`clean_trainable`：可进入 `learn` 和 `train_card_model --manifest`。",
            f"shadow rows 的 source-quality 是 `{grade}`；默认 shadow 训练只接收 `pristine`。",
        ]
        if grade != "pristine":
            lines.append("本局可作为复盘证据，但不应进入默认 pristine shadow 训练。")
        return lines
    if category == "diagnostic_excluded":
        return [
            "`diagnostic_excluded`：排除在默认学习之外。",
            "它用于证据、监控或续跑判断，不用于更新模型。",
        ]
    return [
        "`infra_blocked`：排除在默认学习之外。",
        "主要影响是执行层修复，而不是策略或模型晋升。",
    ]


def _run_review_markdown_v2(
    log_path: Path,
    item: dict[str, Any],
    *,
    category: str,
    records: list[dict[str, Any]],
    offline_summary: dict[str, Any],
    training_summary: dict[str, Any],
) -> str:
    title = f"# 单局复盘：{log_path.stem}"
    notable = _notable_decisions(records)
    card_picks = _card_picks(records)
    authority_events = _model_authority_events(records)
    route_assist_events = _route_model_assist_events(records)
    runtime_authority = [event for event in authority_events if event.get("runtime_authority")]
    search_count = sum(1 for row in notable if row.get("kind") in {"search", "direct_lethal"})
    route_count = sum(1 for row in notable if row.get("kind") == "route")
    boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    action_recovery = item.get("action_recovery_summary") if isinstance(item.get("action_recovery_summary"), dict) else {}
    training = _review_training_impact_cn(item, category, training_summary)
    next_action = offline_summary.get("recommended_next_action") or offline_summary.get("gate_next_action")
    authority_levels = sorted(
        {str(event.get("level")) for event in authority_events if event.get("level")}
        | {str(event.get("level")) for event in route_assist_events if event.get("level")}
    )

    lines = [
        title,
        "",
        "## 本局概况",
        "",
        f"- 源日志：`{log_path}`",
        f"- 分类：`{category}` / `{item.get('reason')}`",
        f"- 角色/进阶：`{item.get('character')}` A{item.get('ascension')}",
        f"- 最终楼层：`{item.get('floor')}`；胜利：`{item.get('victory')}`",
        f"- 失败归因：`{item.get('failure_attribution')}`",
        f"- 验证等级：`{item.get('validation_grade')}` flags={item.get('validation_flags') or []}",
        f"- 动作恢复：total={action_recovery.get('total', item.get('recovered_actions', 0))} "
        f"recovered={action_recovery.get('recovered', item.get('recovered_actions', 0))} "
        f"unrecovered={action_recovery.get('unrecovered', 0)}",
        f"- 第一幕 Boss：reached={boss.get('reached')} cleared={boss.get('cleared')} "
        f"prefix_pristine={boss.get('prefix_pristine_clear')} blockers={boss.get('prefix_blockers') or []}",
        "",
        "## 启发式与搜索的影响",
        "",
        "- 本局 live 控制仍由现有策略、执行保护和 one-turn combat search 驱动。",
        f"- 关键启发式/搜索决策计数：search/direct-lethal={search_count}，route={route_count}。",
        "- 这些记录代表控制器或搜索器的实时选择，不等同于训练后模型的直接产出。",
    ]
    for row in notable[:18]:
        lines.append(f"- Step {row['step']} F{row.get('floor')}：{row['text']}")
    if not notable:
        lines.append("- 简要扫描没有发现可归类的关键决策文本。")

    lines.extend(
        [
            "",
            "## 学习与模型的影响",
            "",
            f"- 本局记录到的模型权限档位：`{', '.join(authority_levels) if authority_levels else 'none'}`。",
        ]
    )
    if runtime_authority:
        lines.append("- 下列行为由模型/学习信号实际参与接管或改选：")
        for event in runtime_authority[:12]:
            lines.append(
                f"- Step {event['step']} F{event.get('floor')} {event.get('surface')}："
                f"`{event.get('level')}` 通过 `{event.get('selection_source')}` 选择 "
                f"`{event.get('selected_card')}`；启发式第一候选 `{event.get('heuristic_best_card')}`，"
                f"模型信号第一候选 `{event.get('model_best_card')}`。"
            )
    if route_assist_events:
        lines.append("- 下列路线选择受到 route-risk assist 评分影响；它会改变路线分数，但不直接控制 live MCP 点击：")
        for event in route_assist_events[:12]:
            lines.append(
                f"- Step {event['step']} F{event.get('floor')} choice={event.get('selected_choice_index')} "
                f"`{event.get('selected_symbol')}`：selected_adjustment={event.get('selected_model_adjustment')}，"
                f"highest_risk_choice={event.get('highest_risk_choice_index')} "
                f"risk={event.get('highest_risk_score')} penalty={event.get('highest_risk_penalty')}，"
                f"authority={event.get('level')}，direct_mcp_control={event.get('direct_mcp_control')}。"
            )
    if not runtime_authority and authority_events:
        if route_assist_events:
            lines.append("- 本局没有发生 `runtime_authority=true` 的卡牌改选；route-risk assist 的评分影响已在上方单列。")
        else:
            lines.append("- 本局有模型权限元数据，但没有发生 runtime_authority=true 的接管；模型仍处于影子/辅助观察。")
    elif not authority_events and not route_assist_events:
        lines.append("- 本局没有记录 model_authority 元数据；这通常表示旧日志，或本轮未开放模型执行权限。")


    if card_picks:
        lines.append("- 本局记录到的卡牌奖励选择，可用于卡牌价值学习：")
        for pick in card_picks[:12]:
            lines.append(f"- Step {pick['step']} F{pick.get('floor')}：`{pick['pick']}`")
    else:
        lines.append("- 本局没有记录到可用于卡牌价值学习的卡牌奖励选择。")

    shadow = training_summary.get("shadow_models") if isinstance(training_summary.get("shadow_models"), dict) else {}
    card_model = training_summary.get("card_model") if isinstance(training_summary.get("card_model"), dict) else {}
    combat_value = (
        training_summary.get("combat_value_model")
        if isinstance(training_summary.get("combat_value_model"), dict)
        else {}
    )
    external_priors = (
        training_summary.get("external_priors")
        if isinstance(training_summary.get("external_priors"), dict)
        else {}
    )
    external_decision = (
        training_summary.get("external_structure_decision_model")
        if isinstance(training_summary.get("external_structure_decision_model"), dict)
        else {}
    )
    external_blend = (
        training_summary.get("card_external_prior_blend")
        if isinstance(training_summary.get("card_external_prior_blend"), dict)
        else {}
    )
    lines.extend(
        [
            f"- 本轮 shadow 训练状态：`{shadow.get('status')}` rows={shadow.get('accepted_rows', 0)}。",
            f"- 本轮卡牌模型训练状态：`{card_model.get('status')}` examples={card_model.get('examples', 0)}。",
            f"- 本轮神经网络 combat value shadow 状态：`{combat_value.get('status')}` "
            f"examples={combat_value.get('examples', 0)}；runtime_authority={combat_value.get('runtime_authority', False)}。",
            f"- 本轮外部数据 prior 状态：`{external_priors.get('status')}` "
            f"examples={external_priors.get('examples', 0)}；runtime_authority={external_priors.get('runtime_authority', False)}。",
            f"- External structure decision shadow: `{external_decision.get('status')}` "
            f"examples={external_decision.get('examples', 0)}; runtime_authority={external_decision.get('runtime_authority', False)}; "
            f"direct_mcp_control={external_decision.get('direct_mcp_control', False)}.",
            f"- External decision live shadow disagreement: `{(external_decision.get('live_shadow_disagreement') or {}).get('status')}` "
            f"examples={(external_decision.get('live_shadow_disagreement') or {}).get('examples', 0)}; "
            f"task_counts={(external_decision.get('live_shadow_disagreement') or {}).get('task_counts', {})}; "
            f"purge_examples={(((external_decision.get('live_shadow_disagreement') or {}).get('supported_surfaces') or {}).get('purge_remove') or {}).get('examples', 0)}; "
            f"disagreements={(external_decision.get('live_shadow_disagreement') or {}).get('disagreements', 0)}; "
            f"actual_counts={(external_decision.get('live_shadow_disagreement') or {}).get('actual_counts', {})}; "
            f"can_promote_to_assist={((external_decision.get('live_shadow_disagreement') or {}).get('promotion_readiness') or {}).get('can_promote_to_assist', False)}.",
            f"- External decision training curriculum: `{(external_decision.get('training_curriculum') or {}).get('status')}` "
            f"active_targets={(external_decision.get('training_curriculum') or {}).get('active_target_count', 0)}; "
            f"output={(external_decision.get('training_curriculum') or {}).get('output_path') or None}.",
            f"- 本轮外部融合候选状态：`{external_blend.get('status')}` "
            f"deltas={external_blend.get('deltas', 0)}；runtime_authority={external_blend.get('runtime_authority', False)}。",
            f"- External decision promotion readiness: `{(external_decision.get('promotion_readiness') or {}).get('status')}`; "
            f"can_promote_to_assist={(external_decision.get('promotion_readiness') or {}).get('can_promote_to_assist', False)}.",
            "",
            "## 训练影响",
            "",
        ]
    )
    lines.extend(f"- {line}" for line in training)
    lines.extend(
        [
            "",
            "## 对后续爬塔的影响",
            "",
            f"- 批次建议：`{next_action}`。",
            "- 不因为单局异常直接新增启发式例外；先看 manifest、diagnosis、gate 和重复证据。",
            "- 如果本局是 infra-blocked 或 diagnostic，保留为执行层证据，不进入默认训练。",
            "- 如果模型接管造成错误选择，下一轮先降回 `shadow` 或收紧接管阈值，再继续收集 A0 终局证据。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _review_training_impact_cn(
    item: dict[str, Any],
    category: str,
    training_summary: dict[str, Any],
) -> list[str]:
    grade = str(item.get("validation_grade") or "unknown")
    if category == "clean_trainable":
        lines = [
            "`clean_trainable`：可进入 `learn` 和 `train_card_model --manifest`。",
            f"本局验证等级为 `{grade}`；默认 shadow 训练只接收 `pristine` 证据。",
        ]
        if grade != "pristine":
            lines.append("本局可用于复盘和卡牌学习，但不应进入默认 pristine shadow 训练。")
        return lines
    if category == "diagnostic_excluded":
        return [
            "`diagnostic_excluded`：排除在默认学习之外。",
            "它用于诊断、监控或继续收集证据，不用于刷新模型。",
        ]
    return [
        "`infra_blocked`：排除在默认学习之外。",
        "主要影响是修执行层或 live MCP，而不是升级策略或模型。",
    ]


def _model_authority_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        authority = metadata.get("model_authority") if isinstance(metadata.get("model_authority"), dict) else None
        if authority is None:
            continue
        options = authority.get("options") if isinstance(authority.get("options"), list) else []
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        selected_index = authority.get("selected_choice_index")
        selected = _model_authority_option(options, selected_index)
        heuristic_best = _best_model_authority_option(options, "heuristic_total_score")
        model_best = max(
            (option for option in options if isinstance(option, dict)),
            key=lambda option: _safe_float(option.get("model_signal")),
            default=None,
        )
        events.append(
            {
                "step": record.get("step"),
                "floor": state.get("floor"),
                "surface": authority.get("surface"),
                "level": authority.get("level"),
                "selection_source": authority.get("selection_source"),
                "selected_choice_index": selected_index,
                "selected_card": _option_card_name(selected),
                "heuristic_best_card": _option_card_name(heuristic_best),
                "model_best_card": _option_card_name(model_best),
                "runtime_authority": bool(authority.get("runtime_authority")),
            }
        )
    return events


def _route_model_assist_events(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        route_eval = state.get("route_evaluation") if isinstance(state.get("route_evaluation"), dict) else {}
        options = route_eval.get("options") if isinstance(route_eval.get("options"), list) else []
        assisted = [
            option
            for option in options
            if isinstance(option, dict)
            and isinstance(option.get("model_assist"), dict)
            and option["model_assist"].get("status") == "scored"
        ]
        if not assisted:
            continue
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
        selected_index = next(
            (
                action.get("choice_index")
                for action in actions
                if isinstance(action, dict) and action.get("action") == "choose"
            ),
            None,
        )
        selected = _model_authority_option(options, selected_index) or {}
        highest_risk = max(
            assisted,
            key=lambda option: _safe_float((option.get("model_assist") or {}).get("risk_score")),
            default={},
        )
        selected_assist = selected.get("model_assist") if isinstance(selected.get("model_assist"), dict) else {}
        highest_assist = (
            highest_risk.get("model_assist") if isinstance(highest_risk.get("model_assist"), dict) else {}
        )
        events.append(
            {
                "step": record.get("step"),
                "floor": state.get("floor"),
                "level": selected_assist.get("runtime_authority_level")
                or highest_assist.get("runtime_authority_level")
                or _legacy_route_authority_level(selected_assist)
                or _legacy_route_authority_level(highest_assist),
                "selected_choice_index": selected_index,
                "selected_symbol": selected.get("symbol"),
                "selected_model_adjustment": selected.get("model_adjustment"),
                "highest_risk_choice_index": highest_risk.get("choice_index"),
                "highest_risk_score": highest_assist.get("risk_score"),
                "highest_risk_penalty": highest_assist.get("penalty"),
                "direct_mcp_control": bool(
                    selected_assist.get("direct_mcp_control") or highest_assist.get("direct_mcp_control")
                ),
            }
        )
    return events


def _legacy_route_authority_level(model_assist: dict[str, Any]) -> str | None:
    authority = model_assist.get("runtime_authority")
    if isinstance(authority, str) and authority in {"assist", "pilot"}:
        return authority
    return None


def _model_authority_option(options: list[Any], selected_index: Any) -> dict[str, Any] | None:
    for option in options:
        if isinstance(option, dict) and option.get("choice_index") == selected_index:
            return option
    return None


def _best_model_authority_option(options: list[Any], field: str) -> dict[str, Any] | None:
    return max(
        (option for option in options if isinstance(option, dict)),
        key=lambda option: _safe_float(option.get(field)),
        default=None,
    )


def _option_card_name(option: dict[str, Any] | None) -> str | None:
    if option is None:
        return None
    return str(option.get("card") or option.get("name") or option.get("choice_index"))


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _notable_decisions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        reason = str(decision.get("reason") or "")
        if not reason:
            continue
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        kind = _decision_kind(reason, state)
        if not kind:
            continue
        result.append(
            {
                "step": record.get("step"),
                "floor": state.get("floor"),
                "kind": kind,
                "text": reason,
            }
        )
    return result


def _decision_kind(reason: str, state: dict[str, Any]) -> str | None:
    lower = reason.lower()
    if "direct lethal" in lower:
        return "direct_lethal"
    if "search" in lower or "one-turn" in lower:
        return "search"
    if lower.startswith("route") or state.get("screen_type") == "MAP":
        return "route"
    if "shop" in lower:
        return "shop"
    if "rest" in lower or "smith" in lower:
        return "rest"
    if "potion" in lower or "use " in lower and "药水" in reason:
        return "potion"
    if "pick " in lower or state.get("screen_type") == "CARD_REWARD":
        return "card_reward"
    if "boss relic" in lower:
        return "boss_reward"
    return None


def _card_picks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    for record in records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        pick = decision.get("learn_card_pick")
        if not pick:
            continue
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        picks.append({"step": record.get("step"), "floor": state.get("floor"), "pick": str(pick)})
    return picks


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
    return records


def _cycle_status_line(summary: dict[str, Any]) -> str:
    offline = summary.get("offline_batch") or {}
    manifest_summary = offline.get("manifest_summary") if isinstance(offline, dict) else {}
    training = summary.get("training") if isinstance(summary.get("training"), dict) else {}
    parts = [
        "climb_cycle:",
        f"name={summary.get('name')}",
        f"live={'yes' if (summary.get('live') or {}).get('ran') else 'no'}",
        f"logs={manifest_summary.get('resolved_log_count', 0)}",
        f"clean={manifest_summary.get('clean_trainable', 0)}",
        f"diagnostic={manifest_summary.get('diagnostic_excluded', 0)}",
        f"infra={manifest_summary.get('infra_blocked', 0)}",
    ]
    if offline.get("gate_next_action"):
        parts.append(f"gate_next={offline.get('gate_next_action')}")
    if training.get("shadow_models"):
        parts.append(f"shadow={training['shadow_models'].get('status')}")
    if training.get("card_model"):
        parts.append(f"card={training['card_model'].get('status')}")
    if training.get("combat_value_model"):
        parts.append(f"combat_value={training['combat_value_model'].get('status')}")
    if training.get("external_structure_decision_model"):
        parts.append(f"external_decision={training['external_structure_decision_model'].get('status')}")
    if training.get("card_external_prior_blend"):
        parts.append(f"external_blend={training['card_external_prior_blend'].get('status')}")
    return " ".join(parts)


def _training_status_line(summary: dict[str, Any]) -> str:
    shadow = summary.get("shadow_models") if isinstance(summary.get("shadow_models"), dict) else {}
    card = summary.get("card_model") if isinstance(summary.get("card_model"), dict) else {}
    memory = summary.get("learned_memory") if isinstance(summary.get("learned_memory"), dict) else {}
    combat_value = summary.get("combat_value_model") if isinstance(summary.get("combat_value_model"), dict) else {}
    external = summary.get("external_priors") if isinstance(summary.get("external_priors"), dict) else {}
    external_structure = (
        summary.get("external_structure_priors")
        if isinstance(summary.get("external_structure_priors"), dict)
        else {}
    )
    external_decision = (
        summary.get("external_structure_decision_model")
        if isinstance(summary.get("external_structure_decision_model"), dict)
        else {}
    )
    external_blend = (
        summary.get("card_external_prior_blend")
        if isinstance(summary.get("card_external_prior_blend"), dict)
        else {}
    )
    return (
        "climb_training: "
        f"source_quality={summary.get('source_quality')} "
        f"shadow={shadow.get('status')} rows={shadow.get('accepted_rows', 0)} "
        f"card={card.get('status')} examples={card.get('examples', 0)} "
        f"combat_value={combat_value.get('status')} examples={combat_value.get('examples', 0)} "
        f"memory={memory.get('status')} applied={memory.get('applied_completed_runs', 0)} "
        f"external_prior={external.get('status')} examples={external.get('examples', 0)} "
        f"external_structure={external_structure.get('status')} rows="
        f"{external_structure.get('reward_decision_rows', 0) + external_structure.get('purge_rows', 0) + external_structure.get('deck_cycle_rows', 0)} "
        f"external_decision={external_decision.get('status')} examples={external_decision.get('examples', 0)} "
        f"external_blend={external_blend.get('status')}"
    )


def _safe_artifact_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(name).strip())
    return cleaned or "climb_cycle"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
