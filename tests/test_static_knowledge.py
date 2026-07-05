import unittest

from slay_ai.static_knowledge import StaticKnowledge


class StaticKnowledgeTests(unittest.TestCase):
    def test_loads_seed_tables_and_extracts_deck_features(self):
        knowledge = StaticKnowledge.load()

        self.assertGreaterEqual(knowledge.source_counts["cards"], 20)
        self.assertGreaterEqual(knowledge.source_counts["monsters"], 10)
        self.assertGreaterEqual(knowledge.source_counts["potions"], 10)

        features = knowledge.deck_features(["Strike_R", "Defend_R", "Bash", "Clothesline", "Shrug It Off"])

        self.assertEqual(features["deck_known_cards"], 5)
        self.assertEqual(features["deck_unknown_cards"], 0)
        self.assertEqual(features["deck_tag_weak"], 1)
        self.assertEqual(features["deck_tag_vulnerable"], 1)
        self.assertEqual(features["deck_tag_block"], 2)
        self.assertGreaterEqual(features["deck_total_base_damage"], 26)
        self.assertGreaterEqual(features["deck_total_base_block"], 13)

    def test_extracts_potion_and_enemy_features(self):
        knowledge = StaticKnowledge.load()

        potion_features = knowledge.potion_features(
            [
                {"id": "LiquidMemories"},
                {"name": "Block Potion"},
                {"id": "SkillPotion"},
                {"id": "CultistPotion"},
                {"id": "SpeedPotion"},
                {"id": "DistilledChaos"},
            ]
        )
        enemy_features = knowledge.monster_features([{"id": "Hexaghost"}, {"name": "Sentry"}])

        self.assertTrue(potion_features["has_liquid_memories"])
        self.assertEqual(potion_features["potion_role_emergency"], 4)
        self.assertEqual(potion_features["potion_role_scaling"], 1)
        self.assertEqual(potion_features["potion_role_burst_turn"], 2)
        self.assertEqual(potion_features["potion_known_count"], 6)
        self.assertEqual(potion_features["potion_block_value"], 12)
        self.assertEqual(enemy_features["enemy_boss_count"], 1)
        self.assertEqual(enemy_features["enemy_elite_count"], 1)
        self.assertEqual(enemy_features["enemy_multi_hit_count"], 1)
        self.assertEqual(enemy_features["enemy_tag_status_pressure"], 1)


if __name__ == "__main__":
    unittest.main()
