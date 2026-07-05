"""Read local Slay the Spire unlock and ascension progress."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GAME_DIR = Path("E:/steamApp/steamapps/common/SlayTheSpire")

CHARACTER_DATA_FILES = {
    "IRONCLAD": "STSDataVagabond",
    "SILENT": "STSDataTheSilent",
    "DEFECT": "STSDataDefect",
    "WATCHER": "STSDataWatcher",
}


@dataclass(frozen=True)
class CharacterUnlock:
    character: str
    unlocked_ascension: int
    last_ascension: int
    wins: int
    highest_floor: int


def read_unlocks(game_dir: Path = DEFAULT_GAME_DIR) -> dict[str, CharacterUnlock]:
    preferences = game_dir / "preferences"
    result: dict[str, CharacterUnlock] = {}
    for character, filename in CHARACTER_DATA_FILES.items():
        data = _read_json(preferences / filename)
        unlocked = int(data.get("ASCENSION_LEVEL", 0) or 0)
        result[character] = CharacterUnlock(
            character=character,
            unlocked_ascension=unlocked,
            last_ascension=int(data.get("LAST_ASCENSION_LEVEL", unlocked) or unlocked),
            wins=int(data.get("WIN_COUNT", 0) or 0),
            highest_floor=int(data.get("HIGHEST_FLOOR", 0) or 0),
        )
    return result


def _read_json(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

