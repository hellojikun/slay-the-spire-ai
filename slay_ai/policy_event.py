"""Event and hand-selection policy."""

from __future__ import annotations

import re
from typing import Any

from .memory import StrategyMemory, normalize_card_name
from .policy_decision import Decision


class EventPolicy:
    def __init__(self, memory: StrategyMemory, character: str = "IRONCLAD") -> None:
        self.memory = memory
        self.character = character

    def decide_hand_select(self, game: dict[str, Any]) -> Decision:
        screen_state = game.get("screen_state", {})
        hand = screen_state.get("hand", [])
        max_cards = int(screen_state.get("max_cards", 1) or 1)
        ranked = sorted(
            (
                (self.memory.card_score(_card_key(card), self.character), index, card)
                for index, card in enumerate(hand, start=1)
            ),
            key=lambda item: item[0],
        )
        drop = [index for _, index, _ in ranked[:max_cards]]
        return Decision([{"action": "select_cards", "drop": drop}], f"Drop weakest hand cards {drop}.")

    def decide_event(self, game: dict[str, Any]) -> Decision:
        options = game.get("screen_state", {}).get("options", [])
        enabled = [opt for opt in options if not opt.get("disabled")]
        if not enabled:
            return Decision([], "Event has no enabled option.")
        hp_ratio = _hp_ratio(game)
        current_hp = int(game.get("current_hp", 0) or 0)
        best = None
        for option in enabled:
            text = _option_text(option)
            score = 50
            hp_loss = _event_hp_loss(text)
            if _text_has_any(text, _SAFE_EVENT_WORDS):
                score += 8
            if _text_has_any(text, _HEAL_EVENT_WORDS):
                score += 20 if hp_ratio < 0.65 else 5
            if hp_loss > 0:
                if current_hp and hp_loss >= current_hp:
                    score -= 100
                elif hp_ratio < 0.25:
                    score -= 65
                elif hp_ratio < 0.45:
                    score -= 35
                else:
                    score -= min(20, hp_loss)
            if _text_has_any(text, _DANGEROUS_EVENT_WORDS):
                score -= 45 if hp_ratio < 0.45 else 12
            if _text_has_any(text, _REWARD_EVENT_WORDS):
                score += 15
            if best is None or score > best[0]:
                best = (score, option)
        assert best is not None
        choice_index = int(best[1].get("choice_index", 0)) + 1
        return Decision(
            [{"action": "choose", "choice_index": choice_index}],
            f"Event option score {best[0]:.1f}: {best[1].get('label')}.",
        )


_SAFE_EVENT_WORDS = (
    "leave",
    "ignore",
    "skip",
    "depart",
    "continue",
    "离开",
    "離開",
    "无视",
    "無視",
    "跳过",
    "跳過",
    "继续",
    "繼續",
)
_HEAL_EVENT_WORDS = ("heal", "healing", "治疗", "治療", "回复", "恢復", "恢复")
_DANGEROUS_EVENT_WORDS = (
    "fight",
    "combat",
    "battle",
    "attack",
    "stomp",
    "smash",
    "lose",
    "damage",
    "curse",
    "sacrifice",
    "战斗",
    "戰鬥",
    "攻击",
    "攻擊",
    "踩扁",
    "失去",
    "损失",
    "損失",
    "伤害",
    "傷害",
    "诅咒",
    "詛咒",
    "献祭",
    "獻祭",
)
_REWARD_EVENT_WORDS = (
    "remove",
    "upgrade",
    "transform",
    "relic",
    "gold",
    "card",
    "移除",
    "升级",
    "升級",
    "变化",
    "變化",
    "遗物",
    "遺物",
    "金币",
    "金幣",
    "卡牌",
)


def _card_key(card: dict[str, Any]) -> str:
    return normalize_card_name(str(card.get("id") or card.get("name") or ""))


def _hp_ratio(obj: dict[str, Any]) -> float:
    current = float(obj.get("current_hp", 1) or 1)
    max_hp = float(obj.get("max_hp", current) or current)
    return current / max(max_hp, 1.0)


def _option_text(option: dict[str, Any]) -> str:
    parts = [
        str(option.get("text", "")),
        str(option.get("label", "")),
        str(option.get("description", "")),
    ]
    return " ".join(parts).lower()


def _event_hp_loss(text: str) -> int:
    patterns = (
        r"(?:lose|loss|pay|take)\s*(\d+)\s*(?:hp|health|life)",
        r"(?:失去|损失|損失|支付)\s*(\d+)\s*(?:点)?\s*(?:生命|生命值|血|体力|體力)",
        r"ʧȥ\s*(\d+)\s*������",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def _text_has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word.lower() in text for word in words)
