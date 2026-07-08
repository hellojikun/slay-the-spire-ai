"""Optional learned card-value model used by strategy memory."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "card_value_model.json"
DECK_QUALITY_MODEL_PATH = ROOT / "models" / "deck_quality_model.json"
POTION_TEMPO_MODEL_PATH = ROOT / "models" / "potion_tempo_model.json"
ROUTE_RISK_MODEL_PATH = ROOT / "models" / "route_risk_model.json"
COMBAT_SEARCH_MODEL_PATH = ROOT / "models" / "combat_search_model.json"
COMBAT_VALUE_MODEL_PATH = ROOT / "models" / "combat_value_model.pt"
DECISION_MULTITASK_MODEL_PATH = ROOT / "models" / "decision_multitask_model.pt"


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


@dataclass
class DeckQualityModel:
    path: Path = DECK_QUALITY_MODEL_PATH
    intercept: float = 0.0
    feature_weights: dict[str, float] = field(default_factory=dict)
    feature_means: dict[str, float] = field(default_factory=dict)
    feature_scales: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = DECK_QUALITY_MODEL_PATH) -> "DeckQualityModel":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            intercept=float(data.get("intercept", 0.0)),
            feature_weights={str(key): float(value) for key, value in data.get("feature_weights", {}).items()},
            feature_means={str(key): float(value) for key, value in data.get("feature_means", {}).items()},
            feature_scales={str(key): float(value) for key, value in data.get("feature_scales", {}).items()},
            metadata=data.get("metadata", {}),
        )

    def score_row(self, row: dict[str, Any]) -> float:
        score = self.intercept
        for feature, weight in self.feature_weights.items():
            scale = self.feature_scales.get(feature, 1.0) or 1.0
            score += weight * ((_numeric(row.get(feature)) - self.feature_means.get(feature, 0.0)) / scale)
        return round(_sigmoid(score), 4)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self.metadata,
            "intercept": self.intercept,
            "feature_weights": dict(sorted(self.feature_weights.items())),
            "feature_means": dict(sorted(self.feature_means.items())),
            "feature_scales": dict(sorted(self.feature_scales.items())),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class PotionTempoModel:
    path: Path = POTION_TEMPO_MODEL_PATH
    intercept: float = 0.0
    feature_weights: dict[str, float] = field(default_factory=dict)
    feature_means: dict[str, float] = field(default_factory=dict)
    feature_scales: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = POTION_TEMPO_MODEL_PATH) -> "PotionTempoModel":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            intercept=float(data.get("intercept", 0.0)),
            feature_weights={str(key): float(value) for key, value in data.get("feature_weights", {}).items()},
            feature_means={str(key): float(value) for key, value in data.get("feature_means", {}).items()},
            feature_scales={str(key): float(value) for key, value in data.get("feature_scales", {}).items()},
            metadata=data.get("metadata", {}),
        )

    def score_row(self, row: dict[str, Any]) -> float:
        score = self.intercept
        for feature, weight in self.feature_weights.items():
            scale = self.feature_scales.get(feature, 1.0) or 1.0
            score += weight * ((_numeric(row.get(feature)) - self.feature_means.get(feature, 0.0)) / scale)
        return round(_sigmoid(score), 4)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self.metadata,
            "intercept": self.intercept,
            "feature_weights": dict(sorted(self.feature_weights.items())),
            "feature_means": dict(sorted(self.feature_means.items())),
            "feature_scales": dict(sorted(self.feature_scales.items())),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class RouteRiskModel:
    path: Path = ROUTE_RISK_MODEL_PATH
    intercept: float = 0.0
    feature_weights: dict[str, float] = field(default_factory=dict)
    feature_means: dict[str, float] = field(default_factory=dict)
    feature_scales: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = ROUTE_RISK_MODEL_PATH) -> "RouteRiskModel":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            intercept=float(data.get("intercept", 0.0)),
            feature_weights={str(key): float(value) for key, value in data.get("feature_weights", {}).items()},
            feature_means={str(key): float(value) for key, value in data.get("feature_means", {}).items()},
            feature_scales={str(key): float(value) for key, value in data.get("feature_scales", {}).items()},
            metadata=data.get("metadata", {}),
        )

    def score_row(self, row: dict[str, Any]) -> float:
        score = self.intercept
        for feature, weight in self.feature_weights.items():
            scale = self.feature_scales.get(feature, 1.0) or 1.0
            score += weight * ((_numeric(row.get(feature)) - self.feature_means.get(feature, 0.0)) / scale)
        return round(_sigmoid(score), 4)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self.metadata,
            "intercept": self.intercept,
            "feature_weights": dict(sorted(self.feature_weights.items())),
            "feature_means": dict(sorted(self.feature_means.items())),
            "feature_scales": dict(sorted(self.feature_scales.items())),
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class CombatSearchModel:
    path: Path = COMBAT_SEARCH_MODEL_PATH
    card_priors: dict[str, float] = field(default_factory=dict)
    context_card_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = COMBAT_SEARCH_MODEL_PATH) -> "CombatSearchModel":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            path=path,
            card_priors={combat_card_key(key): float(value) for key, value in data.get("card_priors", {}).items()},
            context_card_scores={
                str(context): {combat_card_key(key): float(value) for key, value in scores.items()}
                for context, scores in data.get("context_card_scores", {}).items()
                if isinstance(scores, dict)
            },
            metadata=data.get("metadata", {}),
        )

    def score_card(self, card_key: Any, row: dict[str, Any] | None = None) -> float:
        normalized = combat_card_key(card_key)
        score = self.card_priors.get(normalized, 0.0)
        if row is not None:
            for context in combat_search_context_keys(row):
                score += self.context_card_scores.get(context, {}).get(normalized, 0.0)
        return round(score, 4)

    def rank_hand(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        hand = row.get("hand_ids") or row.get("hand_names") or []
        if not isinstance(hand, list):
            return []
        ranked: list[dict[str, Any]] = []
        for index, card in enumerate(hand, start=1):
            key = combat_card_key(card)
            if not key:
                continue
            ranked.append(
                {
                    "card_index": index,
                    "card_key": key,
                    "card_id": card,
                    "score": self.score_card(key, row),
                }
            )
        return sorted(ranked, key=lambda item: (-item["score"], item["card_index"], item["card_key"]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "metadata": self.metadata,
            "card_priors": dict(sorted(self.card_priors.items())),
            "context_card_scores": {
                context: dict(sorted(scores.items()))
                for context, scores in sorted(self.context_card_scores.items())
            },
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def combat_card_key(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("name")
    key = str(value or "").strip()
    if "+" in key:
        key = key.split("+", 1)[0].strip()
    return "".join(ch for ch in key.lower() if ch.isalnum())


def combat_search_context_keys(row: dict[str, Any]) -> list[str]:
    contexts: list[str] = []
    if row.get("act") is not None:
        contexts.append(f"act:{int(_numeric(row.get('act')))}")
    if row.get("current_energy") is not None:
        contexts.append(f"energy:{int(_numeric(row.get('current_energy')))}")
    if row.get("turn") is not None:
        turn = int(_numeric(row.get("turn")))
        if turn <= 1:
            contexts.append("turn:first")
        elif turn >= 4:
            contexts.append("turn:late")
        else:
            contexts.append("turn:early")
    hp_ratio = _numeric(row.get("hp_ratio"))
    if hp_ratio > 0:
        if hp_ratio <= 0.3:
            contexts.append("hp:low")
        elif hp_ratio >= 0.7:
            contexts.append("hp:high")
        else:
            contexts.append("hp:mid")
    incoming = _numeric(row.get("incoming"))
    block = _numeric(row.get("current_block"))
    current_hp = _numeric(row.get("current_hp"))
    pressure = max(0.0, incoming - block)
    if pressure <= 0:
        contexts.append("incoming:covered")
    elif current_hp > 0 and pressure >= current_hp:
        contexts.append("incoming:lethal")
    elif current_hp > 0 and pressure >= current_hp * 0.5:
        contexts.append("incoming:danger")
    elif pressure > 0:
        contexts.append("incoming:chip")
    enemy_count = int(_numeric(row.get("enemy_count")))
    if enemy_count >= 2:
        contexts.append("enemy:multi")
    elif enemy_count == 1:
        contexts.append("enemy:single")
    if _numeric(row.get("enemy_boss_count")) > 0:
        contexts.append("enemy:boss")
    for key, value in sorted(row.items()):
        if key.startswith("boss_search_hint_") and _numeric(value) > 0:
            contexts.append(f"boss_search_hint:{key.removeprefix('boss_search_hint_')}")
    enemy_ids = row.get("enemy_ids")
    if isinstance(enemy_ids, list):
        for enemy_id in enemy_ids[:2]:
            key = combat_card_key(enemy_id)
            if key:
                contexts.append(f"enemy_id:{key}")
    return contexts


def _numeric(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sigmoid(value: float) -> float:
    if value >= 35:
        return 1.0
    if value <= -35:
        return 0.0
    return 1.0 / (1.0 + pow(2.718281828459045, -value))
