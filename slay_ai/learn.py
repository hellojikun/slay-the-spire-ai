"""Offline learning from JSONL run logs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .memory import LEARNED_MEMORY, StrategyMemory
from .training_manifest import resolve_log_training_source


@dataclass
class LearnedLog:
    path: Path
    picks: list[str]
    victory: bool | None
    floor: int
    score: int | None
    character: str | None
    ascension: int | None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update learned memory from ai_runs JSONL logs.")
    parser.add_argument("logs", nargs="*", type=Path, default=[Path("runs") / "ai_runs"])
    parser.add_argument("--manifest", type=Path, help="Use clean_trainable log paths from a training manifest.")
    parser.add_argument("--learned-path", type=Path, default=LEARNED_MEMORY)
    parser.add_argument("--reset", action="store_true", help="Ignore existing learned memory before replaying logs.")
    args = parser.parse_args(argv)

    memory = StrategyMemory.load(learned_path=args.learned_path)
    if args.reset:
        memory.learned = {
            "version": 1,
            "runs": {"victories": 0, "deaths": 0, "total": 0},
            "card_picks": {},
            "recent_outcomes": [],
            "learning_replays": [],
        }

    training_source = resolve_log_training_source(args.logs, args.manifest)
    logs = [Path(path) for path in training_source["resolved_logs"]]
    learned = [read_log(path) for path in logs]
    applied = 0
    skipped = 0
    for item in learned:
        for pick in item.picks:
            memory.record_card_pick(pick)
        if item.victory is None:
            skipped += 1
            continue
        memory.record_outcome_summary(
            victory=item.victory,
            floor=item.floor,
            score=item.score,
            character=item.character,
            ascension=item.ascension,
            episode_picks=item.picks,
        )
        applied += 1
    record_learning_replay(memory, training_source, applied=applied, skipped=skipped)
    memory.save()
    print(f"Resolved {training_source['resolved_log_count']} log files from {training_source['mode']}.")
    if training_source["warnings"]:
        print(f"Learning source warnings: {', '.join(training_source['warnings'])}.")
    print(f"Read {len(logs)} logs, applied {applied} completed runs, skipped {skipped} incomplete runs.")
    print(f"Updated learned memory: {memory.learned_path}")
    return 0


def record_learning_replay(
    memory: StrategyMemory,
    training_source: dict[str, Any],
    *,
    applied: int,
    skipped: int,
) -> None:
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": training_source,
        "read_logs": training_source["resolved_log_count"],
        "applied_completed_runs": applied,
        "skipped_incomplete_runs": skipped,
    }
    memory.learned["last_learning_replay"] = entry
    history = memory.learned.setdefault("learning_replays", [])
    if not isinstance(history, list):
        history = []
        memory.learned["learning_replays"] = history
    history.append(entry)


def read_log(path: Path) -> LearnedLog:
    picks: list[str] = []
    latest: dict[str, Any] = {}
    outcome: dict[str, Any] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            latest = record.get("state") or latest
            decision = record.get("decision") or {}
            pick = decision.get("learn_card_pick")
            if pick:
                picks.append(str(pick))
            state_outcome = (record.get("state") or {}).get("outcome")
            if state_outcome:
                outcome = state_outcome

    victory = outcome.get("victory")
    if victory is None and latest.get("screen_type") == "GAME_OVER":
        victory = False
    if victory is not None:
        victory = bool(victory)
    return LearnedLog(
        path=path,
        picks=picks,
        victory=victory,
        floor=int(latest.get("floor") or 0),
        score=outcome.get("score"),
        character=latest.get("class"),
        ascension=latest.get("ascension_level"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
