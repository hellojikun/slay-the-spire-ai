import unittest

from slay_ai.static_knowledge import StaticKnowledge


class StaticKnowledgeTests(unittest.TestCase):
    def test_loads_seed_tables_and_extracts_deck_features(self):
        knowledge = StaticKnowledge.load()

        self.assertGreaterEqual(knowledge.source_counts["cards"], 20)
        self.assertGreaterEqual(knowledge.source_counts["monsters"], 10)
        self.assertGreaterEqual(knowledge.source_counts["potions"], 10)
        self.assertGreaterEqual(knowledge.source_counts["relics"], 10)
        self.assertGreaterEqual(knowledge.source_counts["bosses"], 3)

        features = knowledge.deck_features(["Strike_R", "Defend_R", "Bash", "Clothesline", "Shrug It Off"])

        self.assertEqual(features["deck_known_cards"], 5)
        self.assertEqual(features["deck_unknown_cards"], 0)
        self.assertEqual(features["deck_tag_weak"], 1)
        self.assertEqual(features["deck_tag_vulnerable"], 1)
        self.assertEqual(features["deck_tag_block"], 2)
        self.assertGreaterEqual(features["deck_total_base_damage"], 26)
        self.assertGreaterEqual(features["deck_total_base_block"], 13)
        self.assertEqual(features["deck_upgraded_cards"], 0)
        self.assertEqual(features["deck_unupgraded_known_cards"], 5)
        self.assertGreaterEqual(features["deck_total_current_damage"], features["deck_total_base_damage"])
        self.assertGreater(features["deck_remaining_damage_upgrade_gain"], 0)
        self.assertGreater(features["deck_remaining_block_upgrade_gain"], 0)
        self.assertEqual(features["deck_total_current_vulnerable"], 2)
        self.assertEqual(features["deck_total_current_weak"], 2)
        self.assertEqual(features["deck_total_current_draw"], 1)
        self.assertEqual(features["deck_total_vulnerable_upgrade_gain"], 1)
        self.assertEqual(features["deck_total_weak_upgrade_gain"], 1)
        self.assertEqual(features["deck_total_draw_upgrade_gain"], 0)
        self.assertEqual(features["deck_remaining_vulnerable_upgrade_gain"], 1)
        self.assertEqual(features["deck_remaining_weak_upgrade_gain"], 1)
        self.assertEqual(features["deck_remaining_draw_upgrade_gain"], 0)

    def test_deck_features_account_for_upgraded_card_facts(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.deck_features(
            [
                "Bash+",
                {"id": "Defend_R", "upgrades": 1},
                {"name": "Strike+1"},
                "Clothesline",
            ]
        )

        self.assertEqual(features["deck_known_cards"], 4)
        self.assertEqual(features["deck_unknown_cards"], 0)
        self.assertEqual(features["deck_upgraded_cards"], 3)
        self.assertEqual(features["deck_unupgraded_known_cards"], 1)
        self.assertEqual(features["deck_total_base_damage"], 26)
        self.assertEqual(features["deck_total_current_damage"], 31)
        self.assertEqual(features["deck_total_base_block"], 5)
        self.assertEqual(features["deck_total_current_block"], 8)
        self.assertEqual(features["deck_total_damage_upgrade_gain"], 7)
        self.assertEqual(features["deck_remaining_damage_upgrade_gain"], 2)
        self.assertEqual(features["deck_total_block_upgrade_gain"], 3)
        self.assertEqual(features["deck_remaining_block_upgrade_gain"], 0)
        self.assertEqual(features["deck_total_current_vulnerable"], 3)
        self.assertEqual(features["deck_total_current_weak"], 2)
        self.assertEqual(features["deck_remaining_vulnerable_upgrade_gain"], 0)
        self.assertEqual(features["deck_remaining_weak_upgrade_gain"], 1)

    def test_feature_lookup_accepts_explicit_identity_fields(self):
        knowledge = StaticKnowledge.load()

        deck_features = knowledge.deck_features([{"name": "unmapped display", "card_id": "Bash"}])
        potion_features = knowledge.potion_features([{"name": "unmapped display", "potion_id": "FearPotion"}])
        relic_features = knowledge.relic_features([{"name": "unmapped display", "relic_id": "Anchor"}])

        self.assertEqual(deck_features["deck_known_cards"], 1)
        self.assertEqual(deck_features["deck_total_current_vulnerable"], 2)
        self.assertEqual(potion_features["potion_known_count"], 1)
        self.assertEqual(potion_features["potion_vulnerable_value"], 3)
        self.assertEqual(relic_features["relic_known_count"], 1)
        self.assertEqual(relic_features["relic_combat_block_value"], 10)

    def test_deck_features_extract_card_tempo_and_scaling_values(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.deck_features(
            [
                "Battle Trance",
                "Offering+",
                "Inflame",
                "Disarm+",
                "Seeing Red",
                "Pummel",
                "Perfected Strike",
                "Whirlwind",
            ]
        )

        self.assertEqual(features["deck_known_cards"], 8)
        self.assertEqual(features["deck_total_current_draw"], 8)
        self.assertEqual(features["deck_total_current_energy"], 4)
        self.assertEqual(features["deck_total_current_strength"], 2)
        self.assertEqual(features["deck_total_current_strength_down"], 3)
        self.assertEqual(features["deck_total_self_damage"], 6)
        self.assertEqual(features["deck_total_hits"], 4)
        self.assertEqual(features["deck_total_damage_per_strike"], 2)
        self.assertEqual(features["deck_total_base_damage_per_energy"], 5)
        self.assertEqual(features["deck_total_upgraded_damage_per_energy"], 8)
        self.assertEqual(features["deck_total_draw_upgrade_gain"], 3)
        self.assertEqual(features["deck_remaining_draw_upgrade_gain"], 1)
        self.assertEqual(features["deck_total_strength_upgrade_gain"], 1)
        self.assertEqual(features["deck_remaining_strength_upgrade_gain"], 1)
        self.assertEqual(features["deck_total_strength_down_upgrade_gain"], 1)
        self.assertEqual(features["deck_remaining_strength_down_upgrade_gain"], 0)
        self.assertEqual(features["deck_total_damage_per_strike_upgrade_gain"], 1)
        self.assertEqual(features["deck_remaining_damage_per_strike_upgrade_gain"], 1)

    def test_deck_features_extract_power_and_exhaust_payoff_card_facts(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.deck_features(
            [
                "Feel No Pain+",
                "Sentinel",
                "Warcry+",
                "Demon Form",
                "Berserk",
                "Metallicize+",
                "Fiend Fire",
                "Feed+",
                "Ghostly Armor",
                "Parasite",
            ]
        )

        self.assertEqual(features["deck_known_cards"], 10)
        self.assertEqual(features["deck_unknown_cards"], 0)
        self.assertEqual(features["deck_power_cards"], 4)
        self.assertEqual(features["deck_skill_cards"], 3)
        self.assertEqual(features["deck_attack_cards"], 2)
        self.assertEqual(features["deck_curse_cards"], 1)
        self.assertEqual(features["deck_tag_exhaust_synergy"], 1)
        self.assertEqual(features["deck_tag_exhaust_hand"], 1)
        self.assertEqual(features["deck_tag_max_hp"], 1)
        self.assertEqual(features["deck_total_current_draw"], 2)
        self.assertEqual(features["deck_total_current_strength_per_turn"], 2)
        self.assertEqual(features["deck_total_current_energy_per_turn"], 1)
        self.assertEqual(features["deck_total_current_end_turn_block"], 4)
        self.assertEqual(features["deck_total_current_block_on_exhaust"], 4)
        self.assertEqual(features["deck_total_exhaust_hand_count"], 1)
        self.assertEqual(features["deck_total_max_hp_on_kill"], 4)
        self.assertEqual(features["deck_total_current_block"], 15)
        self.assertEqual(features["deck_total_current_damage"], 12)
        self.assertEqual(features["deck_remaining_block_upgrade_gain"], 6)

    def test_deck_features_cover_remaining_observed_english_card_gaps(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.deck_features(["Rampage", "Fire Breathing", "Decay"])

        self.assertEqual(features["deck_known_cards"], 3)
        self.assertEqual(features["deck_unknown_cards"], 0)
        self.assertEqual(features["deck_attack_cards"], 1)
        self.assertEqual(features["deck_power_cards"], 1)
        self.assertEqual(features["deck_curse_cards"], 1)
        self.assertEqual(features["deck_tag_aoe"], 1)
        self.assertEqual(features["deck_tag_status_synergy"], 1)
        self.assertEqual(features["deck_tag_self_damage"], 1)
        self.assertEqual(features["deck_total_current_damage"], 8)
        self.assertEqual(features["deck_total_self_damage"], 2)

    def test_deck_features_cover_cycle31_parasite_alias(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.deck_features(["瀵勭敓"])

        self.assertEqual(features["deck_known_cards"], 1)
        self.assertEqual(features["deck_unknown_cards"], 0)
        self.assertEqual(features["deck_curse_cards"], 1)
        self.assertEqual(features["deck_tag_max_hp_loss"], 1)

    def test_cycle32_colorless_potion_and_relic_gaps_are_known(self):
        knowledge = StaticKnowledge.load()

        deck_features = knowledge.deck_features(
            ["Violence", "暴力", "HandOfGreed", "贪婪之手", "Magnetism", "磁力", "幽灵铠甲"]
        )
        potion_features = knowledge.potion_features([{"id": "HeartOfIron"}, {"name": "铁之心"}])
        relic_features = knowledge.relic_features(
            [{"id": "Lizard Tail"}, {"id": "LizardTail"}, {"name": "蜥蜴尾巴"}]
        )

        self.assertEqual(deck_features["deck_known_cards"], 7)
        self.assertEqual(deck_features["deck_unknown_cards"], 0)
        self.assertEqual(deck_features["deck_attack_cards"], 2)
        self.assertEqual(deck_features["deck_skill_cards"], 3)
        self.assertEqual(deck_features["deck_power_cards"], 2)
        self.assertEqual(deck_features["deck_tag_tempo"], 2)
        self.assertEqual(potion_features["potion_known_count"], 2)
        self.assertEqual(potion_features["potion_unknown_count"], 0)
        self.assertEqual(potion_features["potion_role_boss_defense"], 2)
        self.assertEqual(relic_features["relic_known_count"], 3)
        self.assertEqual(relic_features["relic_unknown_count"], 0)
        self.assertEqual(relic_features["relic_tag_lethal_prevention"], 3)

    def test_extracts_potion_and_enemy_features(self):
        knowledge = StaticKnowledge.load()

        potion_features = knowledge.potion_features(
            [
                {"id": "LiquidMemories"},
                {"name": "Block Potion"},
                {"id": "SkillPotion"},
                {"id": "CultistPotion"},
                {"id": "SpeedPotion"},
                {"id": "SwiftPotion"},
                {"id": "DistilledChaos"},
            ]
        )
        enemy_features = knowledge.monster_features([{"id": "Hexaghost"}, {"name": "Sentry"}])

        self.assertTrue(potion_features["has_liquid_memories"])
        self.assertEqual(potion_features["potion_role_emergency"], 5)
        self.assertEqual(potion_features["potion_role_scaling"], 1)
        self.assertEqual(potion_features["potion_role_burst_turn"], 2)
        self.assertEqual(potion_features["potion_known_count"], 7)
        self.assertEqual(potion_features["potion_block_value"], 12)
        self.assertEqual(potion_features["potion_draw_value"], 3)
        self.assertEqual(potion_features["potion_generated_options_value"], 3)
        self.assertEqual(potion_features["potion_play_top_cards_value"], 3)
        self.assertEqual(potion_features["potion_temporary_dexterity_value"], 5)
        self.assertEqual(potion_features["potion_ritual_value"], 1)
        self.assertEqual(enemy_features["enemy_boss_count"], 1)
        self.assertEqual(enemy_features["enemy_elite_count"], 1)
        self.assertEqual(enemy_features["enemy_elite_or_boss_count"], 2)
        self.assertEqual(enemy_features["enemy_multi_hit_count"], 1)
        self.assertEqual(enemy_features["enemy_max_expected_attack"], 36)
        self.assertEqual(enemy_features["enemy_total_expected_attack"], 56)
        self.assertEqual(enemy_features["enemy_average_expected_attack"], 28.0)
        self.assertEqual(enemy_features["enemy_status_pressure_count"], 2)
        self.assertEqual(enemy_features["enemy_scaling_pressure_count"], 1)
        self.assertEqual(enemy_features["enemy_multi_enemy_pressure_count"], 1)
        self.assertEqual(enemy_features["enemy_artifact_count"], 1)
        self.assertEqual(enemy_features["enemy_frontload_check_count"], 1)
        self.assertEqual(enemy_features["enemy_aoe_high_value_count"], 1)
        self.assertEqual(enemy_features["enemy_act_1_count"], 2)
        self.assertEqual(enemy_features["enemy_tag_status_pressure"], 1)

    def test_extracts_multi_enemy_act_and_attack_pressure(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.monster_features(
            [
                {"id": "Byrd"},
                {"id": "Snecko"},
                {"id": "UnknownEnemy"},
            ]
        )

        self.assertEqual(features["enemy_known_count"], 2)
        self.assertEqual(features["enemy_unknown_count"], 1)
        self.assertEqual(features["enemy_act_2_count"], 2)
        self.assertEqual(features["enemy_total_expected_attack"], 48)
        self.assertEqual(features["enemy_max_expected_attack"], 27)
        self.assertEqual(features["enemy_average_expected_attack"], 24.0)
        self.assertEqual(features["enemy_multi_hit_count"], 1)
        self.assertEqual(features["enemy_multi_enemy_pressure_count"], 1)
        self.assertEqual(features["enemy_debuff_count"], 1)
        self.assertEqual(features["enemy_potion_tempo_check_count"], 1)
        self.assertEqual(features["enemy_tag_potion_tempo_check"], 1)

    def test_extracts_potion_status_and_healing_fact_values(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.potion_features(
            [
                {"id": "FearPotion"},
                {"id": "WeakPotion"},
                {"id": "StrengthPotion"},
                {"id": "DexterityPotion"},
                {"id": "AncientPotion"},
                {"id": "RegenPotion"},
                {"id": "BloodPotion"},
                {"id": "ElixirPotion"},
            ]
        )

        self.assertEqual(features["potion_known_count"], 8)
        self.assertEqual(features["potion_targeted_count"], 2)
        self.assertEqual(features["potion_noncombat_count"], 1)
        self.assertEqual(features["potion_vulnerable_value"], 3)
        self.assertEqual(features["potion_weak_value"], 3)
        self.assertEqual(features["potion_strength_value"], 2)
        self.assertEqual(features["potion_dexterity_value"], 2)
        self.assertEqual(features["potion_artifact_value"], 1)
        self.assertEqual(features["potion_healing_value"], 15)
        self.assertEqual(features["potion_heal_percent_max_hp"], 20)
        self.assertEqual(features["potion_exhaust_hand_count"], 1)

    def test_extracts_current_probe_potion_and_act2_enemy_gap_facts(self):
        knowledge = StaticKnowledge.load()

        potion_features = knowledge.potion_features(
            [
                {"id": "ColorlessPotion"},
                {"id": "FairyPotion"},
                {"id": "PowerPotion"},
                {"id": "EssenceOfSteel"},
                {"id": "BlessingOfTheForge"},
                {"id": "Fruit Juice"},
                {"id": "SteroidPotion"},
            ]
        )
        enemy_features = knowledge.monster_features(
            [
                {"id": "Chosen"},
                {"id": "BookOfStabbing"},
                {"id": "GremlinTsundere"},
                {"id": "Mugger"},
                {"id": "TheCollector"},
                {"id": "UnknownEnemy"},
            ]
        )

        self.assertEqual(potion_features["potion_known_count"], 7)
        self.assertEqual(potion_features["potion_unknown_count"], 0)
        self.assertEqual(potion_features["potion_generated_options_value"], 6)
        self.assertEqual(potion_features["potion_plated_armor_value"], 4)
        self.assertEqual(potion_features["potion_revive_percent_max_hp"], 30)
        self.assertEqual(potion_features["potion_max_hp_value"], 5)
        self.assertEqual(potion_features["potion_temporary_strength_value"], 5)
        self.assertEqual(potion_features["potion_upgrade_hand_count"], 1)
        self.assertEqual(potion_features["potion_noncombat_count"], 1)
        self.assertEqual(potion_features["potion_role_card_generation"], 2)
        self.assertEqual(potion_features["potion_role_route_recovery"], 2)
        self.assertEqual(enemy_features["enemy_known_count"], 5)
        self.assertEqual(enemy_features["enemy_unknown_count"], 1)
        self.assertEqual(enemy_features["enemy_act_2_count"], 4)
        self.assertEqual(enemy_features["enemy_act_1_count"], 1)
        self.assertEqual(enemy_features["enemy_boss_count"], 1)
        self.assertEqual(enemy_features["enemy_elite_count"], 1)
        self.assertEqual(enemy_features["enemy_elite_or_boss_count"], 2)
        self.assertEqual(enemy_features["enemy_multi_hit_count"], 2)
        self.assertEqual(enemy_features["enemy_max_expected_attack"], 35)
        self.assertEqual(enemy_features["enemy_total_expected_attack"], 119)
        self.assertEqual(enemy_features["enemy_potion_tempo_check_count"], 2)
        self.assertEqual(enemy_features["enemy_multi_enemy_pressure_count"], 2)
        self.assertEqual(enemy_features["enemy_debuff_count"], 2)

    def test_extracts_relic_features(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.relic_features(
            [
                {"id": "燃烧之血"},
                {"name": "Bag of Marbles"},
                {"id": "Preserved Insect"},
                {"id": "Unknown Relic"},
            ]
        )

        self.assertEqual(features["relic_known_count"], 3)
        self.assertEqual(features["relic_unknown_count"], 1)
        self.assertTrue(features["has_burning_blood"])
        self.assertTrue(features["has_preserved_insect"])
        self.assertEqual(features["relic_healing_value"], 6)
        self.assertEqual(features["relic_vulnerable_value"], 1)
        self.assertEqual(features["relic_elite_hp_damage_percent_value"], 25)
        self.assertEqual(features["relic_tag_frontload"], 2)
        self.assertEqual(features["relic_tag_route_aggression"], 1)

    def test_extracts_relic_energy_draw_and_tradeoff_values(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.relic_features(
            [
                {"id": "Lantern"},
                {"id": "Ink Bottle"},
                {"id": "Snecko Eye"},
                {"id": "Slaver's Collar"},
                {"id": "Coffee Dripper"},
                {"id": "Ectoplasm"},
                {"id": "Sozu"},
                {"id": "Chemical X"},
                {"id": "Paper Phrog"},
                {"id": "Champion Belt"},
                {"id": "Charon's Ashes"},
                {"id": "Brimstone"},
            ]
        )

        self.assertEqual(features["relic_known_count"], 12)
        self.assertEqual(features["relic_turn_one_energy_value"], 1)
        self.assertEqual(features["relic_energy_per_turn_value"], 3)
        self.assertEqual(features["relic_energy_in_elite_or_boss_value"], 1)
        self.assertEqual(features["relic_draw_value"], 2)
        self.assertEqual(features["relic_draw_every_cards_value"], 10)
        self.assertEqual(features["relic_vulnerable_damage_multiplier_bonus_value"], 25)
        self.assertEqual(features["relic_weak_when_vulnerable_value"], 1)
        self.assertEqual(features["relic_damage_all_on_exhaust_value"], 3)
        self.assertEqual(features["relic_enemy_strength_per_turn_value"], 1)
        self.assertEqual(features["relic_x_cost_bonus_value"], 2)
        self.assertTrue(features["has_no_rest_relic"])
        self.assertTrue(features["has_no_potions_relic"])
        self.assertTrue(features["has_no_gold_relic"])

    def test_extracts_cycle30_relic_gaps_and_aliases(self):
        knowledge = StaticKnowledge.load()

        features = knowledge.relic_features(
            [
                {"id": "Paper Frog"},
                {"id": "Black Blood"},
                {"id": "Letter Opener"},
            ]
        )

        self.assertEqual(features["relic_known_count"], 3)
        self.assertEqual(features["relic_unknown_count"], 0)
        self.assertEqual(features["relic_vulnerable_damage_multiplier_bonus_value"], 25)
        self.assertEqual(features["relic_healing_value"], 12)
        self.assertEqual(features["relic_aoe_damage_value"], 5)
        self.assertEqual(features["relic_skills_per_trigger_value"], 3)

    def test_extracts_cycle28_bite_double_tap_and_gremlin_horn_aliases(self):
        knowledge = StaticKnowledge.load()

        deck_features = knowledge.deck_features(
            [
                {"id": "Bite", "name": "噬咬"},
                {"id": "Double Tap", "name": "双发"},
            ]
        )
        relic_features = knowledge.relic_features(
            [
                {"id": "Gremlin Horn", "name": "地精之角"},
                {"id": "White Beast Statue", "name": "白兽雕像"},
                {"id": "HornCleat", "name": "船夹板"},
            ]
        )

        self.assertEqual(deck_features["deck_unknown_cards"], 0)
        self.assertEqual(deck_features["deck_known_cards"], 2)
        self.assertEqual(deck_features["deck_total_current_damage"], 7)
        self.assertEqual(relic_features["relic_unknown_count"], 0)
        self.assertEqual(relic_features["relic_known_count"], 3)

    def test_extracts_observed_probe_relic_gap_features_and_aliases(self):
        knowledge = StaticKnowledge.load()

        observed_ids = [
            "Potion Belt",
            "Bronze Scales",
            "Regal Pillow",
            "Gambling Chip",
            "Matryoshka",
            "Blue Candle",
            "Old Coin",
            "Fusion Hammer",
            "Meat on the Bone",
            "Whetstone",
            "CaptainsWheel",
            "Smiling Mask",
            "Omamori",
            "FossilizedHelix",
            "Nunchaku",
            "Anchor",
            "Vajra",
            "Sozu",
            "Charon's Ashes",
        ]
        relic_alias_items = []
        for relic_id in observed_ids:
            relic = knowledge.relic_for({"id": relic_id}) or {}
            aliases = relic.get("aliases") or []
            alias = next((item for item in aliases if any(ord(char) > 127 for char in item)), relic_id)
            relic_alias_items.append({"id": alias})

        features = knowledge.relic_features(relic_alias_items)

        self.assertEqual(features["relic_known_count"], 19)
        self.assertEqual(features["relic_unknown_count"], 0)
        self.assertEqual(features["relic_potion_slots_value"], 2)
        self.assertEqual(features["relic_thorns_value"], 3)
        self.assertEqual(features["relic_rest_heal_bonus_value"], 15)
        self.assertEqual(features["relic_opening_hand_filter_count"], 1)
        self.assertEqual(features["relic_bonus_chest_relics_value"], 1)
        self.assertEqual(features["relic_bonus_chests_value"], 2)
        self.assertEqual(features["relic_curse_playable_count"], 1)
        self.assertEqual(features["relic_gold_value"], 300)
        self.assertEqual(features["relic_energy_per_turn_value"], 2)
        self.assertEqual(features["relic_heal_below_half_value"], 12)
        self.assertEqual(features["relic_upgrade_attack_count"], 2)
        self.assertEqual(features["relic_turn_three_block_value"], 18)
        self.assertEqual(features["relic_card_remove_cost_value"], 50)
        self.assertEqual(features["relic_curse_negate_count"], 2)
        self.assertEqual(features["relic_damage_prevent_count"], 1)
        self.assertEqual(features["relic_energy_every_attacks_value"], 10)
        self.assertEqual(features["relic_strength_value"], 1)
        self.assertEqual(features["relic_combat_block_value"], 10)
        self.assertEqual(features["relic_damage_all_on_exhaust_value"], 3)
        self.assertTrue(features["has_no_smith_relic"])
        self.assertTrue(features["has_no_potions_relic"])

    def test_extracts_boss_mechanic_features(self):
        knowledge = StaticKnowledge.load()

        guardian = knowledge.boss_features([{"id": "TheGuardian"}])
        possible_act1 = knowledge.boss_features([], act=1, boss_available=True)

        self.assertTrue(guardian["boss_identity_known"])
        self.assertEqual(guardian["boss_known_count"], 1)
        self.assertEqual(guardian["boss_mechanic_mode_shift"], 1)
        self.assertEqual(guardian["boss_mechanic_sharp_hide"], 1)
        self.assertEqual(guardian["boss_search_hint_mode_shift_attack_cancel"], 1)
        self.assertEqual(guardian["boss_search_hint_value_non_attack_defense"], 1)
        self.assertEqual(guardian["boss_need_premium_block"], 1)
        self.assertEqual(guardian["boss_potion_need_duplication"], 1)
        self.assertEqual(guardian["boss_max_expected_attack"], 36)
        self.assertEqual(guardian["boss_total_expected_attack"], 36)
        self.assertEqual(guardian["boss_max_mode_shift_threshold"], 30)
        self.assertEqual(guardian["boss_max_sharp_hide_damage"], 3)
        self.assertFalse(possible_act1["boss_identity_known"])
        self.assertEqual(possible_act1["boss_possible_count"], 3)
        self.assertEqual(possible_act1["boss_mechanic_hp_scaled_opening"], 1)
        self.assertEqual(possible_act1["boss_mechanic_split_threshold"], 1)
        self.assertEqual(possible_act1["boss_mechanic_mode_shift"], 1)
        self.assertEqual(possible_act1["boss_search_hint_burn_cleanup_pressure"], 1)
        self.assertEqual(possible_act1["boss_search_hint_cross_split_line"], 1)
        self.assertEqual(possible_act1["boss_search_hint_mode_shift_attack_cancel"], 1)
        self.assertEqual(possible_act1["boss_need_premium_block"], 3)
        self.assertEqual(possible_act1["boss_need_aoe"], 1)
        self.assertEqual(possible_act1["boss_max_expected_attack"], 38)
        self.assertEqual(possible_act1["boss_total_expected_attack"], 110)
        self.assertEqual(possible_act1["boss_max_split_threshold_percent"], 50)
        self.assertEqual(possible_act1["boss_max_mode_shift_threshold"], 30)
        self.assertEqual(possible_act1["boss_max_sharp_hide_damage"], 3)
        self.assertEqual(possible_act1["boss_max_hit_count"], 6)
        self.assertEqual(possible_act1["boss_max_burn_damage"], 2)
        self.assertEqual(possible_act1["boss_max_upgraded_burn_damage"], 4)
        self.assertEqual(possible_act1["boss_max_post_split_enemy_count"], 2)


if __name__ == "__main__":
    unittest.main()
