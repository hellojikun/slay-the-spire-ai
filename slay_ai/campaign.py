"""Long-running campaign runner for frontier and probe attempts."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .act1_boss_gate import DEFAULT_MIN_CLEARED, DEFAULT_MIN_PRISTINE_CLEARED, DEFAULT_MIN_REACHED
from .act1_boss_gate import build_report as build_act1_boss_gate_report
from .act1_boss_gate import status_line as act1_boss_gate_status_line
from .memory import StrategyMemory
from .mcp.client import MCPClient, MCPError
from .model import COMBAT_SEARCH_MODEL_PATH, DECK_QUALITY_MODEL_PATH, POTION_TEMPO_MODEL_PATH, ROUTE_RISK_MODEL_PATH
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
    parser.add_argument("--log-dir", type=Path, default=ROOT / "runs" / "ai_runs")
    parser.add_argument("--no-manifest", action="store_true", help="Do not write per-run training manifests.")
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        help="Directory for per-run manifests. Defaults to <log-dir>/manifests.",
    )
    parser.add_argument(
        "--manifest-knowledge-dir",
        type=Path,
        default=ROOT / "data" / "static_knowledge",
        help="Static knowledge directory used to enrich automatic per-run manifests when present.",
    )
    parser.add_argument("--manifest-shadow-dir", type=Path, help="Optional base directory for per-run shadow JSONL files.")
    parser.add_argument("--manifest-advice-dir", type=Path, help="Optional base directory for per-run shadow advice JSONL files.")
    parser.add_argument("--route-risk-model-path", type=Path, default=ROUTE_RISK_MODEL_PATH)
    parser.add_argument("--potion-tempo-model-path", type=Path, default=POTION_TEMPO_MODEL_PATH)
    parser.add_argument("--deck-quality-model-path", type=Path, default=DECK_QUALITY_MODEL_PATH)
    parser.add_argument("--combat-search-model-path", type=Path, default=COMBAT_SEARCH_MODEL_PATH)
    parser.add_argument(
        "--model-authority",
        choices=["shadow", "assist", "pilot"],
        default="shadow",
        help="How much runtime authority learned signals may take. assist/pilot currently affect card rewards only.",
    )
    parser.add_argument("--act1-boss-gate-output", type=Path, help="Optional JSON report for the target's Act 1 boss gate.")
    parser.add_argument("--act1-boss-gate-min-reached", type=int, default=DEFAULT_MIN_REACHED)
    parser.add_argument("--act1-boss-gate-min-cleared", type=int, default=DEFAULT_MIN_CLEARED)
    parser.add_argument("--act1-boss-gate-min-pristine-cleared", type=int, default=DEFAULT_MIN_PRISTINE_CLEARED)
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
                        write_manifest=not args.no_manifest,
                        manifest_dir=args.manifest_dir,
                        manifest_knowledge_dir=args.manifest_knowledge_dir,
                        manifest_shadow_dir=args.manifest_shadow_dir,
                        manifest_advice_dir=args.manifest_advice_dir,
                        route_risk_model_path=args.route_risk_model_path,
                        potion_tempo_model_path=args.potion_tempo_model_path,
                        deck_quality_model_path=args.deck_quality_model_path,
                        combat_search_model_path=args.combat_search_model_path,
                        model_authority=getattr(args, "model_authority", "shadow"),
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
            item["last_manifest"] = str(result.manifest_path) if result.manifest_path else None
            _append_manifest_history(item, result.manifest_path)
            item["last_manifest_category"] = result.manifest_category
            item["last_manifest_reason"] = result.manifest_reason
            item.update(_manifest_progress_fields(result.manifest_path))
            item.update(_act1_boss_gate_progress_fields(args, target, item))
            item["last_shadow_advice"] = str(result.shadow_advice_path) if result.shadow_advice_path else None
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


def _append_manifest_history(item: dict[str, Any], path: Path | None) -> None:
    if path is None:
        return
    path_text = str(path)
    history = item.setdefault("manifest_history", [])
    if not isinstance(history, list):
        history = []
        item["manifest_history"] = history
    if path_text not in history:
        history.append(path_text)


def _act1_boss_gate_progress_fields(args: argparse.Namespace, target: Target, item: dict[str, Any]) -> dict[str, Any]:
    history = item.get("manifest_history")
    if not isinstance(history, list):
        return {}
    manifest_paths = [Path(path) for path in history if isinstance(path, str) and Path(path).exists()]
    if not manifest_paths:
        return {}
    report = build_act1_boss_gate_report(
        manifest_paths,
        character=target.character,
        ascension=target.ascension,
        min_reached=int(getattr(args, "act1_boss_gate_min_reached", DEFAULT_MIN_REACHED)),
        min_cleared=int(getattr(args, "act1_boss_gate_min_cleared", DEFAULT_MIN_CLEARED)),
        min_pristine_cleared=int(getattr(args, "act1_boss_gate_min_pristine_cleared", DEFAULT_MIN_PRISTINE_CLEARED)),
    )
    output = getattr(args, "act1_boss_gate_output", None)
    next_probe_goal = report["gate"].get("next_probe_goal", {})
    fields: dict[str, Any] = {
        "last_act1_boss_gate_passed": bool(report["gate"]["passed"]),
        "last_act1_boss_gate_next_action": report["gate"]["next_action"],
        "last_act1_boss_gate_counts": report["counts"],
        "last_act1_boss_gate_requirements": report["gate"]["requirements"],
        "last_act1_boss_gate_deficits": report["gate"].get("deficits", {}),
        "last_act1_boss_gate_progress": report["gate"].get("progress", {}),
        "last_act1_boss_gate_remaining_progress": report["gate"].get("remaining_progress", []),
        "last_act1_boss_gate_primary_remaining_progress": report["gate"].get("primary_remaining_progress"),
        "last_act1_boss_gate_next_probe_goal": next_probe_goal,
        "last_act1_boss_gate_next_probe_acceptance_criteria": next_probe_goal.get("acceptance_criteria", {}),
        "last_act1_boss_gate_latest_run_acceptance": report["gate"].get("latest_run_acceptance", {}),
        "last_act1_boss_gate_execution_recovery": report.get("execution_recovery", {}),
        "last_act1_boss_gate_data_quality": _act1_boss_gate_data_quality_progress(
            report.get("data_quality", {})
        ),
        "last_act1_boss_gate_focus": report["gate"].get("focus", {}),
        "last_act1_boss_gate_blocking_reasons": report["gate"].get("blocking_reasons", []),
        "last_act1_boss_gate_primary_blocking_reason": report["gate"].get("primary_blocking_reason"),
        "last_act1_boss_gate_status": act1_boss_gate_status_line(report),
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fields["last_act1_boss_gate_report"] = str(output)
    return fields


def _act1_boss_gate_data_quality_progress(data_quality: Any) -> dict[str, Any]:
    if not isinstance(data_quality, dict):
        return {}
    fields = (
        "total_manifests",
        "coverage_manifests",
        "missing_coverage_manifests",
        "shadow_feature_rows",
        "feature_gap_manifests",
        "feature_zero_manifests",
        "feature_gaps",
        "feature_zero",
        "shadow_label_rows",
        "shadow_label_trainable",
        "shadow_label_excluded",
        "label_exclusion_manifests",
        "label_exclusion_reasons",
        "direct_kill_available_labels",
        "missed_single_card_search_labels",
    )
    return {field: data_quality[field] for field in fields if field in data_quality}


def _manifest_progress_fields(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    item = _single_manifest_item(manifest)
    if not item:
        return {}
    fields: dict[str, Any] = {}
    if item.get("validation_grade"):
        fields["last_validation_grade"] = item.get("validation_grade")
    flags = item.get("validation_flags")
    if isinstance(flags, list):
        fields["last_validation_flags"] = flags
    boss = ((item.get("validation_evidence") or {}).get("act1_boss") or {})
    if isinstance(boss, dict) and boss:
        fields["last_act1_boss_reached"] = bool(boss.get("reached"))
        fields["last_act1_boss_cleared"] = bool(boss.get("cleared"))
        for source, target in (
            ("enemy_ids", "last_act1_boss_enemy_ids"),
            ("entry_step", "last_act1_boss_entry_step"),
            ("entry_hp", "last_act1_boss_entry_hp"),
            ("entry_potion_count", "last_act1_boss_entry_potion_count"),
            ("clear_step", "last_act1_boss_clear_step"),
            ("prefix_pristine_clear", "last_act1_boss_prefix_pristine_clear"),
            ("prefix_blockers", "last_act1_boss_prefix_blockers"),
            ("last_turn", "last_act1_boss_last_turn"),
            ("last_hp", "last_act1_boss_last_hp"),
            ("potion_use_steps", "last_act1_boss_potion_use_steps"),
        ):
            if source in boss:
                fields[target] = boss.get(source)
    return fields


def _single_manifest_item(manifest: dict[str, Any]) -> dict[str, Any] | None:
    categories = manifest.get("categories") if isinstance(manifest.get("categories"), dict) else {}
    for category in ("infra_blocked", "diagnostic_excluded", "clean_trainable"):
        rows = categories.get(category) or []
        if rows:
            row = rows[0]
            return row if isinstance(row, dict) else None
    return None


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
