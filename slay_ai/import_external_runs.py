"""Import external Slay the Spire run-history data into isolated prior rows."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO


CARD_PRIOR_ROWS_FILE = "card_reward_priors.jsonl"
EXTERNAL_RUN_HISTORY = "external_run_history"
EXTERNAL_REFERENCE = "external_reference"
REJECTED_EXTERNAL = "rejected_external"
EXTERNAL_PRIOR_GRADE = "external_prior"
SUPPORTED_SUFFIXES = {".json", ".jsonl", ".run", ".gz"}


@dataclass
class ImportResult:
    manifest: dict[str, Any]
    card_prior_rows: list[dict[str, Any]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import external Slay the Spire run history as isolated prior rows.")
    parser.add_argument("inputs", nargs="+", type=Path, help="External JSON/JSONL/.run files or directories.")
    parser.add_argument("--source", required=True, help="Stable source id, such as 77m_metrics_dump_sample.")
    parser.add_argument("--source-uri", default=None, help="Source URL or local provenance note.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--character", action="append", help="Keep only these characters. Can be repeated.")
    parser.add_argument("--ascension-min", type=int, default=None)
    parser.add_argument("--ascension-max", type=int, default=None)
    parser.add_argument("--limit-runs", type=int, default=None)
    parser.add_argument("--source-weight", type=float, default=1.0)
    args = parser.parse_args(argv)

    result = import_external_runs(
        args.inputs,
        source_id=args.source,
        source_uri=args.source_uri,
        output_dir=args.output_dir,
        characters=args.character,
        ascension_min=args.ascension_min,
        ascension_max=args.ascension_max,
        limit_runs=args.limit_runs,
        source_weight=args.source_weight,
    )
    manifest_path = args.output_dir / "external_manifest.json"
    print(
        "external_import: "
        f"source={args.source} runs={result.manifest['summary']['accepted_runs']} "
        f"rejected={result.manifest['summary']['rejected_runs']} "
        f"card_rows={result.manifest['summary']['card_prior_rows']}"
    )
    print(f"Wrote manifest: {manifest_path}")
    print(f"Wrote card priors: {args.output_dir / CARD_PRIOR_ROWS_FILE}")
    return 0


def import_external_runs(
    inputs: Iterable[Path],
    *,
    source_id: str,
    output_dir: Path,
    source_uri: str | None = None,
    characters: Iterable[str] | None = None,
    ascension_min: int | None = None,
    ascension_max: int | None = None,
    limit_runs: int | None = None,
    source_weight: float = 1.0,
) -> ImportResult:
    input_paths = [Path(path) for path in inputs]
    resolved_files = list(iter_external_files(input_paths))
    allowed_characters = {_normalize_character(character) for character in characters or []}
    filters = {
        "game": "STS1",
        "characters": sorted(allowed_characters),
        "ascension_min": ascension_min,
        "ascension_max": ascension_max,
        "limit_runs": limit_runs,
    }
    accepted_runs: list[dict[str, Any]] = []
    rejected_runs: list[dict[str, Any]] = []
    card_prior_rows: list[dict[str, Any]] = []
    seen_runs = 0

    for file_path in resolved_files:
        for index, record in enumerate(iter_external_records(file_path), start=1):
            if limit_runs is not None and seen_runs >= limit_runs:
                break
            seen_runs += 1
            run_id = _source_run_hash(source_id, file_path, index, record)
            normalized, reject_reason = normalize_external_run(
                record,
                source_id=source_id,
                source_uri=source_uri,
                source_file=file_path,
                source_index=index,
                source_run_id_hash=run_id,
                source_weight=source_weight,
            )
            if reject_reason is None:
                reject_reason = _filter_reject_reason(
                    normalized,
                    allowed_characters=allowed_characters,
                    ascension_min=ascension_min,
                    ascension_max=ascension_max,
                )
            if reject_reason:
                rejected_runs.append(
                    {
                        "source_file": str(file_path),
                        "source_index": index,
                        "source_run_id_hash": run_id,
                        "reason": reject_reason,
                    }
                )
                if limit_runs is not None and seen_runs >= limit_runs:
                    break
                continue
            accepted_runs.append(_manifest_run_summary(normalized))
            card_prior_rows.extend(_card_prior_rows_from_run(normalized))
            if limit_runs is not None and seen_runs >= limit_runs:
                break
        if limit_runs is not None and seen_runs >= limit_runs:
            break

    output_dir.mkdir(parents=True, exist_ok=True)
    card_rows_path = output_dir / CARD_PRIOR_ROWS_FILE
    _write_jsonl(card_rows_path, card_prior_rows)
    manifest = _build_manifest(
        source_id=source_id,
        source_uri=source_uri,
        input_paths=input_paths,
        resolved_files=resolved_files,
        output_dir=output_dir,
        filters=filters,
        accepted_runs=accepted_runs,
        rejected_runs=rejected_runs,
        card_prior_rows=card_prior_rows,
        source_weight=source_weight,
    )
    manifest_path = output_dir / "external_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ImportResult(manifest=manifest, card_prior_rows=card_prior_rows)


def iter_external_files(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            candidates = sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() and _is_supported_external_file(candidate)
            )
        else:
            candidates = [path] if path.exists() and _is_supported_external_file(path) else []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield candidate


def iter_external_records(path: Path) -> Iterable[dict[str, Any]]:
    with _open_external_text(path) as handle:
        if _is_jsonl_file(path):
            yield from _iter_json_lines_handle(handle, path)
            return

        prefix = handle.read(4096)
        stripped = prefix.lstrip()
        if not stripped:
            return
        if stripped[0] == "[":
            yield from _iter_json_array_stream(_chain_text(prefix, handle), path)
            return
        text = prefix + handle.read()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            yield from _iter_json_lines_text(text, path)
            return
        yield from _records_from_json_payload(payload)
def normalize_external_run(
    record: dict[str, Any],
    *,
    source_id: str,
    source_uri: str | None,
    source_file: Path,
    source_index: int,
    source_run_id_hash: str,
    source_weight: float,
) -> tuple[dict[str, Any], str | None]:
    character = _normalize_character(_first_value(record, "character", "character_chosen", "player_chosen", "class", "hero"))
    ascension = _int_or_none(_first_value(record, "ascension", "ascension_level", "ascensionLevel", "ascension_level_chosen"))
    victory = _bool_or_none(_first_value(record, "victory", "is_victory", "won"))
    final_floor = _int_or_none(_first_value(record, "floor_reached", "floor", "final_floor", "floorReached"))
    score = _int_or_none(_first_value(record, "score"))
    if not character:
        return {}, "missing_character"
    if ascension is None:
        return {}, "missing_ascension"
    if victory is None:
        return {}, "missing_victory"
    if final_floor is None:
        return {}, "missing_final_floor"
    card_choices = list(_extract_card_choices(record))
    return (
        {
            "source_id": source_id,
            "source_uri": source_uri,
            "source_file": str(source_file),
            "source_index": source_index,
            "source_run_id_hash": source_run_id_hash,
            "source_weight": float(source_weight),
            "character": character,
            "ascension": ascension,
            "victory": victory,
            "final_floor": final_floor,
            "score": score,
            "seed": _first_value(record, "seed", "seed_played"),
            "game_version": _first_value(record, "build_version", "game_version", "version"),
            "timestamp": _first_value(record, "timestamp", "local_time", "date"),
            "card_choices": card_choices,
            "raw_run_id": _first_value(record, "id", "run_id", "runId", "play_id", "playId"),
        },
        None,
    )


def _extract_card_choices(record: dict[str, Any]) -> Iterable[dict[str, Any]]:
    raw = _first_value(record, "card_choices", "cardChoices", "card_rewards", "cardRewards", "card_reward_choices")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for index, choice in enumerate(raw, start=1):
        if isinstance(choice, str):
            picked = choice
            not_picked: list[str] = []
            floor = index
        elif isinstance(choice, dict):
            picked = _first_value(choice, "picked", "picked_card", "pickedCard", "choice")
            not_picked = _list_of_text(_first_value(choice, "not_picked", "notPicked", "options", "cards"))
            floor = _int_or_none(_first_value(choice, "floor", "floor_num", "floorNum")) or index
        else:
            continue
        picked_text = _card_text(picked)
        if not picked_text or picked_text.lower() in {"skip", "skipped"}:
            continue
        options = _dedupe_cards([picked_text, *not_picked])
        result.append({"floor": floor, "picked": picked_text, "options": options})
    return result


def _card_prior_rows_from_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for choice in run.get("card_choices", []):
        picked = _card_text(choice.get("picked"))
        if not picked:
            continue
        options = _dedupe_cards(choice.get("options") or [picked])
        rows.append(
            {
                "character": run["character"],
                "ascension": run["ascension"],
                "floor": int(choice.get("floor") or 0),
                "picked": picked,
                "options": options,
                "has_reward_options": len(options) > 1,
                "victory": bool(run["victory"]),
                "final_floor": int(run["final_floor"]),
                "score": run.get("score"),
                "source_category": EXTERNAL_RUN_HISTORY,
                "source_reason": EXTERNAL_PRIOR_GRADE,
                "source_validation_grade": EXTERNAL_PRIOR_GRADE,
                "source_validation_flags": ["external", "not_mcp", "not_pristine"],
                "source_dataset": run["source_id"],
                "source_uri": run.get("source_uri"),
                "source_file": run["source_file"],
                "source_index": run["source_index"],
                "source_run_id_hash": run["source_run_id_hash"],
                "source_weight": float(run.get("source_weight") or 1.0),
                "transform_version": 1,
            }
        )
    return rows


def _build_manifest(
    *,
    source_id: str,
    source_uri: str | None,
    input_paths: list[Path],
    resolved_files: list[Path],
    output_dir: Path,
    filters: dict[str, Any],
    accepted_runs: list[dict[str, Any]],
    rejected_runs: list[dict[str, Any]],
    card_prior_rows: list[dict[str, Any]],
    source_weight: float,
) -> dict[str, Any]:
    warnings: list[str] = []
    if not resolved_files:
        warnings.append("no_resolved_external_files")
    if not card_prior_rows:
        warnings.append("no_card_prior_rows")
    characters = sorted({row["character"] for row in accepted_runs if row.get("character")})
    ascensions = sorted({int(row["ascension"]) for row in accepted_runs if row.get("ascension") is not None})
    return {
        "version": 1,
        "manifest_type": "external_prior_manifest",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_category": EXTERNAL_RUN_HISTORY,
        "source_id": source_id,
        "source_uri": source_uri,
        "source_weight": float(source_weight),
        "inputs": [str(path) for path in input_paths],
        "resolved_files": [str(path) for path in resolved_files],
        "filters": filters,
        "sample_method": "stream_first_n" if filters.get("limit_runs") is not None else "full_stream",
        "artifacts": {
            "card_reward_priors": str(output_dir / CARD_PRIOR_ROWS_FILE),
        },
        "summary": {
            "input_count": len(input_paths),
            "resolved_file_count": len(resolved_files),
            "accepted_runs": len(accepted_runs),
            "rejected_runs": len(rejected_runs),
            "seen_runs": len(accepted_runs) + len(rejected_runs),
            "card_prior_rows": len(card_prior_rows),
            "characters": characters,
            "ascensions": ascensions,
            "warnings": warnings,
        },
        "categories": {
            EXTERNAL_RUN_HISTORY: accepted_runs,
            EXTERNAL_REFERENCE: [],
            REJECTED_EXTERNAL: rejected_runs,
        },
        "permitted_uses": ["shadow_prior", "card_prior", "offline_ablation"],
        "forbidden_uses": ["clean_trainable", "pristine", "gate", "learned_memory", "runtime_authority"],
    }


def _manifest_run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_file": run["source_file"],
        "source_index": run["source_index"],
        "source_run_id_hash": run["source_run_id_hash"],
        "character": run["character"],
        "ascension": run["ascension"],
        "victory": run["victory"],
        "final_floor": run["final_floor"],
        "score": run.get("score"),
        "seed": run.get("seed"),
        "game_version": run.get("game_version"),
        "timestamp": run.get("timestamp"),
        "card_choice_count": len(run.get("card_choices", [])),
        "source_validation_grade": EXTERNAL_PRIOR_GRADE,
    }


def _filter_reject_reason(
    run: dict[str, Any],
    *,
    allowed_characters: set[str],
    ascension_min: int | None,
    ascension_max: int | None,
) -> str | None:
    character = run.get("character")
    ascension = run.get("ascension")
    if allowed_characters and character not in allowed_characters:
        return "filtered_character"
    if ascension_min is not None and ascension < ascension_min:
        return "filtered_ascension"
    if ascension_max is not None and ascension > ascension_max:
        return "filtered_ascension"
    return None


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _is_supported_external_file(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".gz":
        return len(suffixes) >= 2 and suffixes[-2] in {".json", ".jsonl", ".run"}
    return suffixes[-1] in SUPPORTED_SUFFIXES


@contextmanager
def _open_external_text(path: Path) -> Iterator[TextIO]:
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            yield handle
        return
    with path.open("r", encoding="utf-8") as handle:
        yield handle


def _read_external_text(path: Path) -> str:
    with _open_external_text(path) as handle:
        return handle.read()


def _iter_json_lines_text(text: str, path: Path) -> Iterable[dict[str, Any]]:
    for line_number, line in enumerate(text.splitlines(), start=1):
        yield from _parse_json_line(line, path, line_number)


def _iter_json_lines_handle(handle: TextIO, path: Path) -> Iterable[dict[str, Any]]:
    for line_number, line in enumerate(handle, start=1):
        yield from _parse_json_line(line, path, line_number)


def _parse_json_line(line: str, path: Path, line_number: int) -> Iterable[dict[str, Any]]:
    if not line.strip():
        return
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON line in {path} at {line_number}: {exc}") from exc
    if isinstance(payload, dict):
        yield payload


def _records_from_json_payload(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
    elif isinstance(payload, dict):
        runs = payload.get("runs") or payload.get("data")
        if isinstance(runs, list):
            for item in runs:
                if isinstance(item, dict):
                    yield item
        else:
            yield payload


def _iter_json_array_stream(chunks: Iterable[str], path: Path) -> Iterable[dict[str, Any]]:
    decoder = json.JSONDecoder()
    buffer = ""
    pos = 0
    started = False
    finished = False
    for chunk in chunks:
        buffer += chunk
        while True:
            pos = _skip_json_ws(buffer, pos)
            if pos >= len(buffer):
                break
            if not started:
                if buffer[pos] != "[":
                    raise ValueError(f"Expected JSON array in {path}")
                started = True
                pos += 1
                continue
            if buffer[pos] == "]":
                finished = True
                return
            if buffer[pos] == ",":
                pos += 1
                continue
            try:
                payload, end = decoder.raw_decode(buffer, pos)
            except json.JSONDecodeError:
                break
            if isinstance(payload, dict):
                yield payload
            pos = end
        if pos > 65536:
            buffer = buffer[pos:]
            pos = 0
    pos = _skip_json_ws(buffer, pos)
    if started and not finished and pos < len(buffer):
        if buffer[pos] == "]":
            return
        raise ValueError(f"Unfinished JSON array in {path}")


def _chain_text(prefix: str, handle: TextIO, *, chunk_size: int = 65536) -> Iterable[str]:
    yield prefix
    while True:
        chunk = handle.read(chunk_size)
        if not chunk:
            break
        yield chunk


def _skip_json_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _is_jsonl_file(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if suffixes and suffixes[-1] == ".gz":
        suffixes = suffixes[:-1]
    return bool(suffixes and suffixes[-1] == ".jsonl")
def _source_run_hash(source_id: str, path: Path, index: int, record: dict[str, Any]) -> str:
    raw_id = _first_value(record, "id", "run_id", "runId", "play_id", "playId")
    seed = f"{source_id}|{path}|{index}|{raw_id or json.dumps(record, sort_keys=True, ensure_ascii=False)[:500]}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _normalize_character(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    aliases = {
        "THE_IRONCLAD": "IRONCLAD",
        "IRON_CLAD": "IRONCLAD",
        "THE_SILENT": "SILENT",
    }
    return aliases.get(text, text)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "win", "won"}:
        return True
    if text in {"false", "0", "no", "loss", "lost"}:
        return False
    return None


def _list_of_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_card_text(item) for item in value if _card_text(item)]
    text = _card_text(value)
    return [text] if text else []


def _card_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("id") or value.get("name")
    return str(value or "").strip()


def _dedupe_cards(cards: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for card in cards:
        text = _card_text(card)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
