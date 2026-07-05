"""Build a clean training manifest and shadow-model examples from run logs."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


CLEAN_TRAINABLE = "clean_trainable"
DIAGNOSTIC_EXCLUDED = "diagnostic_excluded"
INFRA_BLOCKED = "infra_blocked"


@dataclass
class LogClassification:
    path: str
    category: str
    reason: str
    character: str | None = None
    ascension: int | None = None
    floor: int = 0
    victory: bool | None = None
    score: int | None = None
    steps: int = 0
    card_picks: int = 0
    action_records: int = 0
    recovered_actions: int = 0
    failed_actions: int = 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify JSONL logs before offline learning.")
    parser.add_argument("logs", nargs="*", type=Path, default=[Path("ai_runs")])
    parser.add_argument("--output", type=Path, default=Path("data") / "training_log_manifest.json")
    parser.add_argument("--shadow-dir", type=Path, help="Optional directory for route/potion/pre-boss JSONL examples.")
    parser.add_argument("--print-clean", action="store_true", help="Print clean trainable log paths, one per line.")
    args = parser.parse_args(argv)

    manifest, shadow_examples = build_manifest(args.logs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.shadow_dir:
        write_shadow_examples(args.shadow_dir, shadow_examples)
    if args.print_clean:
        for item in manifest["categories"][CLEAN_TRAINABLE]:
            print(item["path"])
    else:
        summary = manifest["summary"]
        print(
            "Classified "
            f"{summary['total_logs']} logs: "
            f"{summary[CLEAN_TRAINABLE]} clean, "
            f"{summary[DIAGNOSTIC_EXCLUDED]} diagnostic, "
            f"{summary[INFRA_BLOCKED]} infra."
        )
        print(f"Wrote manifest: {args.output}")
    return 0


def build_manifest(paths: Iterable[Path]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    categories: dict[str, list[dict[str, Any]]] = {
        CLEAN_TRAINABLE: [],
        DIAGNOSTIC_EXCLUDED: [],
        INFRA_BLOCKED: [],
    }
    shadow_examples: dict[str, list[dict[str, Any]]] = {
        "route_risk": [],
        "potion_tempo": [],
        "pre_boss_deck_quality": [],
    }

    for path in _iter_log_files(paths):
        classification, records = classify_log(path)
        categories[classification.category].append(asdict(classification))
        if classification.category != CLEAN_TRAINABLE:
            continue
        shadow_examples["route_risk"].extend(_route_risk_examples(path, records, classification))
        shadow_examples["potion_tempo"].extend(_potion_tempo_examples(path, records, classification))
        shadow_examples["pre_boss_deck_quality"].extend(_pre_boss_examples(path, records, classification))

    summary = {
        "total_logs": sum(len(items) for items in categories.values()),
        CLEAN_TRAINABLE: len(categories[CLEAN_TRAINABLE]),
        DIAGNOSTIC_EXCLUDED: len(categories[DIAGNOSTIC_EXCLUDED]),
        INFRA_BLOCKED: len(categories[INFRA_BLOCKED]),
        "shadow_examples": {key: len(value) for key, value in shadow_examples.items()},
    }
    manifest = {
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "categories": categories,
    }
    return manifest, shadow_examples


def classify_log(path: Path) -> tuple[LogClassification, list[dict[str, Any]]]:
    try:
        records = _read_records(path)
    except json.JSONDecodeError as exc:
        return (
            LogClassification(
                path=str(path),
                category=INFRA_BLOCKED,
                reason=f"json_decode_error:{exc.lineno}",
            ),
            [],
        )
    if not records:
        return LogClassification(path=str(path), category=DIAGNOSTIC_EXCLUDED, reason="empty_log"), []

    latest_state = _latest_state(records)
    outcome = _latest_outcome(records)
    victory = outcome.get("victory")
    if victory is None and latest_state.get("screen_type") == "GAME_OVER":
        victory = False
    if victory is not None:
        victory = bool(victory)

    action_records = [record for record in records if record.get("event") == "action_result"]
    failed_actions = [
        record
        for record in action_records
        if str(record.get("action_status") or "").lower() in {"failed", "action_failed"}
    ]
    recovered_actions = [
        record
        for record in action_records
        if record.get("recovered") or str(record.get("action_status") or "").lower() in {"recoverable_error", "preflight_mismatch"}
    ]
    card_picks = sum(1 for record in records if (record.get("decision") or {}).get("learn_card_pick"))

    base = LogClassification(
        path=str(path),
        category=CLEAN_TRAINABLE,
        reason="completed_clean",
        character=latest_state.get("class"),
        ascension=_optional_int(latest_state.get("ascension_level")),
        floor=_safe_int(latest_state.get("floor")),
        victory=victory,
        score=outcome.get("score"),
        steps=_safe_int(records[-1].get("step")),
        card_picks=card_picks,
        action_records=len(action_records),
        recovered_actions=len(recovered_actions),
        failed_actions=len(failed_actions),
    )
    if failed_actions:
        base.category = INFRA_BLOCKED
        base.reason = "failed_action"
    elif victory is None:
        base.category = DIAGNOSTIC_EXCLUDED
        base.reason = "no_terminal_outcome"
    return base, records


def write_shadow_examples(output_dir: Path, examples: dict[str, list[dict[str, Any]]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in examples.items():
        path = output_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def clean_log_paths_from_manifest(path: Path) -> list[Path]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rows = manifest.get("categories", {}).get(CLEAN_TRAINABLE, [])
    return [Path(row["path"]) for row in rows if row.get("path")]


def _iter_log_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.glob("*.jsonl"))
        elif path.exists():
            yield path


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def _latest_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for record in records:
        state = record.get("state")
        if isinstance(state, dict):
            latest = state
    return latest


def _latest_outcome(records: list[dict[str, Any]]) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    for record in records:
        state = record.get("state") or {}
        if isinstance(state.get("outcome"), dict):
            outcome = state["outcome"]
    return outcome


def _route_risk_examples(path: Path, records: list[dict[str, Any]], final: LogClassification) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        if state.get("screen_type") != "MAP":
            continue
        route = state.get("route_evaluation") or {}
        options = route.get("options") or []
        if not options:
            continue
        decision = record.get("decision") or {}
        selected = _first_choice_index(decision.get("actions") or [])
        selected_option = next((option for option in options if _safe_int(option.get("choice_index")) == selected), {})
        lookahead = selected_option.get("lookahead") or {}
        rows.append(
            {
                "source_log": str(path),
                "step": record.get("step"),
                "character": state.get("class"),
                "ascension": state.get("ascension_level"),
                "floor": state.get("floor"),
                "act": state.get("act"),
                "hp_ratio": _hp_ratio(state),
                "selected_choice": selected,
                "selected_symbol": selected_option.get("symbol"),
                "route_score": selected_option.get("score"),
                "forced_elite_within_3": bool(lookahead.get("forced_elite_within_3")),
                "forced_combat_within_2": bool(lookahead.get("forced_combat_within_2")),
                "nearest_rest": lookahead.get("nearest_rest"),
                "nearest_shop": lookahead.get("nearest_shop"),
                "final_floor": final.floor,
                "victory": final.victory,
                "floor_delta": final.floor - _safe_int(state.get("floor")),
            }
        )
    return rows


def _potion_tempo_examples(path: Path, records: list[dict[str, Any]], final: LogClassification) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        combat = state.get("combat") or {}
        potions = state.get("potions") or []
        incoming = _safe_int(combat.get("incoming_damage"))
        if not combat or not potions or incoming <= 0:
            continue
        rows.append(
            {
                "source_log": str(path),
                "step": record.get("step"),
                "character": state.get("class"),
                "ascension": state.get("ascension_level"),
                "floor": state.get("floor"),
                "act": state.get("act"),
                "turn": combat.get("turn"),
                "hp_ratio": _hp_ratio(state),
                "incoming": incoming,
                "potion_ids": [potion.get("id") or potion.get("name") for potion in potions if isinstance(potion, dict)],
                "enemy_ids": [
                    monster.get("id") or monster.get("name")
                    for monster in combat.get("monsters", [])
                    if isinstance(monster, dict)
                ],
                "died_same_floor": bool(final.victory is False and final.floor == _safe_int(state.get("floor"))),
                "final_floor": final.floor,
                "victory": final.victory,
            }
        )
    return rows


def _pre_boss_examples(path: Path, records: list[dict[str, Any]], final: LogClassification) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        state = record.get("state") or {}
        if not state.get("boss_available"):
            continue
        deck = state.get("deck") or []
        rows.append(
            {
                "source_log": str(path),
                "step": record.get("step"),
                "character": state.get("class"),
                "ascension": state.get("ascension_level"),
                "floor": state.get("floor"),
                "act": state.get("act"),
                "hp_ratio": _hp_ratio(state),
                "deck_size": len(deck) if isinstance(deck, list) else None,
                "attack_cards": _count_named_cards(deck, {"Strike_R", "Strike", "Bash", "Clothesline", "Wild Strike", "Immolate"}),
                "block_cards": _count_named_cards(deck, {"Defend_R", "Defend", "Shrug It Off", "True Grit", "Power Through"}),
                "draw_cards": _count_named_cards(deck, {"Battle Trance", "Burning Pact", "Pommel Strike", "Offering"}),
                "potion_count": len(state.get("potions") or []),
                "relic_count": len(state.get("relics") or []),
                "gold": state.get("gold"),
                "final_floor": final.floor,
                "victory": final.victory,
            }
        )
    return rows


def _first_choice_index(actions: list[dict[str, Any]]) -> int | None:
    for action in actions:
        if action.get("action") == "choose":
            return _optional_int(action.get("choice_index"))
    return None


def _hp_ratio(state: dict[str, Any]) -> float | None:
    current = _safe_int(state.get("current_hp"))
    maximum = _safe_int(state.get("max_hp"))
    if maximum <= 0:
        return None
    return round(current / maximum, 3)


def _count_named_cards(deck: Any, names: set[str]) -> int:
    if not isinstance(deck, list):
        return 0
    normalized = {name.replace(" ", "").lower() for name in names}
    count = 0
    for card in deck:
        key = str(card).replace(" ", "").lower()
        if key in normalized:
            count += 1
    return count


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    parsed = _optional_int(value)
    return parsed if parsed is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
