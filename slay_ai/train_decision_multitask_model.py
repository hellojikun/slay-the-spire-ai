"""Train a shadow multitask decision model from external structure rows.

The checkpoint produced here is deliberately shadow-only. It learns from external
run-history rows, but it is not allowed to control live MCP/runtime decisions.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .import_external_runs import EXTERNAL_PRIOR_GRADE, EXTERNAL_RUN_HISTORY
from .model import DECISION_MULTITASK_MODEL_PATH, combat_card_key
from .train_card_model import hardware_metadata
from .train_combat_value_model import (
    _mean,
    _numeric,
    _select_device,
    _seed_everything,
    _std,
    _torch,
    _train_val_split,
    _weighted_loss,
)
from .train_external_structure_priors import (
    CARD_PURGE_PRIORS_FILE,
    CARD_REWARD_DECISIONS_FILE,
    DECK_CYCLE_PRIORS_FILE,
    FORBIDDEN_USES,
    _base_card_name,
    _outcome_reward,
    _safe_artifact_name,
)


TASK_TAKE_SKIP = "take_skip"
TASK_PURGE_REMOVE = "purge_remove"
TASK_DECK_CYCLE = "deck_cycle_quality"
TASKS = [TASK_TAKE_SKIP, TASK_PURGE_REMOVE, TASK_DECK_CYCLE]
TASK_TO_ID = {task: index for index, task in enumerate(TASKS)}
BINARY_TASKS = {TASK_TAKE_SKIP, TASK_PURGE_REMOVE}

NUMERIC_FEATURES = [
    "ascension",
    "floor",
    "option_count",
    "purge_index",
    "purchased_purge_count",
    "deck_size",
    "unique_card_count",
    "starter_count",
    "strike_count",
    "defend_count",
    "curse_count",
    "draw_card_count",
    "draw_density",
    "exhaust_card_count",
    "zero_cost_count",
    "attack_count",
    "skill_count",
    "power_count",
    "purge_count",
    "card_reward_take_count",
    "card_reward_skip_count",
    "card_reward_skip_rate",
    "starter_density",
]


@dataclass(frozen=True)
class DecisionRowsSource:
    source_id: str
    source_uri: str | None
    source_weight: float | None
    input_paths: list[Path]
    manifest_paths: list[Path]
    summary_paths: list[Path]
    reward_files: list[Path]
    purge_files: list[Path]
    deck_files: list[Path]
    warnings: list[str]


@dataclass(frozen=True)
class DecisionMultitaskExample:
    task: str
    row: dict[str, Any]
    target: float
    sample_weight: float


@dataclass(frozen=True)
class DecisionRowsLoad:
    source: DecisionRowsSource
    reward_rows: list[dict[str, Any]]
    purge_rows: list[dict[str, Any]]
    deck_rows: list[dict[str, Any]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a shadow-only multitask decision model from external rows.")
    parser.add_argument("inputs", nargs="+", type=Path, help="External manifest, structure summary, rows dir, or JSONL rows.")
    parser.add_argument("--model-path", type=Path, default=DECISION_MULTITASK_MODEL_PATH)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--prediction-output", type=Path, help="Optional JSONL shadow predictions for audit/evaluation.")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--learning-rate", type=float, default=0.005)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--min-categorical-count", type=int, default=1)
    parser.add_argument("--weak-negative-weight", type=float, default=0.25)
    parser.add_argument("--max-purge-negatives-per-run", type=int, default=64)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args(argv)

    result = train_decision_multitask_model(
        args.inputs,
        model_path=args.model_path,
        summary_output=args.summary_output,
        prediction_output=args.prediction_output,
        epochs=args.epochs,
        hidden_dim=args.hidden_dim,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        min_categorical_count=args.min_categorical_count,
        weak_negative_weight=args.weak_negative_weight,
        max_purge_negatives_per_run=args.max_purge_negatives_per_run,
        seed=args.seed,
        device_name=args.device,
    )
    summary = result["summary"]
    print(
        "decision_multitask_training: "
        f"examples={summary['examples']} tasks={summary['task_counts']} "
        f"device={summary['device']} train_loss={summary['train_loss']:.4f} "
        f"val_loss={summary['val_loss']:.4f} path={summary['path']}"
    )
    if args.summary_output:
        print(f"Wrote decision multitask summary: {args.summary_output}")
    if args.prediction_output:
        print(f"Wrote decision multitask predictions: {args.prediction_output}")
    return 0


def train_decision_multitask_model(
    inputs: Iterable[Path],
    *,
    model_path: Path = DECISION_MULTITASK_MODEL_PATH,
    summary_output: Path | None = None,
    prediction_output: Path | None = None,
    epochs: int = 80,
    hidden_dim: int = 64,
    batch_size: int = 64,
    learning_rate: float = 0.005,
    weight_decay: float = 0.0001,
    min_categorical_count: int = 1,
    weak_negative_weight: float = 0.25,
    max_purge_negatives_per_run: int = 64,
    seed: int = 11,
    device_name: str = "auto",
) -> dict[str, Any]:
    row_load = load_decision_rows(inputs)
    examples = build_examples(
        row_load,
        weak_negative_weight=weak_negative_weight,
        max_purge_negatives_per_run=max_purge_negatives_per_run,
        seed=seed,
    )
    if not examples:
        raise SystemExit("No decision multitask examples found.")
    result = train_torch_model(
        examples,
        source=row_load.source,
        row_counts={
            "card_reward_decisions": len(row_load.reward_rows),
            "card_purge_priors": len(row_load.purge_rows),
            "deck_cycle_priors": len(row_load.deck_rows),
        },
        model_path=model_path,
        epochs=epochs,
        hidden_dim=hidden_dim,
        batch_size=batch_size,
        learning_rate=learning_rate,
        weight_decay=weight_decay,
        min_categorical_count=min_categorical_count,
        weak_negative_weight=weak_negative_weight,
        max_purge_negatives_per_run=max_purge_negatives_per_run,
        seed=seed,
        device_name=device_name,
    )
    if summary_output:
        summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary_output.write_text(json.dumps(result["summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if prediction_output:
        write_predictions(prediction_output, examples, model_path)
    return result


def load_decision_rows(inputs: Iterable[Path]) -> DecisionRowsLoad:
    source = resolve_decision_rows_source(inputs)
    reward_rows = _read_many_jsonl(source.reward_files)
    purge_rows = _read_many_jsonl(source.purge_files)
    deck_rows = _read_many_jsonl(source.deck_files)
    return DecisionRowsLoad(source=source, reward_rows=reward_rows, purge_rows=purge_rows, deck_rows=deck_rows)


def resolve_decision_rows_source(inputs: Iterable[Path]) -> DecisionRowsSource:
    input_paths = [Path(path) for path in inputs]
    manifest_paths: list[Path] = []
    summary_paths: list[Path] = []
    reward_files: list[Path] = []
    purge_files: list[Path] = []
    deck_files: list[Path] = []
    warnings: list[str] = []
    source_ids: list[str] = []
    source_uri: str | None = None
    source_weight: float | None = None

    def add_row_file(candidate: Path) -> None:
        if not candidate.exists():
            return
        name = candidate.name
        if name == CARD_REWARD_DECISIONS_FILE:
            reward_files.append(candidate.resolve())
        elif name == CARD_PURGE_PRIORS_FILE:
            purge_files.append(candidate.resolve())
        elif name == DECK_CYCLE_PRIORS_FILE:
            deck_files.append(candidate.resolve())

    def add_rows_dir(directory: Path) -> None:
        for name in [CARD_REWARD_DECISIONS_FILE, CARD_PURGE_PRIORS_FILE, DECK_CYCLE_PRIORS_FILE]:
            add_row_file(directory / name)

    for path in input_paths:
        if not path.exists():
            warnings.append(f"missing:{path}")
            continue
        if path.is_dir():
            add_rows_dir(path)
            manifest = path / "external_manifest.json"
            if manifest.exists():
                manifest_paths.append(manifest.resolve())
                payload = _read_json(manifest)
                if payload.get("source_id"):
                    source_ids.append(str(payload["source_id"]))
                source_uri = source_uri or payload.get("source_uri")
                if payload.get("source_weight") is not None:
                    source_weight = float(payload["source_weight"])
            summary = path / "external_structure_training_summary.json"
            if summary.exists():
                summary_paths.append(summary.resolve())
                payload = _read_json(summary)
                source_payload = payload.get("training_source") if isinstance(payload.get("training_source"), dict) else {}
                if source_payload.get("source_id"):
                    source_ids.append(str(source_payload["source_id"]))
                source_uri = source_uri or source_payload.get("source_uri")
                if source_payload.get("source_weight") is not None:
                    source_weight = float(source_payload["source_weight"])
                _add_summary_rows(payload, add_row_file)
            continue
        if path.name == "external_manifest.json":
            manifest_paths.append(path.resolve())
            payload = _read_json(path)
            if payload.get("source_id"):
                source_ids.append(str(payload["source_id"]))
            source_uri = source_uri or payload.get("source_uri")
            if payload.get("source_weight") is not None:
                source_weight = float(payload["source_weight"])
            add_rows_dir(path.parent)
            continue
        if path.name == "external_structure_training_summary.json":
            summary_paths.append(path.resolve())
            payload = _read_json(path)
            source_payload = payload.get("training_source") if isinstance(payload.get("training_source"), dict) else {}
            if source_payload.get("source_id"):
                source_ids.append(str(source_payload["source_id"]))
            source_uri = source_uri or source_payload.get("source_uri")
            if source_payload.get("source_weight") is not None:
                source_weight = float(source_payload["source_weight"])
            _add_summary_rows(payload, add_row_file)
            add_rows_dir(path.parent)
            continue
        add_row_file(path)

    source_id = _safe_artifact_name(source_ids[0]) if source_ids else _infer_source_id(input_paths)
    return DecisionRowsSource(
        source_id=source_id,
        source_uri=source_uri,
        source_weight=source_weight,
        input_paths=input_paths,
        manifest_paths=_unique_paths(manifest_paths),
        summary_paths=_unique_paths(summary_paths),
        reward_files=_unique_paths(reward_files),
        purge_files=_unique_paths(purge_files),
        deck_files=_unique_paths(deck_files),
        warnings=warnings,
    )


def build_examples(
    row_load: DecisionRowsLoad,
    *,
    weak_negative_weight: float = 0.25,
    max_purge_negatives_per_run: int = 64,
    seed: int = 11,
) -> list[DecisionMultitaskExample]:
    examples: list[DecisionMultitaskExample] = []
    for row in row_load.reward_rows:
        decision = str(row.get("decision") or "").lower()
        if decision not in {"take", "skip"}:
            continue
        examples.append(
            DecisionMultitaskExample(
                task=TASK_TAKE_SKIP,
                row=row,
                target=1.0 if decision == "take" else 0.0,
                sample_weight=_source_weight(row),
            )
        )
    for row in row_load.purge_rows:
        card = _candidate_remove_card(row)
        if not card:
            continue
        enriched = dict(row)
        enriched["candidate_card"] = card
        enriched["label_source"] = "external_removed_card"
        examples.append(
            DecisionMultitaskExample(
                task=TASK_PURGE_REMOVE,
                row=enriched,
                target=1.0,
                sample_weight=_source_weight(row),
            )
        )
    if weak_negative_weight > 0:
        for row in _purge_weak_negative_rows(
            row_load.reward_rows,
            row_load.purge_rows,
            weak_negative_weight=weak_negative_weight,
            max_per_run=max_purge_negatives_per_run,
            seed=seed,
        ):
            examples.append(
                DecisionMultitaskExample(
                    task=TASK_PURGE_REMOVE,
                    row=row,
                    target=0.0,
                    sample_weight=_source_weight(row) * weak_negative_weight,
                )
            )
    for row in row_load.deck_rows:
        examples.append(
            DecisionMultitaskExample(
                task=TASK_DECK_CYCLE,
                row=row,
                target=_deck_cycle_target(row),
                sample_weight=_source_weight(row),
            )
        )
    return examples


def train_torch_model(
    examples: list[DecisionMultitaskExample],
    *,
    source: DecisionRowsSource,
    row_counts: dict[str, int],
    model_path: Path = DECISION_MULTITASK_MODEL_PATH,
    epochs: int = 80,
    hidden_dim: int = 64,
    batch_size: int = 64,
    learning_rate: float = 0.005,
    weight_decay: float = 0.0001,
    min_categorical_count: int = 1,
    weak_negative_weight: float = 0.25,
    max_purge_negatives_per_run: int = 64,
    seed: int = 11,
    device_name: str = "auto",
) -> dict[str, Any]:
    torch = _torch()
    _seed_everything(seed, torch)
    feature_spec = _build_feature_spec(examples, min_categorical_count=min_categorical_count)
    vectors = [_vectorize(example, feature_spec) for example in examples]
    device = _select_device(torch, device_name)
    x = torch.tensor(vectors, dtype=torch.float32, device=device)
    targets = torch.tensor([example.target for example in examples], dtype=torch.float32, device=device)
    task_ids = torch.tensor([TASK_TO_ID[example.task] for example in examples], dtype=torch.long, device=device)
    weights = torch.tensor([example.sample_weight for example in examples], dtype=torch.float32, device=device)

    model = _DecisionMultitaskNet(input_dim=int(x.shape[1]), hidden_dim=hidden_dim, torch=torch).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    train_idx, val_idx = _train_val_split(len(examples), seed=seed)
    train_tensor = torch.tensor(train_idx, dtype=torch.long, device=device)
    val_tensor = torch.tensor(val_idx, dtype=torch.long, device=device) if val_idx else None

    last_train_loss = 0.0
    last_val_loss = 0.0
    batch_size = max(1, batch_size)
    for epoch in range(max(1, epochs)):
        model.train()
        shuffled = list(train_idx)
        random.Random(seed + epoch).shuffle(shuffled)
        total = 0.0
        batches = 0
        for start in range(0, len(shuffled), batch_size):
            batch = torch.tensor(shuffled[start : start + batch_size], dtype=torch.long, device=device)
            outputs = model(x.index_select(0, batch))
            loss = _multitask_loss(
                outputs,
                task_ids.index_select(0, batch),
                targets.index_select(0, batch),
                weights.index_select(0, batch),
                torch,
            )
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.detach().cpu().item())
            batches += 1
        last_train_loss = total / max(batches, 1)
        model.eval()
        with torch.no_grad():
            if val_tensor is not None and len(val_idx) > 0:
                last_val_loss = float(
                    _multitask_loss(
                        model(x.index_select(0, val_tensor)),
                        task_ids.index_select(0, val_tensor),
                        targets.index_select(0, val_tensor),
                        weights.index_select(0, val_tensor),
                        torch,
                    )
                    .detach()
                    .cpu()
                    .item()
                )
            else:
                last_val_loss = float(
                    _multitask_loss(
                        model(x.index_select(0, train_tensor)),
                        task_ids.index_select(0, train_tensor),
                        targets.index_select(0, train_tensor),
                        weights.index_select(0, train_tensor),
                        torch,
                    )
                    .detach()
                    .cpu()
                    .item()
                )

    model.eval()
    with torch.no_grad():
        prediction_values = _predict_values(model(x), task_ids, torch)
    evaluation = _evaluation_metrics(examples, prediction_values, train_idx, val_idx)
    prediction_audit = _prediction_audit(examples, prediction_values, val_idx)
    promotion_readiness = _promotion_readiness(evaluation, prediction_audit)

    task_counts = _task_counts(examples)
    metadata = {
        "version": 1,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "model_kind": "decision_multitask_shadow",
        "backend": "pytorch_multitask_mlp",
        "training_source": training_source_payload(source),
        "training_source_quality": EXTERNAL_PRIOR_GRADE,
        "source_validation_grade": EXTERNAL_PRIOR_GRADE,
        "runtime_authority": False,
        "runtime_default_enabled": False,
        "runtime_authority_level": "shadow",
        "does_not_control_live_mcp": True,
        "direct_mcp_control": False,
        "requires_audited_promotion": True,
        "forbidden_uses": FORBIDDEN_USES,
        "heads": list(TASKS),
        "target_definitions": {
            TASK_TAKE_SKIP: "binary label: take=1 skip=0 from adapted card rewards",
            TASK_PURGE_REMOVE: "binary label: removed=1 weak_keep_candidate=0",
            TASK_DECK_CYCLE: "bounded outcome quality from victory/final_floor, predicted from deck-cycle features",
        },
        "weak_negative_sampling": {
            "purge_keep_from_reward_options": weak_negative_weight > 0,
            "weak_negative_weight": weak_negative_weight,
            "max_purge_negatives_per_run": max_purge_negatives_per_run,
            "quality": "weak_external_keep_candidate_not_pristine",
        },
        "examples": len(examples),
        "train_examples": len(train_idx),
        "validation_examples": len(val_idx),
        "weighted_examples": round(sum(example.sample_weight for example in examples), 4),
        "task_counts": task_counts,
        "row_counts": row_counts,
        "device": str(device),
        "torch_version": str(torch.__version__),
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_runtime": getattr(torch.version, "cuda", None),
        "epochs": epochs,
        "hidden_dim": hidden_dim,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "weight_decay": weight_decay,
        "min_categorical_count": min_categorical_count,
        "seed": seed,
        "train_loss": last_train_loss,
        "val_loss": last_val_loss,
        "evaluation": evaluation,
        "prediction_audit": prediction_audit,
        "promotion_readiness": promotion_readiness,
    }
    metadata.update(hardware_metadata(device_name))
    checkpoint = {
        "version": 1,
        "model_type": "decision_multitask_shadow_mlp",
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "feature_spec": feature_spec,
        "task_to_id": dict(TASK_TO_ID),
        "input_dim": int(x.shape[1]),
        "hidden_dim": hidden_dim,
        "metadata": metadata,
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, model_path)
    summary = {
        **metadata,
        "path": str(model_path),
        "input_dim": checkpoint["input_dim"],
        "numeric_features": len(feature_spec["numeric_features"]),
        "categorical_features": len(feature_spec["categorical_vocab"]),
    }
    return {"checkpoint": checkpoint, "summary": summary}


def score_row(model_path: Path, task: str, row: dict[str, Any]) -> float:
    torch, checkpoint, model = _load_checkpoint_model(model_path)
    if task not in TASK_TO_ID:
        raise ValueError(f"unknown task: {task}")
    example = DecisionMultitaskExample(task=task, row=row, target=0.0, sample_weight=1.0)
    with torch.no_grad():
        vector = torch.tensor([_vectorize(example, checkpoint["feature_spec"])], dtype=torch.float32)
        output = model(vector)[task].squeeze()
        if task in BINARY_TASKS:
            return round(float(torch.sigmoid(output).item()), 4)
        return round(max(0.0, min(1.0, float(output.item()))), 4)


def write_predictions(output_path: Path, examples: list[DecisionMultitaskExample], model_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch, checkpoint, model = _load_checkpoint_model(model_path)
    with output_path.open("w", encoding="utf-8") as handle:
        for index, example in enumerate(examples, start=1):
            prediction = _score_example(torch, checkpoint, model, example)
            handle.write(
                json.dumps(
                    _prediction_record(index, example, prediction, checkpoint),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


def _score_example(torch: Any, checkpoint: dict[str, Any], model: Any, example: DecisionMultitaskExample) -> float:
    with torch.no_grad():
        vector = torch.tensor([_vectorize(example, checkpoint["feature_spec"])], dtype=torch.float32)
        output = model(vector)[example.task].squeeze()
        if example.task in BINARY_TASKS:
            return round(float(torch.sigmoid(output).item()), 4)
        return round(max(0.0, min(1.0, float(output.item()))), 4)


def _prediction_record(
    index: int,
    example: DecisionMultitaskExample,
    prediction: float,
    checkpoint: dict[str, Any],
) -> dict[str, Any]:
    row = example.row
    record = {
        "index": index,
        "task": example.task,
        "target": round(float(example.target), 4),
        "prediction": prediction,
        "sample_weight": round(float(example.sample_weight), 6),
        "character": row.get("character"),
        "floor": row.get("floor"),
        "source_dataset": row.get("source_dataset"),
        "source_file": row.get("source_file"),
        "source_index": row.get("source_index"),
        "source_validation_grade": row.get("source_validation_grade"),
        "transform_target": row.get("transform_target"),
        "model_authority": {
            "level": "shadow",
            "runtime_authority": False,
            "runtime_default_enabled": False,
            "does_not_control_live_mcp": True,
            "direct_mcp_control": False,
            "requires_audited_promotion": True,
            "model_kind": (checkpoint.get("metadata") or {}).get("model_kind"),
        },
    }
    if example.task in BINARY_TASKS:
        predicted_target = 1.0 if prediction >= 0.5 else 0.0
        record["predicted_target"] = predicted_target
        record["correct"] = predicted_target == float(example.target)
    if example.task == TASK_TAKE_SKIP:
        record.update(
            {
                "actual_decision": row.get("decision"),
                "predicted_decision": "take" if prediction >= 0.5 else "skip",
                "picked": row.get("picked"),
                "options": row.get("options") or [],
            }
        )
    elif example.task == TASK_PURGE_REMOVE:
        record.update(
            {
                "candidate_card": _candidate_remove_card(row),
                "actual_decision": "remove" if example.target >= 0.5 else "keep_candidate",
                "predicted_decision": "remove" if prediction >= 0.5 else "keep_candidate",
                "label_source": row.get("label_source"),
            }
        )
    elif example.task == TASK_DECK_CYCLE:
        record.update(
            {
                "deck_size": row.get("deck_size"),
                "draw_density": row.get("draw_density"),
                "starter_density": row.get("starter_density"),
                "purge_count": row.get("purge_count"),
                "absolute_error": round(abs(prediction - float(example.target)), 4),
            }
        )
    return record

def training_source_payload(source: DecisionRowsSource) -> dict[str, Any]:
    return {
        "mode": "external_decision_multitask",
        "source_category": EXTERNAL_RUN_HISTORY,
        "source_quality_policy": EXTERNAL_PRIOR_GRADE,
        "source_id": source.source_id,
        "source_uri": source.source_uri,
        "source_weight": source.source_weight,
        "inputs": [str(path) for path in source.input_paths],
        "manifest_paths": [str(path) for path in source.manifest_paths],
        "summary_paths": [str(path) for path in source.summary_paths],
        "reward_files": [str(path) for path in source.reward_files],
        "purge_files": [str(path) for path in source.purge_files],
        "deck_files": [str(path) for path in source.deck_files],
        "warnings": source.warnings,
        "forbidden_uses": FORBIDDEN_USES,
    }


def _load_checkpoint_model(model_path: Path) -> tuple[Any, dict[str, Any], Any]:
    torch = _torch()
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    model = _DecisionMultitaskNet(
        input_dim=int(checkpoint["input_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
        torch=torch,
    )
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return torch, checkpoint, model


def _DecisionMultitaskNet(*, input_dim: int, hidden_dim: int, torch: Any) -> Any:
    class Net(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            mid = max(8, hidden_dim // 2)
            self.shared = torch.nn.Sequential(
                torch.nn.Linear(input_dim, hidden_dim),
                torch.nn.ReLU(),
                torch.nn.Dropout(p=0.05),
                torch.nn.Linear(hidden_dim, mid),
                torch.nn.ReLU(),
            )
            self.heads = torch.nn.ModuleDict({task: torch.nn.Linear(mid, 1) for task in TASKS})

        def forward(self, x: Any) -> dict[str, Any]:
            hidden = self.shared(x)
            return {task: head(hidden).squeeze(-1) for task, head in self.heads.items()}

    return Net()


def _multitask_loss(outputs: dict[str, Any], task_ids: Any, targets: Any, weights: Any, torch: Any) -> Any:
    total_loss = None
    used_heads = 0
    bce = torch.nn.BCEWithLogitsLoss(reduction="none")
    mse = torch.nn.MSELoss(reduction="none")
    for task, task_id in TASK_TO_ID.items():
        mask = task_ids == task_id
        if not bool(mask.any().detach().cpu().item()):
            continue
        prediction = outputs[task][mask]
        target = targets[mask]
        losses = bce(prediction, target) if task in BINARY_TASKS else mse(prediction, target)
        weighted = _weighted_loss(losses, weights[mask], torch)
        total_loss = weighted if total_loss is None else total_loss + weighted
        used_heads += 1
    if total_loss is None:
        return torch.tensor(0.0, dtype=torch.float32, device=targets.device)
    return total_loss / max(used_heads, 1)


def _predict_values(outputs: dict[str, Any], task_ids: Any, torch: Any) -> list[float]:
    predictions = [0.0] * int(task_ids.numel())
    for task, task_id in TASK_TO_ID.items():
        mask = task_ids == task_id
        if not bool(mask.any().detach().cpu().item()):
            continue
        values = outputs[task][mask]
        if task in BINARY_TASKS:
            values = torch.sigmoid(values)
        else:
            values = torch.clamp(values, min=0.0, max=1.0)
        indices = torch.nonzero(mask, as_tuple=False).flatten().detach().cpu().tolist()
        for index, value in zip(indices, values.detach().cpu().tolist()):
            predictions[int(index)] = round(float(value), 4)
    return predictions


def _evaluation_metrics(
    examples: list[DecisionMultitaskExample],
    predictions: list[float],
    train_idx: list[int],
    val_idx: list[int],
) -> dict[str, Any]:
    all_indices = list(range(len(examples)))
    return {
        "all": _split_metrics(examples, predictions, all_indices),
        "train": _split_metrics(examples, predictions, train_idx),
        "validation": _split_metrics(examples, predictions, val_idx),
    }


def _split_metrics(
    examples: list[DecisionMultitaskExample],
    predictions: list[float],
    indices: list[int],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for task in TASKS:
        task_indices = [index for index in indices if examples[index].task == task]
        if not task_indices:
            metrics[task] = {"examples": 0}
            continue
        targets = [float(examples[index].target) for index in task_indices]
        scores = [float(predictions[index]) for index in task_indices]
        if task in BINARY_TASKS:
            predicted = [1.0 if score >= 0.5 else 0.0 for score in scores]
            actual = [1.0 if target >= 0.5 else 0.0 for target in targets]
            correct = sum(1 for guess, target in zip(predicted, actual) if guess == target)
            tp = sum(1 for guess, target in zip(predicted, actual) if guess == 1.0 and target == 1.0)
            fp = sum(1 for guess, target in zip(predicted, actual) if guess == 1.0 and target == 0.0)
            fn = sum(1 for guess, target in zip(predicted, actual) if guess == 0.0 and target == 1.0)
            tn = sum(1 for guess, target in zip(predicted, actual) if guess == 0.0 and target == 0.0)
            metrics[task] = {
                "examples": len(task_indices),
                "positive_examples": int(sum(actual)),
                "predicted_positive": int(sum(predicted)),
                "accuracy": _round_metric(correct / len(task_indices)),
                "precision": _round_metric(tp / max(tp + fp, 1)),
                "recall": _round_metric(tp / max(tp + fn, 1)),
                "true_positive": tp,
                "false_positive": fp,
                "true_negative": tn,
                "false_negative": fn,
                "mean_prediction": _round_metric(sum(scores) / len(scores)),
                "mean_target": _round_metric(sum(targets) / len(targets)),
            }
        else:
            absolute_errors = [abs(score - target) for score, target in zip(scores, targets)]
            metrics[task] = {
                "examples": len(task_indices),
                "mae": _round_metric(sum(absolute_errors) / len(absolute_errors)),
                "max_absolute_error": _round_metric(max(absolute_errors)),
                "mean_prediction": _round_metric(sum(scores) / len(scores)),
                "mean_target": _round_metric(sum(targets) / len(targets)),
            }
    return metrics


def _round_metric(value: float) -> float:
    return round(float(value), 4)

def _build_feature_spec(examples: list[DecisionMultitaskExample], *, min_categorical_count: int) -> dict[str, Any]:
    numeric_values = {feature: [_numeric(example.row.get(feature)) for example in examples] for feature in NUMERIC_FEATURES}
    token_counts: Counter[str] = Counter()
    for example in examples:
        token_counts.update(_row_tokens(example))
    categorical_vocab = {
        token: index
        for index, token in enumerate(
            sorted(token for token, count in token_counts.items() if count >= min_categorical_count)
        )
    }
    return {
        "numeric_features": list(NUMERIC_FEATURES),
        "numeric_means": {feature: _mean(values) for feature, values in numeric_values.items()},
        "numeric_scales": {feature: _std(values, fallback=1.0) for feature, values in numeric_values.items()},
        "categorical_vocab": categorical_vocab,
    }


def _vectorize(example: DecisionMultitaskExample, feature_spec: dict[str, Any]) -> list[float]:
    row = example.row
    vector: list[float] = []
    for feature in feature_spec["numeric_features"]:
        scale = float(feature_spec["numeric_scales"].get(feature) or 1.0)
        vector.append((_numeric(row.get(feature)) - float(feature_spec["numeric_means"].get(feature, 0.0))) / scale)
    categorical = [0.0] * len(feature_spec["categorical_vocab"])
    for token in _row_tokens(example):
        index = feature_spec["categorical_vocab"].get(token)
        if index is not None:
            categorical[index] = 1.0
    return vector + categorical


def _row_tokens(example: DecisionMultitaskExample) -> list[str]:
    row = example.row
    tokens = [f"task:{example.task}"]
    character = str(row.get("character") or "").upper()
    if character:
        tokens.append(f"character:{character}")
    floor = int(_numeric(row.get("floor")))
    if floor:
        tokens.append(f"floor_bucket:{_floor_bucket(floor)}")
    if example.task == TASK_TAKE_SKIP:
        for option in row.get("options") or []:
            key = combat_card_key(_base_card_name(option))
            if key:
                tokens.append(f"reward_option:{key}")
    elif example.task == TASK_PURGE_REMOVE:
        key = combat_card_key(_base_card_name(row.get("candidate_card") or row.get("removed_base") or row.get("removed")))
        if key:
            tokens.append(f"purge_candidate:{key}")
        if row.get("label_source"):
            tokens.append(f"label_source:{row['label_source']}")
    else:
        tokens.append("deck_cycle:aggregate")
    return tokens


def _purge_weak_negative_rows(
    reward_rows: list[dict[str, Any]],
    purge_rows: list[dict[str, Any]],
    *,
    weak_negative_weight: float,
    max_per_run: int,
    seed: int,
) -> list[dict[str, Any]]:
    if weak_negative_weight <= 0 or max_per_run <= 0:
        return []
    removed_by_run: dict[tuple[str, str], set[str]] = {}
    for row in purge_rows:
        key = _run_key(row)
        card = _candidate_remove_card(row)
        if card:
            removed_by_run.setdefault(key, set()).add(combat_card_key(_base_card_name(card)))
    candidates_by_run: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in reward_rows:
        run_key = _run_key(row)
        removed = removed_by_run.get(run_key, set())
        for option in row.get("options") or []:
            card = _base_card_name(option)
            card_key = combat_card_key(card)
            if not card_key or card_key in removed:
                continue
            negative = dict(row)
            negative["candidate_card"] = card
            negative["label_source"] = "weak_external_keep_candidate"
            negative["transform_target"] = "remove_vs_keep"
            candidates_by_run.setdefault(run_key, []).append(negative)
    rng = random.Random(seed)
    negatives: list[dict[str, Any]] = []
    for run_key in sorted(candidates_by_run):
        seen_cards: set[str] = set()
        unique: list[dict[str, Any]] = []
        for row in candidates_by_run[run_key]:
            card_key = combat_card_key(row.get("candidate_card"))
            if not card_key or card_key in seen_cards:
                continue
            seen_cards.add(card_key)
            unique.append(row)
        rng.shuffle(unique)
        negatives.extend(unique[:max_per_run])
    return negatives


def _read_many_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(_read_jsonl(path))
    return rows


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _add_summary_rows(payload: dict[str, Any], add_row_file: Any) -> None:
    rows = payload.get("rows") if isinstance(payload.get("rows"), dict) else {}
    for value in rows.values():
        if value:
            add_row_file(Path(str(value)))


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    return list(dict.fromkeys(path.resolve() for path in paths))


def _infer_source_id(input_paths: list[Path]) -> str:
    for path in input_paths:
        if path.exists():
            return _safe_artifact_name(path.stem if path.is_file() else path.name)
    return "decision_multitask_external"


def _run_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("source_file") or ""), str(row.get("source_index") or ""))


def _candidate_remove_card(row: dict[str, Any]) -> str:
    return _base_card_name(row.get("candidate_card") or row.get("removed_base") or row.get("removed"))


def _source_weight(row: dict[str, Any]) -> float:
    try:
        weight = float(row.get("source_weight") if row.get("source_weight") is not None else 1.0)
    except (TypeError, ValueError):
        weight = 1.0
    return max(weight, 0.0)


def _deck_cycle_target(row: dict[str, Any]) -> float:
    reward = _outcome_reward(bool(row.get("victory")), int(_numeric(row.get("final_floor"))))
    return round(max(0.0, min(1.0, (reward + 0.6) / 1.6)), 4)


def _task_counts(examples: list[DecisionMultitaskExample]) -> dict[str, int]:
    counts: dict[str, int] = {task: 0 for task in TASKS}
    for example in examples:
        counts[example.task] = counts.get(example.task, 0) + 1
    return counts


def _floor_bucket(floor: int) -> str:
    if floor <= 16:
        return "act1"
    if floor <= 33:
        return "act2"
    return "act3_plus"


if __name__ == "__main__":
    raise SystemExit(main())
