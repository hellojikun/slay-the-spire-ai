"""Persistent strategy memory for the growing AI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import MODEL_PATH, CardValueModel


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MEMORY = ROOT / "data" / "default_memory.json"
LEARNED_MEMORY = ROOT / "data" / "learned_memory.json"


def normalize_card_name(name: str) -> str:
    normalized = name.replace("+", "").strip()
    for suffix in ("_R", "_G", "_B", "_P"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


@dataclass
class StrategyMemory:
    base: dict[str, Any]
    learned_path: Path = LEARNED_MEMORY
    value_model_path: Path = MODEL_PATH
    learned: dict[str, Any] = field(default_factory=dict)
    value_model: CardValueModel = field(default_factory=CardValueModel)

    @classmethod
    def load(
        cls,
        base_path: Path = DEFAULT_MEMORY,
        learned_path: Path = LEARNED_MEMORY,
        value_model_path: Path = MODEL_PATH,
    ) -> "StrategyMemory":
        base = json.loads(base_path.read_text(encoding="utf-8"))
        if learned_path.exists():
            learned = json.loads(learned_path.read_text(encoding="utf-8"))
        else:
            learned = {
                "version": 1,
                "runs": {"victories": 0, "deaths": 0, "total": 0},
                "card_picks": {},
                "recent_outcomes": [],
            }
        return cls(
            base=base,
            learned_path=learned_path,
            value_model_path=value_model_path,
            learned=learned,
            value_model=CardValueModel.load(value_model_path),
        )

    def card_score(self, name: str, character: str = "IRONCLAD") -> float:
        return self.card_score_breakdown(name, character)["total"]

    def card_score_breakdown(self, name: str, character: str = "IRONCLAD") -> dict[str, Any]:
        profile = self.base["character_profiles"].get(character, {})
        normalized = normalize_card_name(name)
        score = float(profile.get("card_scores", {}).get(normalized, 30))
        learned_delta = self.learned.get("card_picks", {}).get(normalized, {}).get("delta", 0)
        model_delta = self.value_model.score_delta(normalized, character)
        return {
            "card": normalized,
            "base_score": score,
            "learned_delta": float(learned_delta),
            "model_delta": float(model_delta),
            "total": score + float(learned_delta) + float(model_delta),
        }

    def reload_value_model(self) -> None:
        self.value_model = CardValueModel.load(self.value_model_path)

    def upgrade_score(self, name: str, character: str = "IRONCLAD") -> float:
        profile = self.base["character_profiles"].get(character, {})
        return profile.get("upgrade_scores", {}).get(normalize_card_name(name), self.card_score(name, character) * 0.6)

    def relic_score(self, name: str) -> float:
        return self.base.get("relic_scores", {}).get(name, 50)

    def route_score(self, symbol: str, hp_ratio: float, floor: int) -> float:
        scores = self.base.get("route_scores", {})
        score = scores.get(symbol, 30)
        if symbol == "E":
            if hp_ratio < 0.45:
                score -= 35
            elif hp_ratio < 0.62:
                score -= 35
            elif floor <= 8:
                score += 10
        if symbol == "R" and hp_ratio < 0.5:
            score += 30
        if symbol == "M" and floor <= 5:
            score += 10
        if symbol == "$" and floor <= 6:
            score -= 12
        return score

    def record_card_pick(self, card_name: str) -> None:
        picks = self.learned.setdefault("card_picks", {})
        item = picks.setdefault(normalize_card_name(card_name), {"picked": 0, "delta": 0})
        item["picked"] += 1

    def record_outcome(self, state: dict[str, Any], episode_picks: list[str] | None = None) -> None:
        game = state.get("game_state", {})
        screen_state = game.get("screen_state", {})
        self.record_outcome_summary(
            victory=bool(screen_state.get("victory")),
            floor=int(game.get("floor") or 0),
            score=screen_state.get("score"),
            character=game.get("class"),
            ascension=game.get("ascension_level"),
            episode_picks=episode_picks,
        )

    def record_outcome_summary(
        self,
        *,
        victory: bool,
        floor: int,
        score: int | None = None,
        character: str | None = None,
        ascension: int | None = None,
        episode_picks: list[str] | None = None,
    ) -> None:
        runs = self.learned.setdefault("runs", {"victories": 0, "deaths": 0, "total": 0})
        runs["total"] = runs.get("total", 0) + 1
        if victory:
            runs["victories"] = runs.get("victories", 0) + 1
        else:
            runs["deaths"] = runs.get("deaths", 0) + 1
        if episode_picks:
            self._adjust_card_deltas(episode_picks, victory=victory, floor=floor)
        outcome = {
            "time": datetime.now(timezone.utc).isoformat(),
            "victory": victory,
            "score": score,
            "floor": floor,
            "class": character,
            "ascension": ascension,
        }
        recent = self.learned.setdefault("recent_outcomes", [])
        recent.append(outcome)
        del recent[:-20]
        self.save()

    def save(self) -> None:
        self.learned_path.parent.mkdir(parents=True, exist_ok=True)
        self.learned_path.write_text(
            json.dumps(self.learned, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _adjust_card_deltas(self, episode_picks: list[str], victory: bool, floor: int) -> None:
        if victory:
            adjustment = 1.0
            result_key = "wins"
        elif floor < 17:
            adjustment = -0.6
            result_key = "early_deaths"
        elif floor < 34:
            adjustment = -0.3
            result_key = "mid_deaths"
        else:
            adjustment = 0.15
            result_key = "late_runs"

        picks = self.learned.setdefault("card_picks", {})
        for raw_name in episode_picks:
            name = normalize_card_name(raw_name)
            item = picks.setdefault(name, {"picked": 0, "delta": 0})
            item[result_key] = item.get(result_key, 0) + 1
            item["delta"] = max(-12, min(12, round(float(item.get("delta", 0)) + adjustment, 2)))
