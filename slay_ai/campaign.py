"""Long-running campaign runner for four-character A20 attempts."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .memory import StrategyMemory
from .mcp.client import MCPClient, MCPError
from .runner import EpisodeResult, ROOT, run_episode
from .unlocks import DEFAULT_GAME_DIR, read_unlocks


DEFAULT_CHARACTERS = ["IRONCLAD", "SILENT", "DEFECT", "WATCHER"]


@dataclass
class Target:
    character: str
    ascension: int

    @property
    def key(self) -> str:
        return f"{self.character}:A{self.ascension}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loop Slay the Spire attempts until campaign targets are cleared.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/mcp")
    parser.add_argument("--characters", nargs="+", default=DEFAULT_CHARACTERS)
    parser.add_argument("--ascension", type=int, default=20)
    parser.add_argument("--ladder", action="store_true", help="Clear A0..target for each character instead of target only.")
    parser.add_argument(
        "--from-unlocks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Start each character from the locally unlocked ascension and climb toward --ascension.",
    )
    parser.add_argument("--game-dir", type=Path, default=DEFAULT_GAME_DIR)
    parser.add_argument("--attempts-per-target", type=int, default=50)
    parser.add_argument("--max-steps", type=int, default=1800)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument("--cooldown", type=float, default=1.0)
    parser.add_argument(
        "--existing-save",
        choices=["fail", "continue", "abandon"],
        default="fail",
        help="What to do when a fresh attempt is blocked by an existing save.",
    )
    parser.add_argument("--log-dir", type=Path, default=ROOT / "ai_runs")
    parser.add_argument("--progress-file", type=Path, default=ROOT / "data" / "campaign_progress.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-all-hardware", action="store_true", help="Record full-resource intent; game control remains one MCP instance.")
    args = parser.parse_args(argv)

    unlocks = read_unlocks(args.game_dir)
    targets = build_targets(args.characters, args.ascension, args.ladder, unlocks if args.from_unlocks else None)
    progress = load_progress(args.progress_file)
    progress.setdefault("hardware", hardware_snapshot(args.use_all_hardware))
    progress.setdefault("targets", {})

    client = MCPClient(args.endpoint)
    memory = StrategyMemory.load()
    client.ensure_initialized()

    print(f"Campaign targets: {', '.join(target.key for target in targets)}")
    print(f"Progress file: {args.progress_file}")
    if args.from_unlocks:
        print(
            "Unlocked ascensions: "
            + ", ".join(f"{char}=A{unlocks.get(char.upper()).unlocked_ascension if unlocks.get(char.upper()) else 0}" for char in args.characters)
        )
    if args.use_all_hardware:
        print("Full-resource mode noted. Current MCP control is single-instance; offline training can use CPU/GPU logs.")

    if args.from_unlocks:
        run_frontier_campaign(args, progress, client, memory)
    else:
        run_static_campaign(args, targets, progress, client, memory)

    completed = [key for key, item in progress["targets"].items() if item.get("wins", 0) > 0]
    print(f"Campaign pass complete. Cleared {len(completed)} recorded targets.")
    return 0


def run_static_campaign(
    args: argparse.Namespace,
    targets: list[Target],
    progress: dict[str, Any],
    client: MCPClient,
    memory: StrategyMemory,
) -> None:
    for target in targets:
        run_target_attempts(args, target, progress, client, memory)


def run_frontier_campaign(
    args: argparse.Namespace,
    progress: dict[str, Any],
    client: MCPClient,
    memory: StrategyMemory,
) -> None:
    for raw_character in args.characters:
        character = raw_character.upper()
        while True:
            unlocks = read_unlocks(args.game_dir)
            unlocked = unlocks.get(character).unlocked_ascension if unlocks.get(character) else 0
            current = min(int(unlocked), args.ascension)
            target = Target(character, current)
            if current == args.ascension and progress["targets"].get(target.key, {}).get("wins", 0) > 0:
                print(f"[done] {target.key} already cleared.")
                break
            before_wins = progress["targets"].get(target.key, {}).get("wins", 0)
            run_target_attempts(args, target, progress, client, memory)
            after_wins = progress["targets"].get(target.key, {}).get("wins", 0)
            if after_wins <= before_wins:
                break
            if current >= args.ascension:
                break


def run_target_attempts(
    args: argparse.Namespace,
    target: Target,
    progress: dict[str, Any],
    client: MCPClient,
    memory: StrategyMemory,
) -> None:
        item = progress["targets"].setdefault(
            target.key,
            {"wins": 0, "attempts": 0, "last_status": None, "last_log": None},
        )
        if item.get("wins", 0) > 0:
            print(f"[skip] {target.key} already has a win.")
            return
        while item.get("attempts", 0) < args.attempts_per_target and item.get("wins", 0) == 0:
            attempt = item.get("attempts", 0) + 1
            print(f"[run] {target.key} attempt {attempt}/{args.attempts_per_target}")
            if args.dry_run:
                item["last_status"] = "dry_run"
                item["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_progress(args.progress_file, progress)
                return
            else:
                try:
                    result = run_episode(
                        endpoint=args.endpoint,
                        character=target.character,
                        ascension=target.ascension,
                        start=True,
                        existing_save=args.existing_save,
                        max_steps=args.max_steps,
                        interval=args.interval,
                        startup_timeout=args.startup_timeout,
                        log_dir=args.log_dir,
                        echo=True,
                        client=client,
                        memory=memory,
                    )
                except MCPError as exc:
                    item["last_status"] = "start_failed"
                    item["last_error"] = str(exc)
                    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
                    save_progress(args.progress_file, progress)
                    raise
            item["attempts"] = attempt
            item["last_status"] = result.status
            item.pop("last_error", None)
            item["last_log"] = str(result.log_path)
            item["last_steps"] = result.steps
            item["last_floor"] = result.floor
            item["last_score"] = result.score
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if result.victory:
                item["wins"] = item.get("wins", 0) + 1
                print(f"[win] {target.key} cleared on attempt {attempt}.")
            save_progress(args.progress_file, progress)
            if not args.dry_run:
                return_to_menu(client)
            if not args.dry_run:
                time.sleep(args.cooldown)


def build_targets(characters: list[str], ascension: int, ladder: bool, unlocks: dict[str, Any] | None = None) -> list[Target]:
    targets: list[Target] = []
    for character in characters:
        normalized = character.upper()
        unlocked = 0
        if unlocks and normalized in unlocks:
            unlocked = int(unlocks[normalized].unlocked_ascension)
        if unlocks:
            levels = [min(unlocked, ascension)]
        else:
            levels = range(0, ascension + 1) if ladder else [ascension]
        targets.extend(Target(normalized, level) for level in levels)
    return targets


def load_progress(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "created_at": datetime.now().isoformat(timespec="seconds")}


def save_progress(path: Path, progress: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hardware_snapshot(use_all_hardware: bool) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "use_all_hardware_requested": use_all_hardware,
        "cpu_count": os.cpu_count(),
        "gpu": detect_gpu(),
    }
    return snapshot


def detect_gpu() -> dict[str, Any]:
    try:
        import torch  # type: ignore
    except Exception:
        return {"torch_available": False, "cuda_available": False}
    cuda_available = bool(torch.cuda.is_available())
    devices = []
    if cuda_available:
        for index in range(torch.cuda.device_count()):
            devices.append(torch.cuda.get_device_name(index))
    return {"torch_available": True, "cuda_available": cuda_available, "devices": devices}


def return_to_menu(client: MCPClient, max_attempts: int = 8) -> None:
    for _ in range(max_attempts):
        try:
            state = client.get_screen_state()
            if not state.get("in_game"):
                return
            if state.get("can_proceed"):
                client.execute_actions([{"action": "proceed"}])
            else:
                client.call_tool("save_game", {})
        except MCPError:
            return
        time.sleep(0.4)


if __name__ == "__main__":
    raise SystemExit(main())
