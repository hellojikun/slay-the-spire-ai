"""Offline learning from JSONL run logs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .memory import LEARNED_MEMORY, StrategyMemory


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
    parser.add_argument("logs", nargs="*", type=Path, default=[Path("ai_runs")])
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
        }

    logs = list(_iter_log_files(args.logs))
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
    memory.save()
    print(f"Read {len(logs)} logs, applied {applied} completed runs, skipped {skipped} incomplete runs.")
    print(f"Updated learned memory: {memory.learned_path}")
    return 0


def _iter_log_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.glob("*.jsonl"))
        elif path.exists():
            yield path


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
