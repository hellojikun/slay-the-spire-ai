"""Train a PyTorch shadow combat-value model from search decision labels."""

from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .model import COMBAT_VALUE_MODEL_PATH, combat_card_key, combat_search_context_keys


NUMERIC_FEATURES = [
    "act",
    "floor",
    "turn",
    "hp_ratio",
    "current_hp",
    "max_hp",
    "current_block",
    "current_energy",
    "incoming",
    "hand_size",
    "playable_count",
    "enemy_count",
    "initial_loss",
    "retaliation_damage",
    "sequence_length",
    "enemy_boss_count",
    "enemy_elite_or_boss_count",
    "enemy_total_expected_attack",
    "enemy_average_expected_attack",
    "enemy_status_pressure_count",
    "enemy_debuff_count",
    "enemy_scaling_pressure_count",
    "enemy_split_count",
    "enemy_multi_enemy_pressure_count",
    "enemy_artifact_count",
    "enemy_potion_tempo_check_count",
    "enemy_frontload_check_count",
    "enemy_block_check_count",
    "enemy_aoe_high_value_count",
    "boss_known_count",
    "boss_possible_count",
    "boss_max_expected_attack",
    "boss_total_expected_attack",
    "boss_max_split_threshold_percent",
    "boss_max_mode_shift_threshold",
    "boss_max_sharp_hide_damage",
    "boss_max_hit_count",
    "boss_max_burn_damage",
    "boss_max_upgraded_burn_damage",
    "boss_max_post_split_enemy_count",
]


