"""Import complete factual STS1 entity coverage from Spire Archive.

The importer augments our hand-curated static knowledge tables without
overwriting tactical tags or learned values. Spire Archive supplies broad
entity coverage and localization; our local tables remain the authority for
project-specific A0 feature engineering.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable


DEFAULT_SOURCE_DIR = Path("data") / "external" / "static_knowledge" / "spire_archive" / "data" / "sts1"
DEFAULT_KNOWLEDGE_DIR = Path("data") / "static_knowledge"
SOURCE_NAME = "Spire Archive STS1 parsed game data"
SOURCE_URL = "https://github.com/nkhoit/spire-archive"

CARD_CHARACTER = {
    "ironclad": "IRONCLAD",
    "silent": "SILENT",
    "defect": "DEFECT",
    "watcher": "WATCHER",
    "colorless": "COLORLESS",
    "curse": "CURSE",
}
MONSTER_SOURCE_ALIASES = {
    "SLAVERBLUE": ("Blue Slaver", "BlueSlaver"),
    "SLAVERRED": ("Red Slaver", "RedSlaver"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge Spire Archive STS1 data into local static knowledge.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing files.")
    args = parser.parse_args(argv)

    summary = import_spire_archive(args.source_dir, args.knowledge_dir, dry_run=args.dry_run)
    print(
        "spire_archive_import: "
        f"cards={summary['cards']['before']}->{summary['cards']['after']} "
        f"relics={summary['relics']['before']}->{summary['relics']['after']} "
        f"potions={summary['potions']['before']}->{summary['potions']['after']} "
        f"monsters={summary['monsters']['before']}->{summary['monsters']['after']} "
        f"bosses={summary['bosses']['before']}->{summary['bosses']['after']} "
        f"dry_run={dry_run_label(args.dry_run)}"
    )
    return 0


def import_spire_archive(source_dir: Path, knowledge_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    source_dir = Path(source_dir)
    knowledge_dir = Path(knowledge_dir)
    source = {
        "cards": _load_json(source_dir / "cards.json"),
        "relics": _load_json(source_dir / "relics.json"),
        "potions": _load_json(source_dir / "potions.json"),
        "monsters": _load_json(source_dir / "monsters.json"),
        "localization": _load_json(source_dir / "localization" / "zh.json"),
    }
    tables = {
        "cards": _load_table(knowledge_dir / "cards.json", "cards"),
        "relics": _load_table(knowledge_dir / "relics.json", "relics"),
        "potions": _load_table(knowledge_dir / "potions.json", "potions"),
        "monsters": _load_table(knowledge_dir / "monsters.json", "monsters"),
        "bosses": _load_table(knowledge_dir / "bosses.json", "bosses"),
    }
    before = {name: len(payload["rows"]) for name, payload in tables.items()}

    _merge_cards(tables["cards"]["rows"], source["cards"], source["localization"].get("cards", {}))
    _merge_relics(tables["relics"]["rows"], source["relics"], source["localization"].get("relics", {}))
    _merge_potions(tables["potions"]["rows"], source["potions"], source["localization"].get("potions", {}))
    _merge_monsters(tables["monsters"]["rows"], source["monsters"], source["localization"].get("monsters", {}))
    _merge_bosses(tables["bosses"]["rows"], source["monsters"], source["localization"].get("monsters", {}))

    for payload in tables.values():
        _drop_conflicting_aliases(payload["rows"])
    for payload in tables.values():
        _ensure_source(payload["payload"])

    summary = {
        name: {
            "before": before[name],
            "after": len(payload["rows"]),
            "added": len(payload["rows"]) - before[name],
        }
        for name, payload in tables.items()
    }
    if dry_run:
        return summary

    for filename, key in (
        ("cards.json", "cards"),
        ("relics.json", "relics"),
        ("potions.json", "potions"),
        ("monsters.json", "monsters"),
        ("bosses.json", "bosses"),
    ):
        payload = tables[key]["payload"]
        payload[key] = _sorted_rows(tables[key]["rows"])
        path = knowledge_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _merge_cards(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]], zh: dict[str, Any]) -> None:
    for source in source_rows:
        aliases = _source_aliases(source, zh)
        row = _find_existing(rows, source, aliases, character=CARD_CHARACTER.get(str(source.get("color") or "").lower()))
        if row is None:
            row = {
                "id": _card_runtime_id(source),
                "name": source.get("name") or source.get("id"),
                "character": CARD_CHARACTER.get(str(source.get("color") or "").lower(), str(source.get("color") or "").upper()),
                "type": _upper(source.get("type")),
                "rarity": _upper(source.get("rarity")),
                "cost": source.get("cost"),
                "tags": _card_tags(source),
                "values": _card_values(source),
            }
            rows.append(row)
        else:
            row.setdefault("character", CARD_CHARACTER.get(str(source.get("color") or "").lower()))
            row.setdefault("type", _upper(source.get("type")))
            row.setdefault("rarity", _upper(source.get("rarity")))
            row.setdefault("cost", source.get("cost"))
            row.setdefault("tags", [])
            row.setdefault("values", {})
            _merge_missing_values(row["values"], _card_values(source))
        _append_aliases(row, aliases)


def _merge_relics(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]], zh: dict[str, Any]) -> None:
    for source in source_rows:
        aliases = _source_aliases(source, zh)
        row = _find_existing(rows, source, aliases)
        if row is None:
            row = {
                "id": source.get("name") or source.get("id"),
                "name": source.get("name") or source.get("id"),
                "rarity": _upper(source.get("tier")),
                "tags": _relic_tags(source),
                "values": {},
            }
            rows.append(row)
        else:
            row.setdefault("rarity", _upper(source.get("tier")))
            row.setdefault("tags", [])
            row.setdefault("values", {})
        _append_aliases(row, aliases)


def _merge_potions(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]], zh: dict[str, Any]) -> None:
    for source in source_rows:
        aliases = _source_aliases(source, zh)
        aliases.append(_compact_name(source.get("name")))
        row = _find_existing(rows, source, aliases)
        if row is None:
            row = {
                "id": source.get("name") or source.get("id"),
                "name": source.get("name") or source.get("id"),
                "rarity": _upper(source.get("rarity")),
                "requires_target": bool(source.get("is_thrown")),
                "combat_only": True,
                "roles": _potion_roles(source),
                "values": {},
            }
            rows.append(row)
        else:
            row.setdefault("rarity", _upper(source.get("rarity")))
            row.setdefault("requires_target", bool(source.get("is_thrown")))
            row.setdefault("combat_only", True)
            row.setdefault("roles", [])
            row.setdefault("values", {})
        _append_aliases(row, aliases)


def _merge_monsters(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]], zh: dict[str, Any]) -> None:
    _remove_generic_source_duplicates(rows, id_value="Slaver", name_value="Slaver")
    for source in source_rows:
        aliases = _source_aliases(source, zh)
        aliases.extend(MONSTER_SOURCE_ALIASES.get(str(source.get("id") or ""), ()))
        row = _find_existing(rows, source, aliases)
        values = _monster_values(source)
        if row is None:
            row = {
                "id": _monster_runtime_id(source),
                "name": source.get("name") or source.get("id"),
                "act": _act_number(source.get("act")),
                "boss": str(source.get("type") or "").lower() == "boss",
                "elite": str(source.get("type") or "").lower() == "elite",
                "multi_hit": _monster_multi_hit(source),
                "tags": _monster_tags(source),
                "values": values,
            }
            rows.append(row)
        else:
            row.setdefault("act", _act_number(source.get("act")))
            row.setdefault("boss", str(source.get("type") or "").lower() == "boss")
            row.setdefault("elite", str(source.get("type") or "").lower() == "elite")
            row.setdefault("multi_hit", _monster_multi_hit(source))
            row.setdefault("tags", [])
            row.setdefault("values", {})
            _merge_missing_values(row["values"], values)
        _append_aliases(row, aliases)


def _merge_bosses(rows: list[dict[str, Any]], source_rows: list[dict[str, Any]], zh: dict[str, Any]) -> None:
    for source in source_rows:
        if str(source.get("type") or "").lower() != "boss":
            continue
        aliases = _source_aliases(source, zh)
        row = _find_existing(rows, source, aliases)
        if row is None:
            row = {
                "id": _monster_runtime_id(source),
                "name": source.get("name") or source.get("id"),
                "act": _act_number(source.get("act")),
                "mechanics": [],
                "search_hints": [],
                "deck_needs": [],
                "potion_needs": [],
                "tags": ["boss"],
                "values": _monster_values(source),
            }
            rows.append(row)
        else:
            row.setdefault("act", _act_number(source.get("act")))
            row.setdefault("mechanics", [])
            row.setdefault("search_hints", [])
            row.setdefault("deck_needs", [])
            row.setdefault("potion_needs", [])
            row.setdefault("tags", [])
            row.setdefault("values", {})
            _merge_missing_values(row["values"], _monster_values(source))
        _append_aliases(row, aliases)


def _find_existing(
    rows: list[dict[str, Any]],
    source: dict[str, Any],
    aliases: Iterable[str],
    *,
    character: str | None = None,
) -> dict[str, Any] | None:
    exact_keys = {_norm(source.get("id")), _norm(source.get("name"))}
    alias_keys = {_norm(alias) for alias in aliases}
    candidates: list[dict[str, Any]] = []
    for row in rows:
        row_keys = {_norm(row.get("id")), _norm(row.get("name"))}
        row_keys.update(_norm(alias) for alias in _list(row.get("aliases")))
        if row_keys & (exact_keys | alias_keys):
            candidates.append(row)
    if not candidates:
        return None
    if character:
        same_character = [
            row for row in candidates if _norm(row.get("character")) == _norm(character) or not row.get("character")
        ]
        if len(same_character) == 1:
            return same_character[0]
    if len(candidates) == 1:
        return candidates[0]
    for row in candidates:
        if _norm(row.get("id")) == _norm(source.get("id")):
            return row
    return None


def _source_aliases(source: dict[str, Any], localization: dict[str, Any]) -> list[str]:
    aliases = [source.get("id"), source.get("name")]
    localized = localization.get(str(source.get("id") or ""))
    if isinstance(localized, dict):
        aliases.append(localized.get("name"))
    return [alias for alias in aliases if isinstance(alias, str) and alias.strip()]


def _remove_generic_source_duplicates(rows: list[dict[str, Any]], *, id_value: str, name_value: str) -> None:
    rows[:] = [
        row
        for row in rows
        if not (_norm(row.get("id")) == _norm(id_value) and _norm(row.get("name")) == _norm(name_value))
    ]


def _append_aliases(row: dict[str, Any], aliases: Iterable[str]) -> None:
    existing = [alias for alias in _list(row.get("aliases")) if alias]
    seen = {_norm(row.get("id")), _norm(row.get("name"))}
    seen.update(_norm(alias) for alias in existing)
    for alias in aliases:
        key = _norm(alias)
        if not key or key in seen:
            continue
        existing.append(str(alias))
        seen.add(key)
    if existing:
        row["aliases"] = existing


def _drop_conflicting_aliases(rows: list[dict[str, Any]]) -> None:
    owner: dict[str, int] = {}
    blocked: set[str] = set()
    for index, row in enumerate(rows):
        for key in (_norm(row.get("id")), _norm(row.get("name"))):
            if not key:
                continue
            previous = owner.get(key)
            if previous is not None and previous != index:
                blocked.add(key)
            owner[key] = index

    for index, row in enumerate(rows):
        clean_aliases: list[str] = []
        for alias in _list(row.get("aliases")):
            key = _norm(alias)
            if not key or key in blocked:
                continue
            previous = owner.get(key)
            if previous is not None and previous != index:
                blocked.add(key)
                continue
            owner[key] = index
            clean_aliases.append(alias)
        if clean_aliases:
            row["aliases"] = clean_aliases
        else:
            row.pop("aliases", None)

    if not blocked:
        return
    for index, row in enumerate(rows):
        clean_aliases = []
        for alias in _list(row.get("aliases")):
            if _norm(alias) not in blocked:
                clean_aliases.append(alias)
        if clean_aliases:
            row["aliases"] = clean_aliases
        else:
            row.pop("aliases", None)


def _card_runtime_id(source: dict[str, Any]) -> str:
    source_id = str(source.get("id") or "")
    if source_id in {"STRIKE_R", "DEFEND_R", "STRIKE_G", "DEFEND_G", "STRIKE_B", "DEFEND_B", "STRIKE_P", "DEFEND_P"}:
        return source_id.title().replace("_", "_")
    return str(source.get("name") or source.get("id"))


def _monster_runtime_id(source: dict[str, Any]) -> str:
    name = str(source.get("name") or source.get("id") or "")
    return "".join(ch for ch in name if ch.isalnum())


def _card_tags(source: dict[str, Any]) -> list[str]:
    tags = [_lower(source.get("type"))]
    if source.get("damage") is not None:
        tags.append("damage")
    if source.get("block") is not None:
        tags.append("block")
    if bool(source.get("exhaust")):
        tags.append("exhaust")
    if bool(source.get("ethereal")):
        tags.append("ethereal")
    if bool(source.get("innate")):
        tags.append("innate")
    if source.get("cost") == 0:
        tags.append("zero_cost")
    return _dedupe(tag for tag in tags if tag)


def _card_values(source: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if source.get("damage") is not None:
        values["base_damage"] = source.get("damage")
        upgraded = _upgrade_number(source.get("upgrade"), "damage", source.get("damage"))
        if upgraded is not None:
            values["upgraded_damage"] = upgraded
    if source.get("block") is not None:
        values["base_block"] = source.get("block")
        upgraded = _upgrade_number(source.get("upgrade"), "block", source.get("block"))
        if upgraded is not None:
            values["upgraded_block"] = upgraded
    magic = source.get("magic_number")
    if magic is not None:
        values["magic_number"] = magic
        upgraded = _upgrade_number(source.get("upgrade"), "magic_number", magic)
        if upgraded is not None:
            values["upgraded_magic_number"] = upgraded
    return values


def _upgrade_number(upgrade: Any, field: str, base: Any) -> int | None:
    if not isinstance(upgrade, dict):
        return None
    raw = upgrade.get(field)
    if raw is None:
        return None
    if isinstance(raw, str) and raw.startswith("+"):
        try:
            return int(base) + int(raw[1:])
        except (TypeError, ValueError):
            return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _relic_tags(source: dict[str, Any]) -> list[str]:
    tags = []
    tier = _lower(source.get("tier"))
    if tier:
        tags.append(tier)
    color = _lower(source.get("color"))
    if color:
        tags.append(color)
    return tags


def _potion_roles(source: dict[str, Any]) -> list[str]:
    roles = []
    if source.get("is_thrown"):
        roles.extend(["offense", "targeted"])
    rarity = _lower(source.get("rarity"))
    if rarity:
        roles.append(rarity)
    return _dedupe(roles)


def _monster_tags(source: dict[str, Any]) -> list[str]:
    tags = []
    monster_type = _lower(source.get("type"))
    if monster_type:
        tags.append(monster_type)
    if _monster_multi_hit(source):
        tags.append("multi_hit")
    if any(str(move.get("intent") or "").lower().find("debuff") >= 0 for move in _list(source.get("moves"))):
        tags.append("debuff")
    if any(str(move.get("intent") or "").lower().find("attack") >= 0 for move in _list(source.get("moves"))):
        tags.append("frontload_check")
    return _dedupe(tags)


def _monster_values(source: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    max_attack = 0
    max_hits = 0
    for move in _list(source.get("moves")):
        damage = _safe_int(move.get("damage_ascension") if move.get("damage_ascension") is not None else move.get("damage"))
        hits = max(1, _safe_int(move.get("hits")))
        max_attack = max(max_attack, damage * hits)
        max_hits = max(max_hits, hits)
    if max_attack:
        values["max_expected_attack"] = max_attack
    if max_hits > 1:
        values["max_hit_count"] = max_hits
    return values


def _monster_multi_hit(source: dict[str, Any]) -> bool:
    return any(_safe_int(move.get("hits")) > 1 for move in _list(source.get("moves")))


def _act_number(value: Any) -> int | None:
    mapping = {
        "exordium": 1,
        "the city": 2,
        "city": 2,
        "the beyond": 3,
        "beyond": 3,
        "ending": 4,
    }
    key = str(value or "").strip().lower()
    return mapping.get(key)


def _merge_missing_values(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key not in target and value is not None:
            target[key] = value


def _ensure_source(payload: dict[str, Any]) -> None:
    sources = payload.setdefault("sources", [])
    if not isinstance(sources, list):
        payload["sources"] = sources = []
    if not any(item.get("url") == SOURCE_URL for item in sources if isinstance(item, dict)):
        sources.append({"name": SOURCE_NAME, "url": SOURCE_URL})


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_table(path: Path, key: str) -> dict[str, Any]:
    payload = _load_json(path)
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain list key {key!r}")
    return {"payload": deepcopy(payload), "rows": deepcopy(rows)}


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (str(row.get("character") or ""), str(row.get("act") or ""), str(row.get("name") or row.get("id") or "")))


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_name(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum())


def _upper(value: Any) -> str | None:
    return str(value).strip().upper() if value is not None and str(value).strip() else None


def _lower(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def dry_run_label(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    raise SystemExit(main())
