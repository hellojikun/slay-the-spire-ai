import unittest

from slay_ai.policy_potion import PotionPolicy


def potion_policy(
    *,
    highest_attack_target=None,
    choose_target=None,
    is_long_fight=None,
    is_dangerous_early_scaling_fight=None,
    has_duplication_potion_target=None,
    liquid_memories_target=None,
    potion_tempo_model=None,
    model_authority="shadow",
    static_knowledge=None,
) -> PotionPolicy:
    return PotionPolicy(
        highest_attack_target=highest_attack_target or (lambda monsters: 1 if monsters else None),
        choose_target=choose_target or (lambda monsters, damage: (1 if monsters else None, monsters[0] if monsters else None)),
        is_long_fight=is_long_fight or (lambda monsters: False),
        is_dangerous_early_scaling_fight=is_dangerous_early_scaling_fight or (lambda game, monsters: False),
        has_duplication_potion_target=has_duplication_potion_target or (lambda game, monsters, *, incoming_sensitive: False),
        liquid_memories_target=liquid_memories_target or (lambda game, monsters, incoming, current_block, current_hp: None),
        potion_tempo_model=potion_tempo_model,
        model_authority=model_authority,
        static_knowledge=static_knowledge,
    )


class FakePotionTempoModel:
    def __init__(self, score=0.9, examples=100):
        self.score = score
        self.feature_weights = {"incoming": 1.0}
        self.metadata = {"examples": examples, "training_source_quality": "pristine"}

    def score_row(self, row):
        self.last_row = row
        return self.score


class FailingPotionTempoModel(FakePotionTempoModel):
    def score_row(self, row):
        raise RuntimeError("model failed")


