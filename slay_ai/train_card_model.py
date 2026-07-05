"""Train a simple card-value model from completed JSONL run logs.

The default backend is dependency-free statistical learning. If PyTorch is
installed, this command records CUDA availability in model metadata; the model
format stays JSON so the runtime policy remains lightweight.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .memory import normalize_card_name
from .model import MODEL_PATH, CardValueModel


@dataclass
class CardExample:
    character: str
    ascension: int
    floor: int
    picked: str
    options: list[str]
    victory: bool
    final_floor: int


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train card-value deltas from ai_runs JSONL logs.")
    parser.add_argument("logs", nargs="*", type=Path, default=[Path("ai_runs")])
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--min-count", type=int, default=1)
    parser.add_argument("--max-delta", type=float, default=10.0)
    parser.add_argument("--backend", choices=["auto", "stats"], default="auto")
    args = parser.parse_args(argv)

    examples = load_examples(args.logs)
    model = train_stats_model(examples, args.model_path, min_count=args.min_count, max_delta=args.max_delta)
    model.metadata.update(hardware_metadata(args.backend))
    model.save()
    print(f"Loaded {len(examples)} card-pick examples.")
    print(f"Wrote {len(model.card_deltas)} card deltas to {model.path}.")
    return 0


def load_examples(paths: Iterable[Path]) -> list[CardExample]:
    examples: list[CardExample] = []
    for path in _iter_log_files(paths):
        examples.extend(_examples_from_log(path))
    return examples


def train_stats_model(
    examples: list[CardExample],
    model_path: Path = MODEL_PATH,
    *,
    min_count: int = 1,
    max_delta: float = 10.0,
) -> CardValueModel:
    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)

    for example in examples:
        reward = _reward(example)
        key = f"{example.character}::{normalize_card_name(example.picked)}"
        totals[key] += reward
        counts[key] += 1

        opportunity_penalty = max(-1.0, min(1.0, -reward * 0.08))
        for option in example.options:
            normalized = normalize_card_name(option)
            if normalized == normalize_card_name(example.picked):
                continue
            alt_key = f"{example.character}::{normalized}"
            totals[alt_key] += opportunity_penalty
            counts[alt_key] += 1

    deltas: dict[str, float] = {}
    for key, total in totals.items():
        count = counts[key]
        if count < min_count:
            continue
        shrink = math.sqrt(count) / (math.sqrt(count) + 3.0)
        delta = (total / count) * shrink
        deltas[key] = round(max(-max_delta, min(max_delta, delta)), 3)

    return CardValueModel(
        path=model_path,
        card_deltas=deltas,
        metadata={
            "version": 1,
            "trained_at": datetime.now().isoformat(timespec="seconds"),
            "backend": "stats",
            "examples": len(examples),
            "min_count": min_count,
            "max_delta": max_delta,
        },
    )


def _examples_from_log(path: Path) -> list[CardExample]:
    pending: list[dict[str, Any]] = []
    latest_state: dict[str, Any] = {}
    outcome: dict[str, Any] | None = None

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            state = record.get("state") or {}
            latest_state = state or latest_state
            decision = record.get("decision") or {}
            picked = decision.get("learn_card_pick")
            options = [
                card.get("id") or card.get("name")
                for card in state.get("card_reward_options", [])
                if card.get("id") or card.get("name")
            ]
            if picked and options:
                pending.append(
                    {
                        "character": state.get("class"),
                        "ascension": state.get("ascension_level"),
                        "floor": state.get("floor"),
                        "picked": picked,
                        "options": options,
                    }
                )
            if "outcome" in state:
                outcome = state["outcome"]

    if not outcome:
        return []
    final_floor = int(latest_state.get("floor") or 0)
    victory = bool(outcome.get("victory"))
    examples: list[CardExample] = []
    for item in pending:
        examples.append(
            CardExample(
                character=str(item.get("character") or "UNKNOWN").upper(),
                ascension=int(item.get("ascension") or 0),
                floor=int(item.get("floor") or 0),
                picked=str(item["picked"]),
                options=[str(option) for option in item["options"]],
                victory=victory,
                final_floor=final_floor,
            )
        )
    return examples


def _reward(example: CardExample) -> float:
    if example.victory:
        return 8.0
    progress = max(0.0, min(1.0, example.final_floor / 50.0))
    if example.final_floor < 17:
        return -5.0 + progress
    if example.final_floor < 34:
        return -2.5 + progress * 2.0
    return 0.5 + progress * 2.0


def _iter_log_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.glob("*.jsonl"))
        elif path.exists():
            yield path


def hardware_metadata(backend: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"requested_backend": backend}
    try:
        import torch  # type: ignore
    except Exception:
        metadata.update({"torch_available": False, "cuda_available": False})
        return metadata
    metadata.update(
        {
            "torch_available": True,
            "torch_version": getattr(torch, "__version__", None),
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device_count": int(torch.cuda.device_count()),
            "cuda_devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
        }
    )
    return metadata


if __name__ == "__main__":
    raise SystemExit(main())
