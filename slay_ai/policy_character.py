"""Character-specific policy facts used by replaceable strategy modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class CharacterProfile:
    character: str
    starter_strike_cards: frozenset[str]
    starter_defend_cards: frozenset[str]
    self_damage_risk_cards: frozenset[str] = field(default_factory=frozenset)
    immediate_self_damage_engine_cards: frozenset[str] = field(default_factory=frozenset)
    self_damage_hp_cost_cards: dict[str, int] = field(default_factory=dict)
    energy_gain_cards: dict[str, int] = field(default_factory=dict)
    search_protected_single_cards: frozenset[str] = field(default_factory=frozenset)
    attack_block_cards: frozenset[str] = field(default_factory=frozenset)
    duplication_high_value_cards: frozenset[str] = field(default_factory=frozenset)

    def is_starter_strike(self, value: Any) -> bool:
        return starter_card_key(value) in self.starter_strike_cards

    def is_starter_defend(self, value: Any) -> bool:
        return starter_card_key(value) in self.starter_defend_cards


def _card_label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("name") or "")
    return str(value or "")


def starter_card_key(value: Any) -> str:
    return _card_label(value).replace("+", "").strip()


GENERIC_ATTACK_BLOCK_CARDS = frozenset({"Dash", "Iron Wave", "Just Lucky", "Wallop"})

IRONCLAD_PROFILE = CharacterProfile(
    character="IRONCLAD",
    starter_strike_cards=frozenset({starter_card_key("Strike"), starter_card_key("Strike_R")}),
    starter_defend_cards=frozenset({starter_card_key("Defend"), starter_card_key("Defend_R")}),
    self_damage_risk_cards=frozenset({"Bloodletting", "Combust", "Offering"}),
    immediate_self_damage_engine_cards=frozenset({"Bloodletting", "Offering"}),
    self_damage_hp_cost_cards={"Hemokinesis": 2},
    energy_gain_cards={"Seeing Red": 2},
    search_protected_single_cards=frozenset(
        {
            "Battle Trance",
            "Burning Pact",
            "Dark Shackles",
            "Disarm",
            "Intimidate",
            "Offering",
            "Piercing Wail",
            "Shockwave",
        }
    ),
    attack_block_cards=GENERIC_ATTACK_BLOCK_CARDS,
    duplication_high_value_cards=frozenset(
        {
            "Carnage",
            "Disarm",
            "Flame Barrier",
            "Impervious",
            "Perfected Strike",
            "Power Through",
            "Shockwave",
            "Shrug It Off",
            "Uppercut",
        }
    ),
)

SILENT_PROFILE = CharacterProfile(
    character="SILENT",
    starter_strike_cards=frozenset({starter_card_key("Strike"), starter_card_key("Strike_G")}),
    starter_defend_cards=frozenset({starter_card_key("Defend"), starter_card_key("Defend_G")}),
    attack_block_cards=GENERIC_ATTACK_BLOCK_CARDS,
)

DEFECT_PROFILE = CharacterProfile(
    character="DEFECT",
    starter_strike_cards=frozenset({starter_card_key("Strike"), starter_card_key("Strike_B")}),
    starter_defend_cards=frozenset({starter_card_key("Defend"), starter_card_key("Defend_B")}),
    attack_block_cards=GENERIC_ATTACK_BLOCK_CARDS,
)

WATCHER_PROFILE = CharacterProfile(
    character="WATCHER",
    starter_strike_cards=frozenset({starter_card_key("Strike"), starter_card_key("Strike_P")}),
    starter_defend_cards=frozenset({starter_card_key("Defend"), starter_card_key("Defend_P")}),
    attack_block_cards=GENERIC_ATTACK_BLOCK_CARDS,
)

CHARACTER_PROFILES = {
    profile.character: profile
    for profile in (IRONCLAD_PROFILE, SILENT_PROFILE, DEFECT_PROFILE, WATCHER_PROFILE)
}
DEFAULT_CHARACTER_PROFILE = IRONCLAD_PROFILE


def profile_for(character: Any) -> CharacterProfile:
    return CHARACTER_PROFILES.get(str(character or "").upper(), DEFAULT_CHARACTER_PROFILE)