class PotionPolicyTests(unittest.TestCase):
    def test_emergency_weak_potion_targets_highest_attacker(self):
        policy = potion_policy(highest_attack_target=lambda monsters: 2)
        game = {
            "current_hp": 20,
            "max_hp": 80,
            "potions": [{"id": "WeakPotion", "can_use": True}],
            "combat_state": {"player": {"current_hp": 20}},
        }
        monsters = [
            {"name": "Louse", "move": {"damage": 6}},
            {"name": "Jaw Worm", "move": {"damage": 18}},
        ]

        decision = policy.emergency_potion(game, monsters, incoming=24, current_block=0, hp_ratio=0.25)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1, "target_index": 2}])

    def test_emergency_fire_potion_uses_target_picker(self):
        policy = potion_policy(choose_target=lambda monsters, damage: (2, monsters[1]))
        game = {
            "current_hp": 9,
            "max_hp": 80,
            "potions": [{"id": "FirePotion", "can_use": True}],
            "combat_state": {"player": {"current_hp": 9}},
        }
        monsters = [
            {"name": "Louse", "current_hp": 12},
            {"name": "Jaw Worm", "current_hp": 30},
        ]

        decision = policy.emergency_potion(game, monsters, incoming=18, current_block=0, hp_ratio=0.11)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1, "target_index": 2}])

    def test_strategic_long_fight_uses_scaling_potion_early(self):
        policy = potion_policy(is_long_fight=lambda monsters: True)
        game = {
            "potions": [{"id": "CultistPotion", "can_use": True}],
            "combat_state": {"turn": 1},
        }

        decision = policy.strategic_combat_potion(game, [{"name": "The Guardian"}])

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertIn("Long boss/elite fight", decision.reason)

    def test_strategic_dangerous_short_fight_does_not_use_forge(self):
        policy = potion_policy(is_dangerous_early_scaling_fight=lambda game, monsters: True)
        game = {
            "potions": [{"id": "BlessingOfTheForge", "can_use": True}],
            "combat_state": {"turn": 1},
        }

        decision = policy.strategic_combat_potion(game, [{"name": "Jaw Worm"}])

        self.assertIsNone(decision)

    def test_emergency_liquid_memories_precedes_skill_potion(self):
        policy = potion_policy(
            liquid_memories_target=lambda game, monsters, incoming, current_block, current_hp: {"name": "Flame Barrier"}
        )
        game = {
            "current_hp": 12,
            "max_hp": 80,
            "potions": [
                {"id": "LiquidMemories", "name": "Liquid Memories", "can_use": True},
                {"id": "SkillPotion", "name": "Skill Potion", "can_use": True},
            ],
            "combat_state": {"player": {"current_hp": 12}},
        }

        decision = policy.emergency_potion(game, [{"name": "Gremlin Nob"}], incoming=30, current_block=0, hp_ratio=0.15)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertIn("recover Flame Barrier", decision.reason)

    def test_preserves_emergency_potions_when_hand_can_mode_shift_guardian(self):
        policy = potion_policy()
        game = {
            "current_hp": 73,
            "max_hp": 85,
            "potions": [
                {"id": "SkillPotion", "name": "Skill Potion", "can_use": True},
                {"id": "FirePotion", "name": "Fire Potion", "can_use": True, "requires_target": True},
            ],
            "combat_state": {
                "player": {"current_hp": 73, "max_hp": 85, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                    {"id": "Carnage", "type": "ATTACK", "cost": 2, "damage": 20, "is_playable": True, "has_target": True},
                    {
                        "id": "Pommel Strike",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 9,
                        "is_playable": True,
                        "has_target": True,
                    },
                ],
                "monsters": [
                    {
                        "id": "TheGuardian",
                        "current_hp": 232,
                        "max_hp": 240,
                        "block": 9,
                        "move": {"damage": 32},
                        "powers": [{"id": "Mode Shift", "amount": 22}, {"id": "Vulnerable", "amount": 1}],
                    }
                ],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=32,
            current_block=0,
            hp_ratio=73 / 85,
        )

        self.assertIsNone(decision)

    def test_uses_fire_potion_when_guardian_mode_shift_is_not_reachable_by_hand(self):
        policy = potion_policy()
        game = {
            "current_hp": 16,
            "max_hp": 85,
            "potions": [{"id": "FirePotion", "name": "Fire Potion", "can_use": True, "requires_target": True}],
            "combat_state": {
                "player": {"current_hp": 16, "max_hp": 85, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Pommel Strike", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True, "has_target": True},
                    {"id": "Bash", "type": "ATTACK", "cost": 2, "damage": 8, "is_playable": True, "has_target": True},
                    {"id": "Pommel Strike", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True, "has_target": True},
                    {"id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True, "has_target": True},
                ],
                "monsters": [
                    {
                        "id": "TheGuardian",
                        "current_hp": 70,
                        "max_hp": 240,
                        "block": 9,
                        "move": {"damage": 32},
                        "powers": [{"id": "Mode Shift", "amount": 22}],
                    }
                ],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=32,
            current_block=0,
            hp_ratio=16 / 85,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1, "target_index": 1}])
        self.assertIn("Emergency tempo", decision.reason)

    def test_preserves_tempo_potion_when_boss_search_can_reduce_danger(self):
        policy = potion_policy()
        game = {
            "current_hp": 73,
            "max_hp": 80,
            "act": 1,
            "floor": 16,
            "potions": [{"id": "SpeedPotion", "name": "Speed Potion", "can_use": True}],
            "combat_state": {
                "player": {"current_hp": 73, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {
                        "id": "Perfected Strike",
                        "type": "ATTACK",
                        "cost": 2,
                        "damage": 16,
                        "is_playable": True,
                        "has_target": True,
                    },
                    {"id": "Slimed", "type": "STATUS", "cost": 1, "is_playable": True},
                    {"id": "Whirlwind", "type": "ATTACK", "cost": -1, "damage": 19, "is_playable": True},
                ],
                "monsters": [
                    {"id": "SpikeSlime_L", "hp": 65, "max_hp": 70, "intent": "ATTACK_DEBUFF", "move": {"damage": 10}},
                    {"id": "AcidSlime_M", "hp": 15, "max_hp": 30, "intent": "DEBUFF"},
                    {"id": "AcidSlime_M", "hp": 31, "max_hp": 30, "intent": "ATTACK", "move": {"damage": 16}},
                ],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=26,
            current_block=0,
            hp_ratio=73 / 80,
        )

        self.assertIsNone(decision)

    def test_assist_potion_model_can_preempt_search_on_high_confidence_boss_pressure(self):
        model = FakePotionTempoModel(score=0.9, examples=120)
        policy = potion_policy(potion_tempo_model=model, model_authority="assist")
        game = {
            "current_hp": 20,
            "max_hp": 80,
            "act": 1,
            "floor": 16,
            "potions": [{"id": "SpeedPotion", "name": "Speed Potion", "can_use": True}],
            "combat_state": {
                "turn": 2,
                "player": {"current_hp": 20, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                ],
                "monsters": [{"id": "TheGuardian", "current_hp": 220, "move": {"damage": 26}}],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=26,
            current_block=0,
            hp_ratio=20 / 80,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.actions, [{"action": "use_potion", "potion_slot": 1}])
        self.assertIn("Potion tempo model assist", decision.reason)
        assist = decision.metadata["potion_model_assist"]
        self.assertTrue(assist["runtime_authority"])
        self.assertFalse(assist["direct_mcp_control"])
        self.assertEqual(assist["decision_influence"], "potion_use_action")
        self.assertEqual(assist["level"], "assist")
        self.assertEqual(assist["examples"], 120)
        self.assertEqual(model.last_row["incoming"], 26)

    def test_assist_potion_model_does_not_preempt_safe_search_solution(self):
        model = FakePotionTempoModel(score=0.9, examples=120)
        policy = potion_policy(potion_tempo_model=model, model_authority="assist")
        game = {
            "current_hp": 73,
            "max_hp": 80,
            "act": 1,
            "floor": 16,
            "potions": [{"id": "SpeedPotion", "name": "Speed Potion", "can_use": True}],
            "combat_state": {
                "turn": 2,
                "player": {"current_hp": 73, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                ],
                "monsters": [{"id": "TheGuardian", "current_hp": 220, "move": {"damage": 26}}],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=26,
            current_block=0,
            hp_ratio=73 / 80,
        )

        self.assertIsNone(decision)

    def test_shadow_potion_model_records_no_runtime_override(self):
        model = FakePotionTempoModel(score=0.9, examples=120)
        policy = potion_policy(potion_tempo_model=model, model_authority="shadow")
        game = {
            "current_hp": 73,
            "max_hp": 80,
            "act": 1,
            "floor": 16,
            "potions": [{"id": "SpeedPotion", "name": "Speed Potion", "can_use": True}],
            "combat_state": {
                "turn": 2,
                "player": {"current_hp": 73, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                ],
                "monsters": [{"id": "TheGuardian", "current_hp": 220, "move": {"damage": 26}}],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=26,
            current_block=0,
            hp_ratio=73 / 80,
        )

        self.assertIsNone(decision)

    def test_assist_potion_model_failure_falls_back_to_heuristic(self):
        model = FailingPotionTempoModel(score=0.9, examples=120)
        policy = potion_policy(potion_tempo_model=model, model_authority="assist")
        game = {
            "current_hp": 20,
            "max_hp": 80,
            "act": 1,
            "floor": 16,
            "potions": [{"id": "SpeedPotion", "name": "Speed Potion", "can_use": True}],
            "combat_state": {
                "turn": 2,
                "player": {"current_hp": 20, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                ],
                "monsters": [{"id": "TheGuardian", "current_hp": 220, "move": {"damage": 26}}],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=26,
            current_block=0,
            hp_ratio=20 / 80,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.reason, "Emergency tempo; use Speed Potion.")

    def test_assist_potion_model_ignores_small_sample_model(self):
        model = FakePotionTempoModel(score=0.9, examples=2)
        policy = potion_policy(potion_tempo_model=model, model_authority="assist")
        game = {
            "current_hp": 73,
            "max_hp": 80,
            "act": 1,
            "floor": 16,
            "potions": [{"id": "SpeedPotion", "name": "Speed Potion", "can_use": True}],
            "combat_state": {
                "turn": 2,
                "player": {"current_hp": 73, "max_hp": 80, "current_energy": 3, "block": 0},
                "hand": [
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                    {"id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True},
                ],
                "monsters": [{"id": "TheGuardian", "current_hp": 220, "move": {"damage": 26}}],
            },
        }

        decision = policy.emergency_potion(
            game,
            game["combat_state"]["monsters"],
            incoming=26,
            current_block=0,
            hp_ratio=73 / 80,
        )

        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
