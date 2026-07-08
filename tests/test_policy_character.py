import unittest

from slay_ai.policy_character import DEFAULT_CHARACTER_PROFILE, profile_for


class PolicyCharacterProfileTests(unittest.TestCase):
    def test_profile_for_returns_character_specific_starter_cards(self):
        ironclad = profile_for("IRONCLAD")
        silent = profile_for("SILENT")

        self.assertTrue(ironclad.is_starter_strike({"id": "Strike_R"}))
        self.assertFalse(ironclad.is_starter_strike({"id": "Strike_G"}))
        self.assertTrue(silent.is_starter_strike({"id": "Strike_G"}))
        self.assertFalse(silent.is_starter_strike({"id": "Strike_R"}))
        self.assertTrue(silent.is_starter_defend({"id": "Defend_G"}))
        self.assertFalse(silent.is_starter_defend({"id": "Defend_R"}))

    def test_unknown_character_uses_default_profile(self):
        self.assertIs(profile_for("UNKNOWN"), DEFAULT_CHARACTER_PROFILE)
        self.assertTrue(profile_for(None).is_starter_strike({"id": "Strike_R"}))


if __name__ == "__main__":
    unittest.main()
