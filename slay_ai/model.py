"""Optional learned card-value model used by strategy memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "card_value_model.json"


@dataclass
class CardValueModel:
    path: Path = MODEL_PATH
    card_deltas: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "CardValueModel":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            card_deltas={str(key): float(value) for key, value in data.get("card_deltas", {}).items()},
            metadata=data.get("metadata", {}),
        )

    def score_delta(self, card_name: str, character: str) -> float:
        exact = f"{character.upper()}::{card_name}"
        if exact in self.card_deltas:
            return self.card_deltas[exact]
        return self.card_deltas.get(f"*::{card_name}", 0.0)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self.metadata,
            "card_deltas": dict(sorted(self.card_deltas.items())),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
