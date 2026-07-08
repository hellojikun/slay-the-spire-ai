"""Rest site and grid-selection policy."""

from __future__ import annotations

from typing import Any

from .memory import StrategyMemory, normalize_card_name
from .policy_decision import Decision


class RestGridPolicy:
    def __init__(self, memory: StrategyMemory, character: str = "IRONCLAD") -> None:
        self.memory = memory
        self.character = character

    def decide_rest(self, game: dict[str, Any]) -> Decision:
        options = game.get("screen_state", {}).get("rest_options", [])
        hp_ratio = _hp_ratio(game)
        labels = [str(opt).lower() for opt in options]
        rest_index = _rest_option_index(labels)
        if game.get("room_phase") == "COMPLETE":
            return Decision([{"action": "proceed"}], "Rest site complete.")
        if rest_index and hp_ratio < 0.42:
            return Decision([{"action": "choose", "choice_index": rest_index}], "Low HP; rest.")
        if rest_index and _should_rest_before_act1_danger(game, hp_ratio):
            return Decision([{"action": "choose", "choice_index": rest_index}], "Act 1 risk is high without potions; rest.")
        if rest_index and _should_rest_before_act2_danger(game, hp_ratio):
            return Decision([{"action": "choose", "choice_index": rest_index}], "Act 2 buffer is low; rest.")
        for index, label in enumerate(labels, start=1):
            if "smith" in label or "upgrade" in label:
                if not rest_index and hp_ratio < 0.45 and _has_no_rest_relic(game):
                    return Decision(
                        [{"action": "choose", "choice_index": index}],
                        "Cannot rest with Coffee Dripper; smith is forced despite low HP.",
                        metadata={"rest_blocked_by_relic": "Coffee Dripper", "low_hp_forced_smith": True},
                    )
                return Decision([{"action": "choose", "choice_index": index}], "HP is safe; smith.")
        return Decision([{"action": "choose", "choice_index": 1}], "Use first rest option.")

    def decide_grid(self, game: dict[str, Any]) -> Decision:
        screen_state = game.get("screen_state", {})
        cards = screen_state.get("cards", [])
        selected_cards = screen_state.get("selected_cards", [])
        if screen_state.get("confirm_up") or _grid_selection_complete(screen_state):
            return Decision([{"action": "confirm"}], "Confirm grid selection.")
        if (
            selected_cards
            and game.get("room_phase") != "EVENT"
            and not screen_state.get("for_upgrade")
            and not screen_state.get("for_purge")
            and not screen_state.get("for_transform")
            and _grid_existing_selection_ready_to_confirm(screen_state)
        ):
            return Decision([{"action": "confirm"}], "Confirm selected grid card.")
        if not cards:
            return Decision([{"action": "confirm"}], "No grid cards; confirm.")
        candidates = _unselected_grid_cards(cards, selected_cards)
        if screen_state.get("for_upgrade"):
            ranked = [
                (self.memory.upgrade_score(_card_key(card), self.character), index, card)
                for index, card in candidates
            ]
        elif (
            screen_state.get("for_purge")
            or screen_state.get("for_transform")
            or game.get("room_phase") == "EVENT"
            or _grid_needs_multiple_low_value_choices(screen_state)
        ):
            ranked = [
                (-self.memory.card_score(_card_key(card), self.character), index, card)
                for index, card in candidates
            ]
        else:
            ranked = [
                (self.memory.card_score(_card_key(card), self.character), index, card)
                for index, card in candidates
            ]
        if not ranked:
            return Decision([{"action": "confirm"}], "No unselected grid cards; confirm.")
        score, index, card = max(ranked, key=lambda item: item[0])
        return Decision([{"action": "choose", "choice_index": index}], f"Grid choose {card.get('name')} score {score:.1f}.")


def _card_key(card: dict[str, Any]) -> str:
    return normalize_card_name(str(card.get("id") or card.get("name") or ""))


def _hp_ratio(obj: dict[str, Any]) -> float:
    current = float(obj.get("current_hp", 1) or 1)
    max_hp = float(obj.get("max_hp", current) or current)
    return current / max(max_hp, 1.0)