@dataclass(frozen=True)
class CombatValueExample:
    row: dict[str, Any]
    target_value: float
    sample_weight: float


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train a CUDA-capable PyTorch shadow model for combat search value labels."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Run JSONL files, run directories, combat_search_labels.jsonl files, or shadow directories.",
    )
    parser.add_argument("--model-path", type=Path, default=COMBAT_VALUE_MODEL_PATH)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--prediction-output", type=Path)
    parser.add_argument("--epochs", type=int, default=180)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--min-categorical-count", type=int, default=1)
    parser.add_argument(
        "--quality-policy",
        choices=["pristine", "weighted"],
        default="pristine",
        help="Source-quality gate. pristine trains only pristine rows; weighted can downweight recoveries/diagnostics.",
    )
    parser.add_argument("--usable-weight", type=float, default=0.35)
    parser.add_argument("--diagnostic-weight", type=float, default=0.0)
    parser.add_argument("--infra-weight", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    args = parser.parse_args(argv)

    examples = load_examples(
        args.inputs,
        quality_policy=args.quality_policy,
        usable_weight=args.usable_weight,
        diagnostic_weight=args.diagnostic_weight,
        infra_weight=args.infra_weight,
    )
    if not examples:
        raise SystemExit("No combat value examples found.")

    result = train_torch_model(
        examples,
        model_path=args.model_path,
        epochs=args.epochs,
        hidden_dim=args.hidden_dim,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        min_categorical_count=args.min_categorical_count,
        seed=args.seed,
        device_name=args.device,
        quality_policy=args.quality_policy,
    )
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(json.dumps(result["summary"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote combat value summary: {args.summary_output}")
    if args.prediction_output:
        write_predictions(args.prediction_output, examples, args.model_path)
        print(f"Wrote combat value predictions: {args.prediction_output}")

    summary = result["summary"]
    print(
        "Combat value model trained: "
        f"examples={summary['examples']}, "
        f"features={summary['input_dim']}, "
        f"device={summary['device']}, "
        f"train_loss={summary['train_loss']:.4f}, "
        f"val_loss={summary['val_loss']:.4f}, "
        f"path={args.model_path}"
    )
    return 0


def load_examples(
    paths: Iterable[Path],
    *,
    quality_policy: str = "pristine",
    usable_weight: float = 0.35,
    diagnostic_weight: float = 0.0,
    infra_weight: float = 0.0,
) -> list[CombatValueExample]:
    examples: list[CombatValueExample] = []
    for path in _iter_jsonl_files(paths):
        if path.name == "combat_search_labels.jsonl":
            rows = _rows_from_shadow_file(path)
        else:
            rows = _rows_from_run_log(path)
        for row in rows:
            key = _label_first_card_key(row)
            if not key:
                continue
            row["label_first_card_key"] = key
            row["sequence_length"] = len(row.get("label_sequence_card_keys") or [key])
            weight = _source_quality_weight(
                row,
                quality_policy=quality_policy,
                usable_weight=usable_weight,
                diagnostic_weight=diagnostic_weight,
                infra_weight=infra_weight,
            )
            if weight <= 0:
                continue
            examples.append(CombatValueExample(row=row, target_value=_label_value(row), sample_weight=weight))
    return examples


def train_torch_model(
    examples: list[CombatValueExample],
    *,
    model_path: Path = COMBAT_VALUE_MODEL_PATH,
    epochs: int = 180,
    hidden_dim: int = 64,
    batch_size: int = 32,
    learning_rate: float = 0.01,
    weight_decay: float = 0.0001,
    min_categorical_count: int = 1,
    seed: int = 7,
    device_name: str = "auto",
    quality_policy: str = "pristine",
) -> dict[str, Any]:
    torch = _torch()
    _seed_everything(seed, torch)
    feature_spec = _build_feature_spec(examples, min_categorical_count=min_categorical_count)
    inputs = [_vectorize(example.row, feature_spec) for example in examples]
    targets = [example.target_value for example in examples]
    target_mean = _mean(targets)
    target_scale = _std(targets, fallback=1.0)

    device = _select_device(torch, device_name)
    x = torch.tensor(inputs, dtype=torch.float32, device=device)
    y = torch.tensor([(target - target_mean) / target_scale for target in targets], dtype=torch.float32, device=device).view(-1, 1)
    weights = torch.tensor([example.sample_weight for example in examples], dtype=torch.float32, device=device).view(-1, 1)

    model = _CombatValueNet(input_dim=x.shape[1], hidden_dim=hidden_dim, torch=torch).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
    criterion = torch.nn.MSELoss(reduction="none")

    train_idx, val_idx = _train_val_split(len(examples), seed=seed)
    train_tensor = torch.tensor(train_idx, dtype=torch.long, device=device)
    val_tensor = torch.tensor(val_idx, dtype=torch.long, device=device) if val_idx else None

    last_train_loss = 0.0
    last_val_loss = 0.0
    batch_size = max(1, batch_size)
    for _epoch in range(max(1, epochs)):
        model.train()
        shuffled = list(train_idx)
        random.Random(seed + _epoch).shuffle(shuffled)
        total = 0.0
        batches = 0
        for start in range(0, len(shuffled), batch_size):
            batch = torch.tensor(shuffled[start : start + batch_size], dtype=torch.long, device=device)
            prediction = model(x.index_select(0, batch))
            batch_weights = weights.index_select(0, batch)
            loss = _weighted_loss(criterion(prediction, y.index_select(0, batch)), batch_weights, torch)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.detach().cpu().item())
            batches += 1
        last_train_loss = total / max(batches, 1)
        model.eval()
        with torch.no_grad():
            if val_tensor is not None and len(val_idx) > 0:
                val_losses = criterion(model(x.index_select(0, val_tensor)), y.index_select(0, val_tensor))
                last_val_loss = float(_weighted_loss(val_losses, weights.index_select(0, val_tensor), torch).cpu().item())
            else:
                train_losses = criterion(model(x.index_select(0, train_tensor)), y.index_select(0, train_tensor))
                last_val_loss = float(_weighted_loss(train_losses, weights.index_select(0, train_tensor), torch).cpu().item())

    quality_counts = _quality_counts(examples)

    checkpoint = {
        "version": 1,
        "model_type": "combat_value_shadow_mlp",
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "feature_spec": feature_spec,
        "target_mean": target_mean,
        "target_scale": target_scale,
        "input_dim": int(x.shape[1]),
        "hidden_dim": hidden_dim,
        "metadata": {
            "version": 1,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "pytorch_mlp",
            "examples": len(examples),
            "train_examples": len(train_idx),
            "validation_examples": len(val_idx),
            "weighted_examples": round(sum(example.sample_weight for example in examples), 4),
            "source_quality_policy": quality_policy,
            "source_quality_counts": quality_counts,
            "target": "combat_search_value",
            "target_definition": "loss_reduction_ratio_plus_kills_attacks_removed_retaliation_damage_avoided_lethal",
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
        },
    }
    model_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, model_path)
    summary = {
        **checkpoint["metadata"],
        "path": str(model_path),
        "input_dim": checkpoint["input_dim"],
        "numeric_features": len(feature_spec["numeric_features"]),
        "categorical_features": len(feature_spec["categorical_vocab"]),
        "target_mean": round(target_mean, 4),
        "target_scale": round(target_scale, 4),
    }
    return {"checkpoint": checkpoint, "summary": summary}


def score_row(model_path: Path, row: dict[str, Any]) -> float:
    torch = _torch()
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    feature_spec = checkpoint["feature_spec"]
    model = _CombatValueNet(
        input_dim=int(checkpoint["input_dim"]),
        hidden_dim=int(checkpoint["hidden_dim"]),
        torch=torch,
    )
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    with torch.no_grad():
        vector = torch.tensor([_vectorize(row, feature_spec)], dtype=torch.float32)
        normalized = float(model(vector).squeeze().item())
    return round(float(checkpoint["target_mean"]) + normalized * float(checkpoint["target_scale"]), 4)


class CombatValueScorer:
    """Lazy runtime scorer for shadow combat-value predictions."""

    def __init__(self, model_path: Path = COMBAT_VALUE_MODEL_PATH) -> None:
        self.model_path = model_path
        self._loaded: tuple[Any, dict[str, Any], Any] | None = None

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def score(self, row: dict[str, Any]) -> float:
        torch, checkpoint, model = self._load()
        with torch.no_grad():
            vector = torch.tensor([_vectorize(row, checkpoint["feature_spec"])], dtype=torch.float32)
            normalized = float(model(vector).squeeze().item())
        return round(float(checkpoint["target_mean"]) + normalized * float(checkpoint["target_scale"]), 4)

    def _load(self) -> tuple[Any, dict[str, Any], Any]:
        if self._loaded is not None:
            return self._loaded
        if not self.model_path.exists():
            raise FileNotFoundError(self.model_path)
        torch = _torch()
        checkpoint = torch.load(self.model_path, map_location="cpu", weights_only=False)
        model = _CombatValueNet(
            input_dim=int(checkpoint["input_dim"]),
            hidden_dim=int(checkpoint["hidden_dim"]),
            torch=torch,
        )
        model.load_state_dict(checkpoint["state_dict"])
        model.eval()
        self._loaded = (torch, checkpoint, model)
        return self._loaded


def write_predictions(output_path: Path, examples: list[CombatValueExample], model_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for example in examples:
            prediction = score_row(model_path, example.row)
            handle.write(
                json.dumps(
                    {
                        "source_log": example.row.get("source_log"),
                        "step": example.row.get("step"),
                        "floor": example.row.get("floor"),
                        "act": example.row.get("act"),
                        "label_first_card_key": example.row.get("label_first_card_key"),
                        "label_sequence_card_keys": example.row.get("label_sequence_card_keys") or [],
                        "target_value": round(example.target_value, 4),
                        "predicted_value": prediction,
                        "error": round(prediction - example.target_value, 4),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )


class _CombatValueNet:
    def __new__(cls, *, input_dim: int, hidden_dim: int, torch: Any) -> Any:
        class Net(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                mid = max(8, hidden_dim // 2)
                self.layers = torch.nn.Sequential(
                    torch.nn.Linear(input_dim, hidden_dim),
                    torch.nn.ReLU(),
                    torch.nn.Dropout(p=0.05),
                    torch.nn.Linear(hidden_dim, mid),
                    torch.nn.ReLU(),
                    torch.nn.Linear(mid, 1),
                )

            def forward(self, x: Any) -> Any:
                return self.layers(x)

        return Net()


def _iter_jsonl_files(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in paths:
        candidates: list[Path]
        if path.is_dir():
            candidates = sorted(path.rglob("*.jsonl"))
        elif path.exists() and path.suffix.lower() == ".jsonl":
            candidates = [path]
        else:
            candidates = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield candidate


def _rows_from_shadow_file(path: Path) -> Iterable[dict[str, Any]]:
    for row in _read_jsonl(path):
        if not isinstance(row, dict):
            continue
        if row.get("initial_loss") is None or row.get("projected_loss") is None:
            continue
        updated = dict(row)
        updated.setdefault("source_log", str(path))
        updated.update(_source_quality_from_shadow_row(updated))
        yield updated


def _rows_from_run_log(path: Path) -> Iterable[dict[str, Any]]:
    records = list(_read_jsonl(path))
    source_quality = _source_quality_from_records(records)
    for record in records:
        decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
        metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
        search = metadata.get("search") if isinstance(metadata.get("search"), dict) else None
        if search is None:
            continue
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        combat = state.get("combat") if isinstance(state.get("combat"), dict) else {}
        if not combat:
            continue
        initial_loss = _optional_float(search.get("initial_loss"))
        projected_loss = _optional_float(search.get("projected_loss"))
        if initial_loss is None or projected_loss is None:
            continue
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hand = combat.get("hand_cards") if isinstance(combat.get("hand_cards"), list) else []
        monsters = combat.get("monsters") if isinstance(combat.get("monsters"), list) else []
        current_hp = _numeric(player.get("current_hp", state.get("current_hp")))
        max_hp = _numeric(player.get("max_hp", state.get("max_hp")))
        row = {
            "source_log": str(path),
            "step": record.get("step"),
            "character": state.get("class"),
            "ascension": state.get("ascension_level"),
            "floor": state.get("floor"),
            "act": state.get("act"),
            "turn": combat.get("turn"),
            "hp_ratio": current_hp / max(max_hp, 1.0) if max_hp else _numeric(state.get("current_hp")) / max(_numeric(state.get("max_hp")), 1.0),
            "current_hp": current_hp,
            "max_hp": max_hp,
            "current_block": player.get("block"),
            "current_energy": player.get("current_energy"),
            "incoming": combat.get("incoming_damage"),
            "hand_size": len(hand),
            "hand_ids": [_card_identity(card) for card in hand],
            "hand_names": [_card_name(card) for card in hand],
            "playable_count": sum(1 for card in hand if not isinstance(card, dict) or card.get("is_playable", True)),
            "enemy_count": len(monsters),
            "enemy_ids": [
                monster.get("id") or monster.get("name")
                for monster in monsters
                if isinstance(monster, dict)
            ],
            "enemy_intents": [
                monster.get("intent") or monster.get("move")
                for monster in monsters
                if isinstance(monster, dict)
            ],
            "label_first_card_key": search.get("first_card_key"),
            "label_sequence_card_keys": list(search.get("sequence_card_keys") or []),
            "search_type": search.get("type"),
            "initial_loss": initial_loss,
            "projected_loss": projected_loss,
            "loss_delta": initial_loss - projected_loss,
            "kills": _numeric(search.get("kills")),
            "attacks_removed": _numeric(search.get("attacks_removed")),
            "retaliation_damage": _numeric(search.get("retaliation_damage")),
            "avoided_lethal": bool(search.get("avoided_lethal")),
        }
        row.update(source_quality)
        yield row


def _source_quality_from_shadow_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("source_validation_grade") is not None:
        return {
            "source_category": row.get("source_category", "unknown"),
            "source_reason": row.get("source_reason", "unknown"),
            "source_validation_grade": row.get("source_validation_grade", "unknown"),
            "source_validation_flags": list(row.get("source_validation_flags") or []),
            "source_recovered_actions": int(_numeric(row.get("source_recovered_actions"))),
            "source_failed_actions": int(_numeric(row.get("source_failed_actions"))),
            "source_has_recovered_action_race": bool(row.get("source_has_recovered_action_race")),
            "source_action_recovery_kinds": dict(row.get("source_action_recovery_kinds") or {}),
        }
    return {
        "source_category": row.get("source_category", "legacy_shadow"),
        "source_reason": row.get("source_reason", "missing_source_quality"),
        "source_validation_grade": "pristine",
        "source_validation_flags": [],
        "source_recovered_actions": 0,
        "source_failed_actions": 0,
        "source_has_recovered_action_race": False,
        "source_action_recovery_kinds": {},
    }


def _source_quality_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    action_results = [record for record in records if record.get("event") == "action_result"]
    recovered_actions = 0
    failed_actions = 0
    unrecovered_actions = 0
    recovery_kinds: dict[str, int] = {}
    for record in action_results:
        status = str(record.get("action_status") or "")
        recovered = bool(record.get("recovered"))
        if status in {"recoverable_error", "preflight_mismatch"}:
            recovered_actions += 1
            kind = _action_recovery_kind(record)
            recovery_kinds[kind] = recovery_kinds.get(kind, 0) + 1
            if not recovered:
                unrecovered_actions += 1
        elif status not in {"ok", ""}:
            failed_actions += 1
    failed_actions += sum(1 for record in records if record.get("event") == "error" and record.get("actions"))

    terminal_state = _latest_terminal_state(records)
    has_terminal = terminal_state is not None
    flags: list[str] = []
    if recovered_actions:
        flags.append("recovered_action_race")
    if terminal_state is not None:
        source = str((terminal_state.get("outcome") or {}).get("source") or "")
        if source.startswith("synthetic_"):
            flags.append("synthetic_terminal")
            flags.append(source)
    if any(record.get("event") == "synthetic_terminal_state" for record in records):
        flags.append("synthetic_terminal_state")

    if failed_actions or unrecovered_actions:
        category = "infra_blocked"
        reason = "failed_action" if failed_actions else "unrecovered_action_race"
        grade = "infra_blocked"
        flags.append("infra_blocked")
    elif not has_terminal:
        category = "diagnostic_excluded"
        reason = "no_terminal_outcome"
        grade = "diagnostic"
        flags.extend(["diagnostic_excluded", "no_terminal_outcome"])
    elif flags:
        category = "clean_trainable"
        reason = "completed_clean"
        grade = "usable_with_recoveries"
    else:
        category = "clean_trainable"
        reason = "completed_clean"
        grade = "pristine"

    return {
        "source_category": category,
        "source_reason": reason,
        "source_validation_grade": grade,
        "source_validation_flags": _dedupe(flags),
        "source_recovered_actions": recovered_actions,
        "source_failed_actions": failed_actions,
        "source_has_recovered_action_race": recovered_actions > 0,
        "source_action_recovery_kinds": dict(sorted(recovery_kinds.items())),
    }


def _source_quality_weight(
    row: dict[str, Any],
    *,
    quality_policy: str,
    usable_weight: float,
    diagnostic_weight: float,
    infra_weight: float,
) -> float:
    grade = str(row.get("source_validation_grade") or "unknown")
    if quality_policy == "pristine":
        return 1.0 if grade == "pristine" else 0.0
    if grade == "pristine":
        return 1.0
    if grade == "usable_with_recoveries":
        return max(0.0, usable_weight)
    if grade == "diagnostic":
        return max(0.0, diagnostic_weight)
    if grade == "infra_blocked":
        return max(0.0, infra_weight)
    return 0.0


def _quality_counts(examples: list[CombatValueExample]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for example in examples:
        grade = str(example.row.get("source_validation_grade") or "unknown")
        counts[grade] = counts.get(grade, 0) + 1
    return dict(sorted(counts.items()))


def _weighted_loss(losses: Any, weights: Any, torch: Any) -> Any:
    total_weight = torch.clamp(weights.sum(), min=1e-8)
    return (losses * weights).sum() / total_weight


def _latest_terminal_state(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(records):
        state = record.get("state")
        if not isinstance(state, dict):
            continue
        if state.get("outcome") is not None or state.get("screen_type") == "GAME_OVER":
            return state
    return None


def _action_recovery_kind(record: dict[str, Any]) -> str:
    text = f"{record.get('last_error') or ''} {record.get('rewrite_reason') or ''}".lower()
    if "stale targeted use_potion" in text:
        return "stale_potion_target_index"
    if "stale target_index" in text and "for use_potion" in text:
        return "stale_potion_target_index"
    if "stale use_potion" in text or "potion_slot" in text:
        return "stale_potion_slot"
    if "stale target_index" in text:
        return "stale_target_index"
    if "grid" in text and "proceed" in text:
        return "grid_confirm_to_proceed"
    if "hand select" in text:
        return "hand_select_to_choose"
    if "unavailable action" in text or "preflight unavailable" in text:
        return "unavailable_action"
    status = str(record.get("action_status") or "").strip()
    return status or "unknown_action_race"


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _build_feature_spec(examples: list[CombatValueExample], *, min_categorical_count: int) -> dict[str, Any]:
    numeric_values = {feature: [_numeric(example.row.get(feature)) for example in examples] for feature in NUMERIC_FEATURES}
    token_counts: dict[str, int] = {}
    for example in examples:
        for token in _row_tokens(example.row):
            token_counts[token] = token_counts.get(token, 0) + 1
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


def _vectorize(row: dict[str, Any], feature_spec: dict[str, Any]) -> list[float]:
    vector: list[float] = []
    for feature in feature_spec["numeric_features"]:
        scale = float(feature_spec["numeric_scales"].get(feature) or 1.0)
        vector.append((_numeric(row.get(feature)) - float(feature_spec["numeric_means"].get(feature, 0.0))) / scale)
    categorical = [0.0] * len(feature_spec["categorical_vocab"])
    for token in _row_tokens(row):
        index = feature_spec["categorical_vocab"].get(token)
        if index is not None:
            categorical[index] = 1.0
    return vector + categorical


def _row_tokens(row: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    first = _label_first_card_key(row)
    if first:
        tokens.append(f"first:{first}")
    for card in row.get("label_sequence_card_keys") or []:
        key = combat_card_key(card)
        if key:
            tokens.append(f"sequence:{key}")
    for card in row.get("hand_ids") or row.get("hand_names") or []:
        key = combat_card_key(card)
        if key:
            tokens.append(f"hand:{key}")
    for enemy in row.get("enemy_ids") or []:
        key = combat_card_key(enemy)
        if key:
            tokens.append(f"enemy:{key}")
    for context in combat_search_context_keys(row):
        tokens.append(f"context:{context}")
    return tokens


def _label_first_card_key(row: dict[str, Any]) -> str:
    key = combat_card_key(row.get("label_first_card_key"))
    if key:
        return key
    sequence = row.get("label_sequence_card_keys")
    if isinstance(sequence, list) and sequence:
        return combat_card_key(sequence[0])
    return ""


def _label_value(row: dict[str, Any]) -> float:
    initial_loss = _numeric(row.get("initial_loss"))
    projected_loss = _numeric(row.get("projected_loss"))
    loss_delta = row.get("loss_delta")
    if loss_delta is None:
        loss_delta = initial_loss - projected_loss
    reduction_ratio = max(0.0, min(1.0, _numeric(loss_delta) / max(initial_loss, 1.0)))
    value = 1.0 + reduction_ratio
    value += min(3.0, _numeric(row.get("attacks_removed"))) * 0.25
    value += min(3.0, _numeric(row.get("kills"))) * 0.2
    value += min(3.0, _numeric(row.get("retaliation_damage")) / 8.0) * 0.25
    if row.get("avoided_lethal"):
        value += 0.5
    return round(value, 4)


def _train_val_split(count: int, *, seed: int) -> tuple[list[int], list[int]]:
    indices = list(range(count))
    random.Random(seed).shuffle(indices)
    if count < 5:
        return indices, []
    validation_count = max(1, int(round(count * 0.2)))
    return indices[validation_count:], indices[:validation_count]


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _card_identity(card: Any) -> Any:
    if isinstance(card, dict):
        return card.get("id") or card.get("name")
    return card


def _card_name(card: Any) -> Any:
    if isinstance(card, dict):
        return card.get("name") or card.get("id")
    return card


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float], *, fallback: float) -> float:
    if len(values) < 2:
        return fallback
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(max(variance, 0.0))
    return std if std > 1e-8 else fallback


def _select_device(torch: Any, device_name: str) -> Any:
    if device_name == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("CUDA device requested, but torch.cuda.is_available() is false.")
        return torch.device("cuda")
    if device_name == "auto" and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


def _seed_everything(seed: int, torch: Any) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _torch() -> Any:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised only when the optional dependency is absent.
        raise SystemExit(
            "PyTorch is required for combat value training. Install a CUDA build, e.g. "
            "`python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128`."
        ) from exc
    return torch


if __name__ == "__main__":
    raise SystemExit(main())
