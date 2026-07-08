"""Report concrete static-knowledge gaps from run JSONL logs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .static_knowledge import DEFAULT_KNOWLEDGE_DIR, StaticKnowledge
from .training_manifest import iter_log_files


ENTITY_KINDS = ("cards", "potions", "relics", "monsters", "bosses")
DEFAULT_SOURCE_LIMIT = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report concrete missing static-knowledge entities from run logs.")
    parser.add_argument("logs", nargs="+", type=Path, help="Run JSONL files or directories.")
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON.")
    parser.add_argument(
        "--source-limit",
        type=int,
        default=DEFAULT_SOURCE_LIMIT,
        help="Maximum source examples to keep per missing entity.",
    )
    args = parser.parse_args(argv)

    knowledge = StaticKnowledge.load(args.knowledge_dir)
    report = build_gap_report(args.logs, knowledge=knowledge, knowledge_dir=args.knowledge_dir, source_limit=args.source_limit)
    text = json.dumps(report, ensure_ascii=True, indent=None if args.compact else 2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote static knowledge gap report: {args.output}")
    else:
        print(text, end="")
    print(report["status_line"])
    return 0


def build_gap_report(
    paths: Iterable[Path],
    *,
    knowledge: StaticKnowledge | None = None,
    knowledge_dir: Path | None = None,
    source_limit: int = DEFAULT_SOURCE_LIMIT,
) -> dict[str, Any]:
    input_paths = [Path(path) for path in paths]
    resolved_logs = list(iter_log_files(input_paths))
    knowledge = knowledge or StaticKnowledge.load(knowledge_dir or DEFAULT_KNOWLEDGE_DIR)
    collectors = {kind: _GapCollector(source_limit=source_limit) for kind in ENTITY_KINDS}
    read_errors: list[dict[str, Any]] = []
    observed_card_aliases: set[str] = set()
    for path in resolved_logs:
        try:
            records = list(_read_jsonl(path))
            card_aliases = _observed_card_aliases(records, knowledge)
            observed_card_aliases.update(card_aliases)
            for record in records:
                _scan_record(path, record, knowledge, collectors, card_aliases)
        except json.JSONDecodeError as exc:
            read_errors.append({"path": str(path), "line": exc.lineno, "error": str(exc)})

    entities = {kind: collectors[kind].summary() for kind in ENTITY_KINDS}
    totals = {
        kind: {
            "occurrences": entities[kind]["occurrences"],
            "unique": entities[kind]["unique"],
        }
        for kind in ENTITY_KINDS
    }
    total_occurrences = sum(item["occurrences"] for item in totals.values())
    warnings = []
    if not resolved_logs:
        warnings.append("no_resolved_logs")
    if read_errors:
        warnings.append("read_errors")
    report = {
        "version": 1,
        "inputs": [str(path) for path in input_paths],
        "resolved_logs": [str(path) for path in resolved_logs],
        "resolved_log_count": len(resolved_logs),
        "warnings": warnings,
        "read_errors": read_errors,
        "knowledge_dir": str(knowledge_dir) if knowledge_dir is not None else str(DEFAULT_KNOWLEDGE_DIR),
        "knowledge_source_counts": knowledge.source_counts,
        "observed_aliases": {"cards": len(observed_card_aliases)},
        "total_missing_occurrences": total_occurrences,
        "totals": totals,
        "entities": entities,
    }
    report["status_line"] = _status_line(report)
    return report


def compact_gap_report(raw: Any, *, per_kind_limit: int = 5, source_limit: int = 2) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    status_line = raw.get("status_line")
    if status_line:
        result["status_line"] = str(status_line)
    for field in ("resolved_log_count", "total_missing_occurrences"):
        result[field] = int(raw.get(field) or 0)
    observed_aliases = raw.get("observed_aliases") if isinstance(raw.get("observed_aliases"), dict) else {}
    compact_aliases = {key: int(value or 0) for key, value in observed_aliases.items() if int(value or 0) > 0}
    if compact_aliases:
        result["observed_aliases"] = compact_aliases
    raw_warnings = raw.get("warnings") if isinstance(raw.get("warnings"), list) else []
    warnings = [str(item) for item in raw_warnings if item]
    if warnings:
        result["warnings"] = warnings
    read_errors = raw.get("read_errors") if isinstance(raw.get("read_errors"), list) else []
    if read_errors:
        result["read_error_count"] = len(read_errors)
    totals = raw.get("totals") if isinstance(raw.get("totals"), dict) else {}
    compact_totals: dict[str, dict[str, int]] = {}
    for kind in ENTITY_KINDS:
        item = totals.get(kind) if isinstance(totals.get(kind), dict) else {}
        occurrences = int(item.get("occurrences") or 0)
        unique = int(item.get("unique") or 0)
        if occurrences > 0 or unique > 0:
            compact_totals[kind] = {"occurrences": occurrences, "unique": unique}
    if compact_totals:
        result["totals"] = compact_totals
    entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    compact_entities: dict[str, list[dict[str, Any]]] = {}
    for kind in ENTITY_KINDS:
        group = entities.get(kind) if isinstance(entities.get(kind), dict) else {}
        items = group.get("items") if isinstance(group.get("items"), list) else []
        shown: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            shown.append(
                {
                    "entity": str(item.get("entity") or ""),
                    "count": int(item.get("count") or 0),
                    "sources": _compact_sources(item.get("sources"), limit=source_limit),
                }
            )
            if len(shown) >= per_kind_limit:
                break
        if shown:
            compact_entities[kind] = shown
    if compact_entities:
        result["entities"] = compact_entities
    return result


def _compact_sources(raw: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    sources: list[dict[str, Any]] = []
    for source in raw:
        if not isinstance(source, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("path", "step", "field"):
            if source.get(key) is not None:
                item[key] = source.get(key)
        if item:
            sources.append(item)
        if len(sources) >= limit:
            break
    return sources


class _GapCollector:
    def __init__(self, *, source_limit: int) -> None:
        self.source_limit = max(0, source_limit)
        self.items: dict[str, dict[str, Any]] = {}

    def add(self, entity: Any, *, path: Path, step: Any, field: str) -> None:
        display = _display_entity(entity)
        if not display:
            return
        item = self.items.setdefault(
            display,
            {
                "entity": display,
                "count": 0,
                "normalized_keys": _normalized_entity_keys(entity),
                "sources": [],
            },
        )
        item["count"] += 1
        if len(item["sources"]) < self.source_limit:
            item["sources"].append({"path": str(path), "step": step, "field": field})

    def summary(self) -> dict[str, Any]:
        items = sorted(self.items.values(), key=lambda item: (-int(item["count"]), str(item["entity"])))
        return {
            "occurrences": sum(int(item["count"]) for item in items),
            "unique": len(items),
            "items": items,
        }


def _scan_record(
    path: Path,
    record: dict[str, Any],
    knowledge: StaticKnowledge,
    collectors: dict[str, _GapCollector],
    card_aliases: dict[str, str],
) -> None:
    state = record.get("state") if isinstance(record.get("state"), dict) else {}
    step = record.get("step")
    _scan_cards(path, step, state, knowledge, collectors["cards"], card_aliases)
    _scan_potions(path, step, state, knowledge, collectors["potions"])
    _scan_relics(path, step, state, knowledge, collectors["relics"])
    _scan_monsters(path, step, state, knowledge, collectors["monsters"], collectors["bosses"])
    _scan_boss_fields(path, step, state, knowledge, collectors["bosses"])


def _scan_cards(
    path: Path,
    step: Any,
    state: dict[str, Any],
    knowledge: StaticKnowledge,
    collector: _GapCollector,
    card_aliases: dict[str, str],
) -> None:
    deck_field = "deck_cards" if _list_items(state.get("deck_cards")) else "deck"
    for field in (deck_field, "hand", "hand_cards", "draw_pile", "discard_pile", "exhaust_pile", "card_reward_options"):
        for card in _list_items(state.get(field)):
            if _is_skip_card(card):
                continue
            if knowledge.card_for(_resolve_card_alias(card, card_aliases)) is None:
                collector.add(card, path=path, step=step, field=field)
    combat = _combat_payload(state)
    for field in ("hand", "hand_cards", "draw_pile", "discard_pile", "exhaust_pile"):
        for card in _list_items(combat.get(field)):
            if _is_skip_card(card):
                continue
            if knowledge.card_for(_resolve_card_alias(card, card_aliases)) is None:
                collector.add(card, path=path, step=step, field=f"combat.{field}")


def _scan_potions(path: Path, step: Any, state: dict[str, Any], knowledge: StaticKnowledge, collector: _GapCollector) -> None:
    for field in ("potions", "potion_reward_options"):
        for potion in _list_items(state.get(field)):
            if knowledge.potion_for(potion) is None:
                collector.add(potion, path=path, step=step, field=field)
    screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
    for field in ("potions", "potion_reward_options"):
        for potion in _list_items(screen_state.get(field)):
            if knowledge.potion_for(potion) is None:
                collector.add(potion, path=path, step=step, field=f"screen_state.{field}")


def _scan_relics(path: Path, step: Any, state: dict[str, Any], knowledge: StaticKnowledge, collector: _GapCollector) -> None:
    relic_field = "relic_items" if _list_items(state.get("relic_items")) else "relics"
    for field in (relic_field, "relic_reward_options"):
        for relic in _list_items(state.get(field)):
            if knowledge.relic_for(relic) is None:
                collector.add(relic, path=path, step=step, field=field)
    screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
    for field in ("relics", "rewards", "reward_items", "relic_reward_options"):
        for relic in _list_items(screen_state.get(field)):
            if _reward_kind(relic) and _reward_kind(relic) != "relic":
                continue
            if knowledge.relic_for(relic) is None:
                collector.add(relic, path=path, step=step, field=f"screen_state.{field}")


def _scan_monsters(
    path: Path,
    step: Any,
    state: dict[str, Any],
    knowledge: StaticKnowledge,
    monster_collector: _GapCollector,
    boss_collector: _GapCollector,
) -> None:
    combat = _combat_payload(state)
    act = _safe_int(state.get("act"))
    for monster in _list_items(combat.get("monsters")):
        if knowledge.monster_for(monster) is None:
            monster_collector.add(monster, path=path, step=step, field="combat.monsters")
        if _looks_like_boss(monster, state_act=act) and knowledge.boss_for(monster) is None:
            boss_collector.add(monster, path=path, step=step, field="combat.monsters")


def _scan_boss_fields(path: Path, step: Any, state: dict[str, Any], knowledge: StaticKnowledge, collector: _GapCollector) -> None:
    screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
    for source_name, source in (("state", state), ("screen_state", screen_state)):
        for field in ("boss", "boss_id", "boss_name", "act_boss", "act_boss_id", "act_boss_name"):
            for boss in _list_items(source.get(field)):
                if knowledge.boss_for(boss) is None:
                    collector.add(boss, path=path, step=step, field=f"{source_name}.{field}")


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                yield record


def _observed_card_aliases(records: list[dict[str, Any]], knowledge: StaticKnowledge) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for record in records:
        state = record.get("state") if isinstance(record.get("state"), dict) else {}
        screen_state = state.get("screen_state") if isinstance(state.get("screen_state"), dict) else {}
        for source in (state, screen_state):
            for option in _list_items(source.get("card_reward_options")):
                if not isinstance(option, dict):
                    continue
                card_id = str(option.get("id") or option.get("card_id") or "").strip()
                if not card_id or knowledge.card_for({"id": card_id}) is None:
                    continue
                for key in _card_alias_keys(option.get("name")):
                    if key and key != card_id:
                        candidates.setdefault(key, set()).add(card_id)
    return {alias: next(iter(ids)) for alias, ids in candidates.items() if len(ids) == 1}


def _resolve_card_alias(card: Any, aliases: dict[str, str]) -> Any:
    if not aliases:
        return card
    if isinstance(card, dict):
        for key in _card_alias_keys(card.get("name")) + _card_alias_keys(card.get("id")):
            card_id = aliases.get(key)
            if card_id:
                resolved = dict(card)
                resolved["id"] = card_id
                return resolved
        return card
    for key in _card_alias_keys(card):
        card_id = aliases.get(key)
        if card_id:
            return {"id": card_id, "name": card}
    return card


def _card_alias_keys(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    base = raw.split("+", 1)[0].removesuffix("_P").strip()
    keys = []
    for key in (raw, base):
        if key and key not in keys:
            keys.append(key)
    return keys


def _combat_payload(state: dict[str, Any]) -> dict[str, Any]:
    combat = state.get("combat") if isinstance(state.get("combat"), dict) else {}
    return combat


def _list_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _display_entity(entity: Any) -> str:
    if isinstance(entity, dict):
        for key in ("id", "name", "card_id", "potion_id", "relic_id"):
            value = entity.get(key)
            if value not in (None, ""):
                return _clean_text(value)
        return _clean_text(json.dumps(entity, ensure_ascii=False, sort_keys=True))
    return _clean_text(entity)


def _normalized_entity_keys(entity: Any) -> list[str]:
    values: list[Any] = []
    if isinstance(entity, dict):
        values.extend(entity.get(key) for key in ("id", "name", "card_id", "potion_id", "relic_id"))
    else:
        values.append(entity)
    keys = []
    for value in values:
        key = _normalize_key(value)
        if key and key not in keys:
            keys.append(key)
    return keys


def _normalize_key(value: Any) -> str:
    if value is None:
        return ""
    return "".join(ch for ch in _clean_text(value).lower() if ch.isalnum())


def _clean_text(value: Any) -> str:
    text = str(value)
    return text.encode("utf-8", errors="replace").decode("utf-8")


def _is_skip_card(card: Any) -> bool:
    return _normalize_key(_display_entity(card)) in {"skip", "skipcard"}


def _reward_kind(reward: Any) -> str:
    if not isinstance(reward, dict):
        return ""
    return str(reward.get("type") or reward.get("reward_type") or reward.get("kind") or "").lower()


def _looks_like_boss(monster: Any, *, state_act: int) -> bool:
    if not isinstance(monster, dict):
        text = _normalize_key(monster)
        return text in {"hexaghost", "slimeboss", "theguardian"}
    if bool(monster.get("boss")):
        return True
    if state_act == 1:
        text = _normalize_key(f"{monster.get('id', '')} {monster.get('name', '')}")
        return any(name in text for name in ("hexaghost", "slimeboss", "theguardian"))
    return False


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _status_line(report: dict[str, Any]) -> str:
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    parts = [
        "static_knowledge_gaps",
        f"logs={int(report.get('resolved_log_count') or 0)}",
        f"missing={int(report.get('total_missing_occurrences') or 0)}",
    ]
    for kind in ENTITY_KINDS:
        summary = totals.get(kind) if isinstance(totals.get(kind), dict) else {}
        occurrences = int(summary.get("occurrences") or 0)
        unique = int(summary.get("unique") or 0)
        if occurrences:
            parts.append(f"{kind}={occurrences}/{unique}")
    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    if warnings:
        parts.append("warnings=" + ",".join(str(warning) for warning in warnings))
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
