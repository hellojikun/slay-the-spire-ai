"""Static game-knowledge tables for feature extraction.

These tables are factual priors used to enrich our own run logs. They are not
training labels: labels still come from clean completed game logs.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_KNOWLEDGE_DIR = Path("data") / "static_knowledge"
REQUIRED_FILES = ("cards.json", "monsters.json", "potions.json")


@dataclass(frozen=True)
class StaticKnowledge:
    cards: dict[str, dict[str, Any]]
    monsters: dict[str, dict[str, Any]]
    potions: dict[str, dict[str, Any]]
    source_counts: dict[str, int]

    @classmethod
    def load(cls, root: Path = DEFAULT_KNOWLEDGE_DIR) -> "StaticKnowledge":
        root = Path(root)
        raw_cards = _load_table(root / "cards.json", "cards")
        raw_monsters = _load_table(root / "monsters.json", "monsters")
        raw_potions = _load_table(root / "potions.json", "potions")
        cards = _index_rows(raw_cards, required=("id", "name", "type", "tags"))
        monsters = _index_rows(raw_monsters, required=("id", "name", "tags"))
        potions = _index_rows(raw_potions, required=("id", "name", "roles"))
        return cls(
            cards=cards,
            monsters=monsters,
            potions=potions,
            source_counts={
                "cards": len(raw_cards),
                "monsters": len(raw_monsters),
                "potions": len(raw_potions),
            },
        )

    def card_for(self, card: Any) -> dict[str, Any] | None:
        return _lookup(self.cards, _entity_keys(card))

    def potion_for(self, potion: Any) -> dict[str, Any] | None:
        return _lookup(self.potions, _entity_keys(potion))

    def monster_for(self, monster: Any) -> dict[str, Any] | None:
        return _lookup(self.monsters, _entity_keys(monster))

    def deck_features(self, deck: Any) -> dict[str, Any]:
        items = deck if isinstance(deck, list) else []
        features: dict[str, Any] = {
            "deck_known_cards": 0,
            "deck_unknown_cards": 0,
            "deck_total_base_damage": 0,
            "deck_total_base_block": 0,
        }
        type_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for item in items:
            card = self.card_for(item)
            if card is None:
                features["deck_unknown_cards"] += 1
                continue
            features["deck_known_cards"] += 1
            card_type = str(card.get("type") or "UNKNOWN").lower()
            type_counts[card_type] = type_counts.get(card_type, 0) + 1
            for tag in _string_list(card.get("tags")):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            values = card.get("values") if isinstance(card.get("values"), dict) else {}
            features["deck_total_base_damage"] += _safe_int(values.get("base_damage"))
            features["deck_total_base_block"] += _safe_int(values.get("base_block"))
        for card_type, count in sorted(type_counts.items()):
            features[f"deck_{card_type}_cards"] = count
        for tag, count in sorted(tag_counts.items()):
            features[f"deck_tag_{tag}"] = count
        return features

    def potion_features(self, potions: Any) -> dict[str, Any]:
        items = potions if isinstance(potions, list) else []
        features: dict[str, Any] = {
            "potion_known_count": 0,
            "potion_unknown_count": 0,
            "potion_block_value": 0,
            "potion_damage_value": 0,
            "potion_energy_value": 0,
            "has_liquid_memories": False,
        }
        role_counts: dict[str, int] = {}
        for item in items:
            potion = self.potion_for(item)
            if potion is None:
                features["potion_unknown_count"] += 1
                continue
            features["potion_known_count"] += 1
            if _norm(potion.get("id")) == "liquidmemories":
                features["has_liquid_memories"] = True
            for role in _string_list(potion.get("roles")):
                role_counts[role] = role_counts.get(role, 0) + 1
            values = potion.get("values") if isinstance(potion.get("values"), dict) else {}
            features["potion_block_value"] += _safe_int(values.get("block"))
            features["potion_damage_value"] += _safe_int(values.get("damage"))
            features["potion_energy_value"] += _safe_int(values.get("energy"))
        for role, count in sorted(role_counts.items()):
            features[f"potion_role_{role}"] = count
        return features

    def monster_features(self, monsters: Any) -> dict[str, Any]:
        items = monsters if isinstance(monsters, list) else []
        features: dict[str, Any] = {
            "enemy_known_count": 0,
            "enemy_unknown_count": 0,
            "enemy_max_expected_attack": 0,
            "enemy_boss_count": 0,
            "enemy_elite_count": 0,
            "enemy_multi_hit_count": 0,
        }
        tag_counts: dict[str, int] = {}
        for item in items:
            monster = self.monster_for(item)
            if monster is None:
                features["enemy_unknown_count"] += 1
                continue
            features["enemy_known_count"] += 1
            if bool(monster.get("boss")):
                features["enemy_boss_count"] += 1
            if bool(monster.get("elite")):
                features["enemy_elite_count"] += 1
            if bool(monster.get("multi_hit")):
                features["enemy_multi_hit_count"] += 1
            values = monster.get("values") if isinstance(monster.get("values"), dict) else {}
            features["enemy_max_expected_attack"] = max(
                features["enemy_max_expected_attack"],
                _safe_int(values.get("max_expected_attack")),
            )
            for tag in _string_list(monster.get("tags")):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        for tag, count in sorted(tag_counts.items()):
            features[f"enemy_tag_{tag}"] = count
        return features


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize static Slay the Spire knowledge tables.")
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    args = parser.parse_args(argv)
    knowledge = StaticKnowledge.load(args.knowledge_dir)
    print(
        "Loaded static knowledge: "
        f"{knowledge.source_counts['cards']} cards, "
        f"{knowledge.source_counts['monsters']} monsters, "
        f"{knowledge.source_counts['potions']} potions."
    )
    return 0


def _load_table(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing static knowledge file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a list at key {key!r}")
    return rows


def _index_rows(rows: Iterable[dict[str, Any]], *, required: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Static knowledge rows must be objects")
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"Static knowledge row missing required keys {missing}: {row}")
        keys = set(_entity_keys(row))
        for alias in _string_list(row.get("aliases")):
            keys.add(_norm(alias))
        keys.discard("")
        for key in keys:
            existing = index.get(key)
            if existing is not None and existing is not row:
                raise ValueError(f"Duplicate static knowledge key {key!r}")
            index[key] = row
    return index


def _lookup(index: dict[str, dict[str, Any]], keys: Iterable[str]) -> dict[str, Any] | None:
    for key in keys:
        if key in index:
            return index[key]
    return None


def _entity_keys(entity: Any) -> list[str]:
    if isinstance(entity, dict):
        return [_norm(entity.get("id")), _norm(entity.get("name"))]
    return [_norm(entity)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower().replace(" ", "_") for item in value if str(item).strip()]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
