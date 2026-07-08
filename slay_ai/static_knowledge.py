"""Static game-knowledge tables for feature extraction.

These tables are factual priors used to enrich our own run logs. They are not
training labels: labels still come from clean completed game logs.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_KNOWLEDGE_DIR = Path("data") / "static_knowledge"
REQUIRED_FILES = ("cards.json", "monsters.json", "potions.json", "relics.json", "bosses.json")
ENEMY_PRESSURE_TAG_GROUPS = {
    "enemy_status_pressure_count": {"status_pressure", "burns", "weak", "frail"},
    "enemy_debuff_count": {"anti_skill", "debuff", "vulnerable_pressure", "weak", "frail", "entangle", "confuse"},
    "enemy_scaling_pressure_count": {"scaling_pressure", "on_death_strength"},
    "enemy_split_count": {"split"},
    "enemy_multi_enemy_pressure_count": {"multi_enemy"},
    "enemy_artifact_count": {"artifact"},
    "enemy_potion_tempo_check_count": {"potion_tempo_check", "high_incoming"},
    "enemy_frontload_check_count": {"frontload_check", "burst_window", "burst_damage_check"},
    "enemy_block_check_count": {"block_check", "defense_check"},
    "enemy_aoe_high_value_count": {"aoe_high_value", "aoe_required"},
}


@dataclass(frozen=True)
class StaticKnowledge:
    cards: dict[str, dict[str, Any]]
    monsters: dict[str, dict[str, Any]]
    potions: dict[str, dict[str, Any]]
    relics: dict[str, dict[str, Any]]
    bosses: dict[str, dict[str, Any]]
    source_counts: dict[str, int]

    @classmethod
    def load(cls, root: Path = DEFAULT_KNOWLEDGE_DIR) -> "StaticKnowledge":
        root = Path(root)
        raw_cards = _load_table(root / "cards.json", "cards")
        raw_monsters = _load_table(root / "monsters.json", "monsters")
        raw_potions = _load_table(root / "potions.json", "potions")
        raw_relics = _load_table(root / "relics.json", "relics")
        raw_bosses = _load_table(root / "bosses.json", "bosses")
        cards = _index_rows(raw_cards, required=("id", "name", "type", "tags"))
        monsters = _index_rows(raw_monsters, required=("id", "name", "tags"))
        potions = _index_rows(raw_potions, required=("id", "name", "roles"))
        relics = _index_rows(raw_relics, required=("id", "name", "tags"))
        bosses = _index_rows(raw_bosses, required=("id", "name", "act", "mechanics", "deck_needs", "potion_needs"))
        return cls(
            cards=cards,
            monsters=monsters,
            potions=potions,
            relics=relics,
            bosses=bosses,
            source_counts={
                "cards": len(raw_cards),
                "monsters": len(raw_monsters),
                "potions": len(raw_potions),
                "relics": len(raw_relics),
                "bosses": len(raw_bosses),
            },
        )

    def card_for(self, card: Any) -> dict[str, Any] | None:
        return _lookup(self.cards, _card_entity_keys(card))

    def potion_for(self, potion: Any) -> dict[str, Any] | None:
        return _lookup(self.potions, _entity_keys(potion))

    def monster_for(self, monster: Any) -> dict[str, Any] | None:
        return _lookup(self.monsters, _entity_keys(monster))

    def relic_for(self, relic: Any) -> dict[str, Any] | None:
        return _lookup(self.relics, _entity_keys(relic))

    def boss_for(self, boss: Any) -> dict[str, Any] | None:
        return _lookup(self.bosses, _entity_keys(boss))

    def deck_features(self, deck: Any) -> dict[str, Any]:
        items = deck if isinstance(deck, list) else []
        features: dict[str, Any] = {
            "deck_known_cards": 0,
            "deck_unknown_cards": 0,
            "deck_upgraded_cards": 0,
            "deck_unupgraded_known_cards": 0,
            "deck_total_base_damage": 0,
            "deck_total_base_block": 0,
            "deck_total_current_damage": 0,
            "deck_total_current_block": 0,
            "deck_total_upgraded_damage": 0,
            "deck_total_upgraded_block": 0,
            "deck_total_damage_upgrade_gain": 0,
            "deck_total_block_upgrade_gain": 0,
            "deck_remaining_damage_upgrade_gain": 0,
            "deck_remaining_block_upgrade_gain": 0,
            "deck_total_current_draw": 0,
            "deck_total_current_energy": 0,
            "deck_total_current_vulnerable": 0,
            "deck_total_current_weak": 0,
            "deck_total_current_strength": 0,
            "deck_total_current_strength_down": 0,
            "deck_total_current_strength_per_turn": 0,
            "deck_total_current_energy_per_turn": 0,
            "deck_total_current_end_turn_block": 0,
            "deck_total_current_block_on_exhaust": 0,
            "deck_total_self_damage": 0,
            "deck_total_hits": 0,
            "deck_total_exhaust_hand_count": 0,
            "deck_total_max_hp_on_kill": 0,
            "deck_total_base_damage_per_energy": 0,
            "deck_total_upgraded_damage_per_energy": 0,
            "deck_total_damage_per_strike": 0,
            "deck_total_draw_upgrade_gain": 0,
            "deck_total_vulnerable_upgrade_gain": 0,
            "deck_total_weak_upgrade_gain": 0,
            "deck_total_strength_upgrade_gain": 0,
            "deck_total_strength_down_upgrade_gain": 0,
            "deck_total_damage_per_strike_upgrade_gain": 0,
            "deck_remaining_draw_upgrade_gain": 0,
            "deck_remaining_vulnerable_upgrade_gain": 0,
            "deck_remaining_weak_upgrade_gain": 0,
            "deck_remaining_strength_upgrade_gain": 0,
            "deck_remaining_strength_down_upgrade_gain": 0,
            "deck_remaining_damage_per_strike_upgrade_gain": 0,
        }
        type_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for item in items:
            card = self.card_for(item)
            if card is None:
                features["deck_unknown_cards"] += 1
                continue
            features["deck_known_cards"] += 1
            card_type = str(card.get("type") or "UNKNOWN").lower()
            type_counts[card_type] = type_counts.get(card_type, 0) + 1
            for tag in _string_list(card.get("tags")):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            values = card.get("values") if isinstance(card.get("values"), dict) else {}
            is_upgraded = _card_is_upgraded(item)
            if is_upgraded:
                features["deck_upgraded_cards"] += 1
            else:
                features["deck_unupgraded_known_cards"] += 1
            has_formula_damage = "base_damage_per_card" in values
            base_damage = 0 if has_formula_damage else _safe_int(values.get("base_damage"))
            base_block = _safe_int(values.get("base_block"))
            upgraded_damage = 0 if has_formula_damage else _upgrade_value(values, "base_damage", "upgraded_damage")
            upgraded_block = _upgrade_value(values, "base_block", "upgraded_block")
            damage_gain = max(0, upgraded_damage - base_damage)
            block_gain = max(0, upgraded_block - base_block)
            draw = _current_value(values, is_upgraded, "draw", "upgraded_draw")
            vulnerable = _current_value(values, is_upgraded, "vulnerable", "upgraded_vulnerable")
            weak = _current_value(values, is_upgraded, "weak", "upgraded_weak")
            strength = _current_value(values, is_upgraded, "strength", "upgraded_strength")
            strength_down = _current_value(values, is_upgraded, "strength_down", "upgraded_strength_down")
            strength_per_turn = _current_value(values, is_upgraded, "strength_per_turn", "upgraded_strength_per_turn")
            energy_per_turn = _current_value(values, is_upgraded, "energy_per_turn", "upgraded_energy_per_turn")
            end_turn_block = _current_value(values, is_upgraded, "end_turn_block", "upgraded_end_turn_block")
            block_on_exhaust = _current_value(values, is_upgraded, "block_on_exhaust", "upgraded_block_on_exhaust")
            max_hp_on_kill = _current_value(values, is_upgraded, "max_hp_on_kill", "upgraded_max_hp_on_kill")
            damage_per_strike = _current_value(values, is_upgraded, "damage_per_strike", "upgraded_damage_per_strike")
            draw_gain = _upgrade_gain(values, "draw", "upgraded_draw")
            vulnerable_gain = _upgrade_gain(values, "vulnerable", "upgraded_vulnerable")
            weak_gain = _upgrade_gain(values, "weak", "upgraded_weak")
            strength_gain = _upgrade_gain(values, "strength", "upgraded_strength")
            strength_down_gain = _upgrade_gain(values, "strength_down", "upgraded_strength_down")
            damage_per_strike_gain = _upgrade_gain(values, "damage_per_strike", "upgraded_damage_per_strike")
            features["deck_total_base_damage"] += base_damage
            features["deck_total_base_block"] += base_block
            features["deck_total_upgraded_damage"] += upgraded_damage
            features["deck_total_upgraded_block"] += upgraded_block
            features["deck_total_current_damage"] += upgraded_damage if is_upgraded else base_damage
            features["deck_total_current_block"] += upgraded_block if is_upgraded else base_block
            features["deck_total_damage_upgrade_gain"] += damage_gain
            features["deck_total_block_upgrade_gain"] += block_gain
            features["deck_total_current_draw"] += draw
            features["deck_total_current_energy"] += _safe_int(values.get("energy"))
            features["deck_total_current_vulnerable"] += vulnerable
            features["deck_total_current_weak"] += weak
            features["deck_total_current_strength"] += strength
            features["deck_total_current_strength_down"] += strength_down
            features["deck_total_current_strength_per_turn"] += strength_per_turn
            features["deck_total_current_energy_per_turn"] += energy_per_turn
            features["deck_total_current_end_turn_block"] += end_turn_block
            features["deck_total_current_block_on_exhaust"] += block_on_exhaust
            features["deck_total_self_damage"] += _safe_int(values.get("self_damage"))
            features["deck_total_hits"] += _safe_int(values.get("hits"))
            if bool(values.get("exhaust_hand_cards")):
                features["deck_total_exhaust_hand_count"] += 1
            features["deck_total_max_hp_on_kill"] += max_hp_on_kill
            features["deck_total_base_damage_per_energy"] += _safe_int(values.get("base_damage_per_energy"))
            features["deck_total_upgraded_damage_per_energy"] += _safe_int(values.get("upgraded_damage_per_energy"))
            features["deck_total_damage_per_strike"] += damage_per_strike
            features["deck_total_draw_upgrade_gain"] += draw_gain
            features["deck_total_vulnerable_upgrade_gain"] += vulnerable_gain
            features["deck_total_weak_upgrade_gain"] += weak_gain
            features["deck_total_strength_upgrade_gain"] += strength_gain
            features["deck_total_strength_down_upgrade_gain"] += strength_down_gain
            features["deck_total_damage_per_strike_upgrade_gain"] += damage_per_strike_gain
            if not is_upgraded:
                features["deck_remaining_damage_upgrade_gain"] += damage_gain
                features["deck_remaining_block_upgrade_gain"] += block_gain
                features["deck_remaining_draw_upgrade_gain"] += draw_gain
                features["deck_remaining_vulnerable_upgrade_gain"] += vulnerable_gain
                features["deck_remaining_weak_upgrade_gain"] += weak_gain
                features["deck_remaining_strength_upgrade_gain"] += strength_gain
                features["deck_remaining_strength_down_upgrade_gain"] += strength_down_gain
                features["deck_remaining_damage_per_strike_upgrade_gain"] += damage_per_strike_gain
        for card_type, count in sorted(type_counts.items()):
            features[f"deck_{card_type}_cards"] = count
        for tag, count in sorted(tag_counts.items()):
            features[f"deck_tag_{tag}"] = count
        return features

    def potion_features(self, potions: Any) -> dict[str, Any]:
        items = potions if isinstance(potions, list) else []
        features: dict[str, Any] = {
            "potion_known_count": 0,
            "potion_unknown_count": 0,
            "potion_block_value": 0,
            "potion_damage_value": 0,
            "potion_energy_value": 0,
            "potion_draw_value": 0,
            "potion_generated_options_value": 0,
            "potion_play_top_cards_value": 0,
            "potion_strength_value": 0,
            "potion_temporary_strength_value": 0,
            "potion_dexterity_value": 0,
            "potion_temporary_dexterity_value": 0,
            "potion_plated_armor_value": 0,
            "potion_vulnerable_value": 0,
            "potion_weak_value": 0,
            "potion_artifact_value": 0,
            "potion_ritual_value": 0,
            "potion_healing_value": 0,
            "potion_heal_percent_max_hp": 0,
            "potion_revive_percent_max_hp": 0,
            "potion_max_hp_value": 0,
            "potion_targeted_count": 0,
            "potion_noncombat_count": 0,
            "potion_exhaust_hand_count": 0,
            "potion_upgrade_hand_count": 0,
            "has_liquid_memories": False,
        }
        role_counts: dict[str, int] = {}
        for item in items:
            potion = self.potion_for(item)
            if potion is None:
                features["potion_unknown_count"] += 1
                continue
            features["potion_known_count"] += 1
            if _norm(potion.get("id")) == "liquidmemories":
                features["has_liquid_memories"] = True
            if bool(potion.get("requires_target")):
                features["potion_targeted_count"] += 1
            if not bool(potion.get("combat_only", True)):
                features["potion_noncombat_count"] += 1
            for role in _string_list(potion.get("roles")):
                role_counts[role] = role_counts.get(role, 0) + 1
            values = potion.get("values") if isinstance(potion.get("values"), dict) else {}
            features["potion_block_value"] += _safe_int(values.get("block"))
            features["potion_damage_value"] += _safe_int(values.get("damage"))
            features["potion_energy_value"] += _safe_int(values.get("energy"))
            features["potion_draw_value"] += _safe_int(values.get("draw"))
            features["potion_generated_options_value"] += _safe_int(values.get("generated_options"))
            features["potion_play_top_cards_value"] += _safe_int(values.get("play_top_cards"))
            features["potion_strength_value"] += _safe_int(values.get("strength"))
            features["potion_temporary_strength_value"] += _safe_int(values.get("temporary_strength"))
            features["potion_dexterity_value"] += _safe_int(values.get("dexterity"))
            features["potion_temporary_dexterity_value"] += _safe_int(values.get("temporary_dexterity"))
            features["potion_plated_armor_value"] += _safe_int(values.get("plated_armor"))
            features["potion_vulnerable_value"] += _safe_int(values.get("vulnerable"))
            features["potion_weak_value"] += _safe_int(values.get("weak"))
            features["potion_artifact_value"] += _safe_int(values.get("artifact"))
            features["potion_ritual_value"] += _safe_int(values.get("ritual"))
            features["potion_healing_value"] += _safe_int(values.get("healing_over_time"))
            features["potion_heal_percent_max_hp"] += _safe_int(values.get("heal_percent_max_hp"))
            features["potion_revive_percent_max_hp"] += _safe_int(values.get("revive_percent_max_hp"))
            features["potion_max_hp_value"] += _safe_int(values.get("max_hp"))
            if bool(values.get("exhaust_hand_cards")):
                features["potion_exhaust_hand_count"] += 1
            if bool(values.get("upgrade_hand_cards")):
                features["potion_upgrade_hand_count"] += 1
        for role, count in sorted(role_counts.items()):
            features[f"potion_role_{role}"] = count
        return features

    def monster_features(self, monsters: Any) -> dict[str, Any]:
        items = monsters if isinstance(monsters, list) else []
        features: dict[str, Any] = {
            "enemy_known_count": 0,
            "enemy_unknown_count": 0,
            "enemy_max_expected_attack": 0,
            "enemy_total_expected_attack": 0,
            "enemy_average_expected_attack": 0.0,
            "enemy_boss_count": 0,
            "enemy_elite_count": 0,
            "enemy_elite_or_boss_count": 0,
            "enemy_multi_hit_count": 0,
            **{feature: 0 for feature in ENEMY_PRESSURE_TAG_GROUPS},
        }
        tag_counts: dict[str, int] = {}
        act_counts: dict[str, int] = {}
        for item in items:
            monster = self.monster_for(item)
            if monster is None:
                features["enemy_unknown_count"] += 1
                continue
            features["enemy_known_count"] += 1
            act = _safe_int(monster.get("act"))
            if act > 0:
                act_counts[str(act)] = act_counts.get(str(act), 0) + 1
            if bool(monster.get("boss")):
                features["enemy_boss_count"] += 1
            if bool(monster.get("elite")):
                features["enemy_elite_count"] += 1
            if bool(monster.get("boss")) or bool(monster.get("elite")):
                features["enemy_elite_or_boss_count"] += 1
            if bool(monster.get("multi_hit")):
                features["enemy_multi_hit_count"] += 1
            monster_tags = set(_string_list(monster.get("tags")))
            for feature, tags in ENEMY_PRESSURE_TAG_GROUPS.items():
                if monster_tags.intersection(tags):
                    features[feature] += 1
            values = monster.get("values") if isinstance(monster.get("values"), dict) else {}
            expected_attack = _safe_int(values.get("max_expected_attack"))
            features["enemy_total_expected_attack"] += expected_attack
            features["enemy_max_expected_attack"] = max(
                features["enemy_max_expected_attack"],
                expected_attack,
            )
            for tag in sorted(monster_tags):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if features["enemy_known_count"]:
            features["enemy_average_expected_attack"] = round(
                features["enemy_total_expected_attack"] / features["enemy_known_count"],
                3,
            )
        for act, count in sorted(act_counts.items()):
            features[f"enemy_act_{act}_count"] = count
        for tag, count in sorted(tag_counts.items()):
            features[f"enemy_tag_{tag}"] = count
        return features

    def relic_features(self, relics: Any) -> dict[str, Any]:
        items = relics if isinstance(relics, list) else []
        features: dict[str, Any] = {
            "relic_known_count": 0,
            "relic_unknown_count": 0,
            "relic_combat_block_value": 0,
            "relic_turn_three_block_value": 0,
            "relic_strength_value": 0,
            "relic_damage_bonus_value": 0,
            "relic_thorns_value": 0,
            "relic_healing_value": 0,
            "relic_heal_below_half_value": 0,
            "relic_rest_heal_bonus_value": 0,
            "relic_turn_one_energy_value": 0,
            "relic_energy_per_turn_value": 0,
            "relic_energy_in_elite_or_boss_value": 0,
            "relic_energy_every_attacks_value": 0,
            "relic_draw_value": 0,
            "relic_draw_every_cards_value": 0,
            "relic_opening_hand_filter_count": 0,
            "relic_vulnerable_value": 0,
            "relic_elite_hp_damage_percent_value": 0,
            "relic_potion_slots_value": 0,
            "relic_gold_value": 0,
            "relic_upgrade_attack_count": 0,
            "relic_bonus_chest_relics_value": 0,
            "relic_bonus_chests_value": 0,
            "relic_curse_playable_count": 0,
            "relic_curse_negate_count": 0,
            "relic_damage_prevent_count": 0,
            "relic_self_damage_value": 0,
            "relic_card_remove_cost_value": 0,
            "relic_max_hp_on_skip_value": 0,
            "relic_vulnerable_damage_multiplier_bonus_value": 0,
            "relic_weak_when_vulnerable_value": 0,
            "relic_damage_all_on_exhaust_value": 0,
            "relic_enemy_strength_per_turn_value": 0,
            "relic_x_cost_bonus_value": 0,
            "relic_skills_per_trigger_value": 0,
            "relic_aoe_damage_value": 0,
            "has_burning_blood": False,
            "has_preserved_insect": False,
            "has_no_rest_relic": False,
            "has_no_potions_relic": False,
            "has_no_gold_relic": False,
            "has_no_smith_relic": False,
        }
        tag_counts: dict[str, int] = {}
        for item in items:
            relic = self.relic_for(item)
            if relic is None:
                features["relic_unknown_count"] += 1
                continue
            features["relic_known_count"] += 1
            relic_id = _norm(relic.get("id"))
            if relic_id == "burningblood":
                features["has_burning_blood"] = True
            if relic_id == "preservedinsect":
                features["has_preserved_insect"] = True
            for tag in _string_list(relic.get("tags")):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            values = relic.get("values") if isinstance(relic.get("values"), dict) else {}
            features["relic_combat_block_value"] += _safe_int(values.get("combat_block"))
            features["relic_turn_three_block_value"] += _safe_int(values.get("turn_three_block"))
            features["relic_strength_value"] += _safe_int(values.get("strength"))
            features["relic_damage_bonus_value"] += _safe_int(values.get("damage_bonus"))
            features["relic_thorns_value"] += _safe_int(values.get("thorns"))
            features["relic_healing_value"] += _safe_int(values.get("healing_per_combat"))
            features["relic_heal_below_half_value"] += _safe_int(values.get("heal_below_half"))
            features["relic_rest_heal_bonus_value"] += _safe_int(values.get("rest_heal_bonus"))
            features["relic_turn_one_energy_value"] += _safe_int(values.get("turn_one_energy"))
            features["relic_energy_per_turn_value"] += _safe_int(values.get("energy_per_turn"))
            features["relic_energy_in_elite_or_boss_value"] += _safe_int(values.get("energy_in_elite_or_boss"))
            features["relic_energy_every_attacks_value"] += _safe_int(values.get("energy_every_attacks"))
            features["relic_draw_value"] += _safe_int(values.get("draw"))
            features["relic_draw_every_cards_value"] += _safe_int(values.get("draw_every_cards"))
            features["relic_opening_hand_filter_count"] += _safe_int(values.get("opening_discard_redraw"))
            features["relic_vulnerable_value"] += _safe_int(values.get("vulnerable"))
            features["relic_elite_hp_damage_percent_value"] += _safe_int(values.get("elite_hp_damage_percent"))
            features["relic_potion_slots_value"] += _safe_int(values.get("potion_slots"))
            features["relic_gold_value"] += _safe_int(values.get("gold"))
            features["relic_upgrade_attack_count"] += _safe_int(values.get("upgrade_attack_count"))
            features["relic_bonus_chest_relics_value"] += _safe_int(values.get("bonus_chest_relics"))
            features["relic_bonus_chests_value"] += _safe_int(values.get("bonus_chests"))
            features["relic_curse_playable_count"] += _safe_int(values.get("curse_playable"))
            features["relic_curse_negate_count"] += _safe_int(values.get("curse_negate_count"))
            features["relic_damage_prevent_count"] += _safe_int(values.get("damage_prevent_count"))
            features["relic_self_damage_value"] += _safe_int(values.get("self_damage"))
            features["relic_card_remove_cost_value"] += _safe_int(values.get("remove_cost"))
            features["relic_max_hp_on_skip_value"] += _safe_int(values.get("max_hp_on_skip"))
            features["relic_vulnerable_damage_multiplier_bonus_value"] += _safe_int(values.get("vulnerable_damage_multiplier_bonus"))
            features["relic_weak_when_vulnerable_value"] += _safe_int(values.get("weak_when_vulnerable"))
            features["relic_damage_all_on_exhaust_value"] += _safe_int(values.get("damage_all_on_exhaust"))
            features["relic_enemy_strength_per_turn_value"] += _safe_int(values.get("enemy_strength_per_turn"))
            features["relic_x_cost_bonus_value"] += _safe_int(values.get("x_cost_bonus"))
            features["relic_skills_per_trigger_value"] += _safe_int(values.get("skills_per_trigger"))
            features["relic_aoe_damage_value"] += _safe_int(values.get("aoe_damage"))
        for tag, count in sorted(tag_counts.items()):
            if tag == "no_rest":
                features["has_no_rest_relic"] = True
            if tag == "no_potions":
                features["has_no_potions_relic"] = True
            if tag == "no_gold":
                features["has_no_gold_relic"] = True
            if tag == "no_smith":
                features["has_no_smith_relic"] = True
            features[f"relic_tag_{tag}"] = count
        return features

    def boss_features(self, bosses: Any = None, *, act: int | None = None, boss_available: bool = False) -> dict[str, Any]:
        items = _entity_list(bosses)
        features: dict[str, Any] = {
            "boss_identity_known": False,
            "boss_known_count": 0,
            "boss_unknown_count": 0,
            "boss_possible_count": 0,
            "boss_max_expected_attack": 0,
            "boss_total_expected_attack": 0,
            "boss_max_split_threshold_percent": 0,
            "boss_max_mode_shift_threshold": 0,
            "boss_max_sharp_hide_damage": 0,
            "boss_max_hit_count": 0,
            "boss_max_burn_damage": 0,
            "boss_max_upgraded_burn_damage": 0,
            "boss_max_post_split_enemy_count": 0,
        }

        matched: list[dict[str, Any]] = []
        for item in items:
            boss = self.boss_for(item)
            if boss is None:
                if boss_available:
                    features["boss_unknown_count"] += 1
                continue
            matched.append(boss)

        if matched:
            features["boss_identity_known"] = True
            features["boss_known_count"] = len(matched)
            features["boss_possible_count"] = len(matched)
            bosses_for_counts = matched
        elif boss_available and act is not None:
            bosses_for_counts = _unique_rows(row for row in self.bosses.values() if _safe_int(row.get("act")) == act)
            features["boss_possible_count"] = len(bosses_for_counts)
        else:
            bosses_for_counts = []

        mechanic_counts: dict[str, int] = {}
        search_hint_counts: dict[str, int] = {}
        deck_need_counts: dict[str, int] = {}
        potion_need_counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for boss in bosses_for_counts:
            for mechanic in _string_list(boss.get("mechanics")):
                mechanic_counts[mechanic] = mechanic_counts.get(mechanic, 0) + 1
            for hint in _string_list(boss.get("search_hints")):
                search_hint_counts[hint] = search_hint_counts.get(hint, 0) + 1
            for need in _string_list(boss.get("deck_needs")):
                deck_need_counts[need] = deck_need_counts.get(need, 0) + 1
            for need in _string_list(boss.get("potion_needs")):
                potion_need_counts[need] = potion_need_counts.get(need, 0) + 1
            for tag in _string_list(boss.get("tags")):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            values = boss.get("values") if isinstance(boss.get("values"), dict) else {}
            features["boss_max_expected_attack"] = max(
                features["boss_max_expected_attack"],
                _safe_int(values.get("max_expected_attack")),
            )
            features["boss_total_expected_attack"] += _safe_int(values.get("max_expected_attack"))
            features["boss_max_split_threshold_percent"] = max(
                features["boss_max_split_threshold_percent"],
                _safe_int(values.get("split_threshold_percent")),
            )
            features["boss_max_mode_shift_threshold"] = max(
                features["boss_max_mode_shift_threshold"],
                _safe_int(values.get("mode_shift_threshold")),
            )
            features["boss_max_sharp_hide_damage"] = max(
                features["boss_max_sharp_hide_damage"],
                _safe_int(values.get("sharp_hide_damage")),
            )
            features["boss_max_hit_count"] = max(
                features["boss_max_hit_count"],
                _safe_int(values.get("max_hit_count")),
            )
            features["boss_max_burn_damage"] = max(
                features["boss_max_burn_damage"],
                _safe_int(values.get("burn_damage")),
            )
            features["boss_max_upgraded_burn_damage"] = max(
                features["boss_max_upgraded_burn_damage"],
                _safe_int(values.get("upgraded_burn_damage")),
            )
            features["boss_max_post_split_enemy_count"] = max(
                features["boss_max_post_split_enemy_count"],
                _safe_int(values.get("post_split_enemy_count")),
            )
        for mechanic, count in sorted(mechanic_counts.items()):
            features[f"boss_mechanic_{mechanic}"] = count
        for hint, count in sorted(search_hint_counts.items()):
            features[f"boss_search_hint_{hint}"] = count
        for need, count in sorted(deck_need_counts.items()):
            features[f"boss_need_{need}"] = count
        for need, count in sorted(potion_need_counts.items()):
            features[f"boss_potion_need_{need}"] = count
        for tag, count in sorted(tag_counts.items()):
            features[f"boss_tag_{tag}"] = count
        return features


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize static Slay the Spire knowledge tables.")
    parser.add_argument("--knowledge-dir", type=Path, default=DEFAULT_KNOWLEDGE_DIR)
    args = parser.parse_args(argv)
    knowledge = StaticKnowledge.load(args.knowledge_dir)
    print(
        "Loaded static knowledge: "
        f"{knowledge.source_counts['cards']} cards, "
        f"{knowledge.source_counts['monsters']} monsters, "
        f"{knowledge.source_counts['potions']} potions, "
        f"{knowledge.source_counts['relics']} relics, "
        f"{knowledge.source_counts['bosses']} bosses."
    )
    return 0


def _load_table(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing static knowledge file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain a list at key {key!r}")
    return rows


def _index_rows(rows: Iterable[dict[str, Any]], *, required: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Static knowledge rows must be objects")
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"Static knowledge row missing required keys {missing}: {row}")
        keys = set(_entity_keys(row))
        for alias in _string_list(row.get("aliases")):
            keys.add(_norm(alias))
        keys.discard("")
        for key in keys:
            existing = index.get(key)
            if existing is not None and existing is not row:
                raise ValueError(f"Duplicate static knowledge key {key!r}")
            index[key] = row
    return index


def _lookup(index: dict[str, dict[str, Any]], keys: Iterable[str]) -> dict[str, Any] | None:
    for key in keys:
        if key in index:
            return index[key]
    return None


_ENTITY_ID_FIELDS = (
    "id",
    "name",
    "card_id",
    "relic_id",
    "potion_id",
    "monster_id",
    "boss_id",
)


def _entity_keys(entity: Any) -> list[str]:
    if isinstance(entity, dict):
        return _dedupe(_norm(entity.get(field)) for field in _ENTITY_ID_FIELDS if field in entity)
    return [_norm(entity)]


def _card_entity_keys(card: Any) -> list[str]:
    keys = _entity_keys(card)
    if isinstance(card, dict):
        for field in ("id", "name", "card_id"):
            keys.extend(_base_card_keys(card.get(field)))
    else:
        keys.extend(_base_card_keys(card))
    return _dedupe(key for key in keys if key)


def _base_card_keys(value: Any) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    stripped = raw
    if "+" in stripped:
        stripped = stripped.split("+", 1)[0]
    stripped = stripped.removesuffix("_P")
    return [_norm(stripped)]


def _card_is_upgraded(card: Any) -> bool:
    if isinstance(card, dict):
        if bool(card.get("upgraded")):
            return True
        for key in ("upgrades", "times_upgraded", "misc"):
            value = card.get(key)
            if _safe_int(value) > 0:
                return True
        return any("+" in str(card.get(key) or "") for key in ("id", "name"))
    return "+" in str(card or "")


def _upgrade_value(values: dict[str, Any], base_key: str, upgraded_key: str) -> int:
    base = _safe_int(values.get(base_key))
    upgraded = _safe_int(values.get(upgraded_key))
    return upgraded if upgraded > 0 else base


def _current_value(values: dict[str, Any], is_upgraded: bool, base_key: str, upgraded_key: str) -> int:
    base = _safe_int(values.get(base_key))
    upgraded = _safe_int(values.get(upgraded_key))
    return upgraded if is_upgraded and upgraded > 0 else base


def _upgrade_gain(values: dict[str, Any], base_key: str, upgraded_key: str) -> int:
    base = _safe_int(values.get(base_key))
    upgraded = _safe_int(values.get(upgraded_key))
    return max(0, upgraded - base)


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _entity_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _unique_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        marker = id(row)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(row)
    return unique


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip().lower().replace(" ", "_") for item in value if str(item).strip()]


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
