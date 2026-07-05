"""Policy decision data structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Decision:
    actions: list[dict[str, Any]]
    reason: str
    should_stop: bool = False
    learn_card_pick: str | None = None