def _grid_selection_complete(screen_state: dict[str, Any]) -> bool:
    needed = _grid_selection_needed(screen_state)
    if needed <= 0:
        return False
    selected = screen_state.get("selected_cards", [])
    return isinstance(selected, list) and len(selected) >= needed


def _grid_selection_needed(screen_state: dict[str, Any]) -> int:
    try:
        return int(screen_state.get("num_cards", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _grid_existing_selection_ready_to_confirm(screen_state: dict[str, Any]) -> bool:
    needed = _grid_selection_needed(screen_state)
    if needed <= 0:
        return True
    selected = screen_state.get("selected_cards", [])
    selected_count = len(selected) if isinstance(selected, list) else 0
    if selected_count >= needed:
        return True
    cards = screen_state.get("cards", [])
    return not _unselected_grid_cards(cards, selected if isinstance(selected, list) else [])


def _grid_needs_multiple_low_value_choices(screen_state: dict[str, Any]) -> bool:
    return _grid_selection_needed(screen_state) > 1


def _unselected_grid_cards(cards: Any, selected_cards: Any) -> list[tuple[int, dict[str, Any]]]:
    if not isinstance(cards, list):
        return []
    if not isinstance(selected_cards, list):
        selected_cards = []
    selected_uuids = {
        card.get("uuid")
        for card in selected_cards
        if isinstance(card, dict) and card.get("uuid")
    }
    selected_keys = {_card_key(card) for card in selected_cards if isinstance(card, dict)}
    return [
        (index, card)
        for index, card in enumerate(cards, start=1)
        if isinstance(card, dict)
        and (not card.get("uuid") or card.get("uuid") not in selected_uuids)
        and _card_key(card) not in selected_keys
    ]


def _rest_option_index(labels: list[str]) -> int | None:
    for index, label in enumerate(labels, start=1):
        if "rest" in label or "sleep" in label:
            return index
    return None


def _should_rest_before_act1_danger(game: dict[str, Any], hp_ratio: float) -> bool:
    if int(game.get("act", 1) or 1) != 1:
        return False
    if int(game.get("floor", 0) or 0) < 5:
        return False
    if hp_ratio >= 0.78:
        return False
    if hp_ratio < 0.70:
        return True
    return not _has_elite_tempo_potion(game)


def _should_rest_before_act2_danger(game: dict[str, Any], hp_ratio: float) -> bool:
    if int(game.get("act", 1) or 1) != 2:
        return False
    floor = int(game.get("floor", 0) or 0)
    if floor >= 31 and hp_ratio < 0.75:
        return True
    return floor >= 23 and hp_ratio < 0.70


def _has_elite_tempo_potion(game: dict[str, Any]) -> bool:
    tempo_tokens = {
        "attack",
        "bronze",
        "cultist",
        "distilledchaos",
        "duplication",
        "essenceofsteel",
        "explosive",
        "fear",
        "fire",
        "forge",
        "heartofiron",
        "liquidbronze",
        "power",
        "steroid",
        "strength",
        "thorn",
        "weak",
        "blessingoftheforge",
    }
    for potion in game.get("potions", []):
        if potion.get("is_empty"):
            continue
        key = _potion_key(potion)
        if any(token in key for token in tempo_tokens):
            return True
    return False


def _potion_key(potion: dict[str, Any]) -> str:
    return "".join(ch for ch in str(potion.get("id") or potion.get("name") or "").lower() if ch.isalnum())


def _has_no_rest_relic(game: dict[str, Any]) -> bool:
    for relic in _iter_relic_items(game):
        key = _relic_key(relic)
        if key == "coffeedripper":
            return True
    return False


def _iter_relic_items(game: dict[str, Any]) -> list[Any]:
    items: list[Any] = []
    for field in ("relic_items", "relics"):
        raw = game.get(field)
        if isinstance(raw, list):
            items.extend(raw)
    return items


def _relic_key(relic: Any) -> str:
    if isinstance(relic, dict):
        raw = relic.get("id") or relic.get("name") or relic.get("relic_id") or ""
    else:
        raw = relic
    return "".join(ch for ch in str(raw).lower() if ch.isalnum())
