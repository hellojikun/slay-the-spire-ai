import unittest

from slay_ai.mcp_watchdog import _recover_terminal_screen


class FakeWatchdogClient:
    def __init__(self, commands: dict) -> None:
        self.commands = commands
        self.executed: list[list[dict]] = []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        self.assert_name = name
        return self.commands

    def execute_actions(self, actions: list[dict]) -> None:
        self.executed.append(actions)


class WatchdogTests(unittest.TestCase):
    def test_recovers_game_over_when_proceed_is_available(self):
        client = FakeWatchdogClient(
            {"screen_type": "GAME_OVER", "available_tools": [{"tool": "proceed"}]}
        )
        self.assertTrue(_recover_terminal_screen(client))
        self.assertEqual(client.executed, [[{"action": "proceed"}]])

    def test_does_not_mutate_non_terminal_screen(self):
        client = FakeWatchdogClient(
            {"screen_type": "MAP", "available_tools": [{"tool": "proceed"}]}
        )
        self.assertFalse(_recover_terminal_screen(client))
        self.assertEqual(client.executed, [])


if __name__ == "__main__":
    unittest.main()
