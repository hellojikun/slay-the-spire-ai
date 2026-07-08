import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from slay_ai import runner
from slay_ai.memory import StrategyMemory
from slay_ai.mcp.client import MCPError


def map_state() -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "MAP",
            "floor": 1,
            "current_hp": 80,
            "max_hp": 80,
            "screen_state": {"next_nodes": [{"symbol": "M", "x": 1, "y": 1}]},
        },
    }


def target_a0_combat_state() -> dict:
    state = combat_state(1)
    game = state["game_state"]
    game["class"] = "IRONCLAD"
    game["ascension_level"] = 0
    game["current_hp"] = 80
    game["max_hp"] = 80
    game["floor"] = 1
    return state


def game_over_state() -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "GAME_OVER",
            "floor": 1,
            "class": "IRONCLAD",
            "ascension_level": 0,
            "screen_state": {"victory": False, "score": 42},
        },
    }


def main_menu_state() -> dict:
    return {"ready_for_command": True, "in_game": False}


def dead_reward_state() -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "COMBAT_REWARD",
            "room_phase": "COMPLETE",
            "floor": 10,
            "act": 1,
            "class": "IRONCLAD",
            "ascension_level": 4,
            "current_hp": 0,
            "max_hp": 80,
            "gold": 123,
            "deck": [],
            "relics": [],
            "potions": [],
            "screen_state": {"rewards": []},
        },
    }


def combat_state(turn: int, hand: list[dict] | None = None, energy: int = 0) -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "floor": 1,
            "combat_state": {
                "turn": turn,
                "player": {"current_energy": energy},
                "hand": hand or [],
                "monsters": [{"name": "Louse", "current_hp": 12, "move": {"damage": 6}}],
            },
        },
    }


def rest_state(room_phase: str = "INCOMPLETE", options: list[str] | None = None) -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "REST",
            "room_phase": room_phase,
            "floor": 7,
            "current_hp": 36,
            "max_hp": 80,
            "screen_state": {"rest_options": ["rest", "smith"] if options is None else options},
        },
    }


def grid_state(confirm_up: bool = False, selected_cards: list[dict] | None = None, for_upgrade: bool = True) -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "GRID",
            "room_phase": "INCOMPLETE",
            "floor": 15,
            "current_hp": 72,
            "max_hp": 80,
            "screen_state": {
                "cards": [{"name": "Whirlwind", "id": "Whirlwind", "uuid": "w"}],
                "selected_cards": [] if selected_cards is None else selected_cards,
                "num_cards": 1,
                "confirm_up": confirm_up,
                "for_upgrade": for_upgrade,
            },
        },
    }


def hand_select_state() -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "HAND_SELECT",
            "room_phase": "COMBAT",
            "floor": 2,
            "screen_state": {
                "hand": [
                    {"name": "Strike", "id": "Strike_R"},
                    {"name": "Defend", "id": "Defend_R"},
                ],
                "max_cards": 1,
            },
        },
    }


def shop_screen_state() -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "SHOP_SCREEN",
            "room_phase": "COMPLETE",
            "floor": 24,
            "act": 2,
            "class": "IRONCLAD",
            "ascension_level": 0,
            "current_hp": 51,
            "max_hp": 80,
            "gold": 200,
            "deck": [{"name": "Strike", "id": "Strike_R"}, {"name": "Defend", "id": "Defend_R"}],
            "potions": [{"is_empty": True}],
            "screen_state": {
                "purge_available": False,
                "purge_cost": 100,
                "cards": [],
                "relics": [],
                "potions": [{"id": "DexterityPotion", "name": "Dexterity Potion", "price": 51}],
            },
        },
    }


def lethal_combat_state() -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "floor": 14,
            "act": 1,
            "class": "IRONCLAD",
            "ascension_level": 4,
            "current_hp": 2,
            "max_hp": 80,
            "gold": 209,
            "deck": [],
            "relics": [],
            "potions": [],
            "combat_state": {
                "turn": 3,
                "player": {"current_hp": 2, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [{"name": "Defend", "type": "SKILL", "cost": 1, "block": 5, "is_playable": False}],
                "monsters": [
                    {"name": "Fungi Beast", "current_hp": 14, "move": {"damage": 11}},
                    {"name": "Fungi Beast", "current_hp": 18, "move": {"damage": 15}},
                ],
            },
        },
    }


def lethal_target_attack_state(monsters: list[dict] | None = None) -> dict:
    return {
        "in_game": True,
        "game_state": {
            "screen_type": "NONE",
            "room_phase": "COMBAT",
            "floor": 16,
            "act": 1,
            "current_hp": 7,
            "max_hp": 80,
            "combat_state": {
                "turn": 19,
                "player": {"current_hp": 7, "max_hp": 80, "current_energy": 1, "block": 0},
                "hand": [
                    {
                        "name": "Strike",
                        "id": "Strike_R",
                        "type": "ATTACK",
                        "cost": 1,
                        "damage": 6,
                        "is_playable": True,
                        "has_target": True,
                    }
                ],
                "monsters": monsters
                if monsters is not None
                else [
                    {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 16, "max_hp": 21},
                    {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 2, "max_hp": 21},
                ],
            },
        },
    }


class FakeClient:
    def __init__(
        self,
        states: list[dict],
        action_error: MCPError | None = None,
        available_tools: list[str] | None = None,
    ) -> None:
        self.states = states
        self.action_error = action_error
        self.available_tools = available_tools or [
            "cancel",
            "choose",
            "confirm",
            "end_turn",
            "play_card",
            "proceed",
            "select_cards",
            "use_potion",
            "wait",
        ]
        self.executed: list[list[dict]] = []

    def initialize(self) -> dict:
        return {}

    def get_game_state(self, include: list[str] | None = None) -> dict:
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def execute_actions(self, actions: list[dict]) -> None:
        self.executed.append(actions)
        if self.action_error:
            error = self.action_error
            self.action_error = None
            raise error

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        return {"available_tools": [{"tool": tool} for tool in self.available_tools]}


class SequencedCommandsClient(FakeClient):
    def __init__(self, states: list[dict], command_tools: list[list[str]]) -> None:
        super().__init__(states, available_tools=command_tools[-1] if command_tools else None)
        self.command_tools = list(command_tools)

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        if name == "get_available_commands" and self.command_tools:
            tools = self.command_tools.pop(0)
            self.available_tools = tools
            return {"available_tools": [{"tool": tool} for tool in tools]}
        return super().call_tool(name, arguments)


class StartBlockedByGameOverClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([map_state()])
        self.start_calls = 0
        self.tool_calls: list[str] = []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "start_game":
            self.start_calls += 1
            if self.start_calls == 1:
                raise MCPError("Error: Invalid command: start. Possible commands: [proceed]")
            return "Started"
        if name == "get_available_commands":
            return {"screen_type": "GAME_OVER", "available_tools": [{"tool": "proceed"}]}
        return super().call_tool(name, arguments)


class StartBlockedByPassiveProceedClient(StartBlockedByGameOverClient):
    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "start_game":
            self.start_calls += 1
            if self.start_calls == 1:
                raise MCPError("Error: Invalid command: start. Possible commands: [proceed, key, click, wait, save, state]")
            return "Started"
        if name == "get_available_commands":
            return {
                "available_tools": [
                    {"tool": "proceed"},
                    {"tool": "key"},
                    {"tool": "click"},
                    {"tool": "wait"},
                    {"tool": "save"},
                    {"tool": "state"},
                ]
            }
        return FakeClient.call_tool(self, name, arguments)


class StartBlockedThenTargetRunClient(StartBlockedByPassiveProceedClient):
    def __init__(self) -> None:
        FakeClient.__init__(self, [target_a0_combat_state()])
        self.start_calls = 0
        self.tool_calls: list[str] = []


class ExistingSaveMenuClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([main_menu_state()])
        self.started = False
        self.abandoned = False
        self.continued = False
        self.start_calls = 0
        self.tool_calls: list[str] = []

    def get_game_state(self, include: list[str] | None = None) -> dict:
        if self.started or self.continued:
            return target_a0_combat_state()
        return main_menu_state()

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "get_available_commands":
            if self.abandoned:
                return {"screen_type": "MAIN_MENU", "in_game": False, "available_tools": [{"tool": "start_game"}]}
            return {
                "screen_type": "MAIN_MENU",
                "in_game": False,
                "available_tools": [{"tool": "continue_game"}, {"tool": "abandon_run"}, {"tool": "state"}],
            }
        if name == "start_game":
            self.start_calls += 1
            if not self.abandoned:
                raise MCPError("start_game should not be called before existing save is handled")
            self.started = True
            return "Started"
        if name == "abandon_run":
            self.abandoned = True
            return "Abandoned"
        if name == "continue_game":
            self.continued = True
            return "Continued"
        return FakeClient.call_tool(self, name, arguments)


class LyingStartAvailableExistingSaveClient(ExistingSaveMenuClient):
    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "get_available_commands":
            return {"screen_type": "MAIN_MENU", "in_game": False, "available_tools": [{"tool": "start_game"}]}
        if name == "start_game":
            self.start_calls += 1
            if not self.abandoned:
                raise MCPError("Error: Invalid command: start. Possible commands: [continue, abandon, state]")
            self.started = True
            return "Started"
        if name == "abandon_run":
            self.abandoned = True
            return "Abandoned"
        return FakeClient.call_tool(self, name, arguments)


class InDungeonContinueClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([target_a0_combat_state()])
        self.tool_calls: list[str] = []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "continue_game":
            raise MCPError("continue_game should not be called while already in game")
        return super().call_tool(name, arguments)


class InDungeonAbandonClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([target_a0_combat_state()])
        self.saved = False
        self.abandoned = False
        self.started = False
        self.start_calls = 0
        self.tool_calls: list[str] = []

    def get_game_state(self, include: list[str] | None = None) -> dict:
        if self.started:
            return target_a0_combat_state()
        if self.saved or self.abandoned:
            return main_menu_state()
        return target_a0_combat_state()

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "get_available_commands":
            if self.saved or self.abandoned:
                return {
                    "screen_type": "MAIN_MENU",
                    "in_game": False,
                    "available_tools": [{"tool": "abandon_run"}, {"tool": "state"}],
                }
            return {
                "screen_type": "NONE",
                "in_game": True,
                "available_tools": [
                    {"tool": "play_card"},
                    {"tool": "end_turn"},
                    {"tool": "use_potion"},
                    {"tool": "key"},
                    {"tool": "click"},
                    {"tool": "wait"},
                    {"tool": "save"},
                    {"tool": "state"},
                ],
            }
        if name == "save_game":
            self.saved = True
            return "Saved"
        if name == "abandon_run":
            self.abandoned = True
            return "Abandoned"
        if name == "start_game":
            self.start_calls += 1
            if not self.abandoned:
                raise MCPError("start_game should not be called before in-dungeon save is abandoned")
            self.started = True
            return "Started"
        return FakeClient.call_tool(self, name, arguments)


class MapObservationErrorClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([map_state()])
        self.includes: list[list[str] | None] = []

    def get_game_state(self, include: list[str] | None = None) -> dict:
        self.includes.append(include)
        if include == ["player", "screen", "map"]:
            raise MCPError("Internal error: null")
        return self.states[0]


class MapObservationSuccessClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([map_state()])

    def get_game_state(self, include: list[str] | None = None) -> dict:
        if include == ["player", "screen", "map"]:
            return {
                "in_game": True,
                "game_state": {
                    "screen_type": "MAP",
                    "map": [[{"symbol": "M", "x": 1, "y": 1}]],
                },
            }
        return self.states[0]


class NullAfterActionClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([lethal_combat_state()])
        self.after_action = False

    def get_game_state(self, include: list[str] | None = None) -> dict:
        if self.after_action:
            raise MCPError("Internal error: null")
        return self.states[0]

    def execute_actions(self, actions: list[dict]) -> None:
        super().execute_actions(actions)
        self.after_action = True

    def probe_health(self) -> dict:
        return {"status": "state_broken", "state_ok": False, "protocol_ok": True, "probes": []}


def nonlethal_looking_boss_state() -> dict:
    state = lethal_combat_state()
    game = state["game_state"]
    game["floor"] = 16
    game["current_hp"] = 1
    game["max_hp"] = 93
    player = game["combat_state"]["player"]
    player["current_hp"] = 1
    player["max_hp"] = 93
    player["current_energy"] = 0
    player["block"] = 13
    game["combat_state"]["turn"] = 11
    game["combat_state"]["hand"] = []
    game["combat_state"]["monsters"] = [
        {"name": "Guardian", "current_hp": 12, "move": {"damage": 12}},
    ]
    return state


class NullWithGameOverCommandsClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([nonlethal_looking_boss_state()])
        self.after_action = False

    def get_game_state(self, include: list[str] | None = None) -> dict:
        if self.after_action:
            raise MCPError("Internal error: null")
        return self.states[0]

    def execute_actions(self, actions: list[dict]) -> None:
        super().execute_actions(actions)
        self.after_action = True

    def probe_health(self) -> dict:
        return {
            "status": "state_broken",
            "state_ok": False,
            "protocol_ok": True,
            "probes": [
                {
                    "name": "get_available_commands",
                    "ok": True,
                    "summary": {"screen_type": "GAME_OVER", "available_tools": ["proceed"]},
                }
            ],
        }


class TerminalRecoveryAfterNullClient(NullWithGameOverCommandsClient):
    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        if name == "get_available_commands" and self.after_action:
            return {"screen_type": "GAME_OVER", "available_tools": [{"tool": "proceed"}]}
        return super().call_tool(name, arguments)


class TerminalCommandsClearClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([main_menu_state()])
        self.proceeded = False
        self.command_reads = 0

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        if name != "get_available_commands":
            return super().call_tool(name, arguments)
        self.command_reads += 1
        if self.proceeded:
            return {"screen_type": "MAIN_MENU", "in_game": False, "available_tools": [{"tool": "start_game"}]}
        return {"screen_type": "GAME_OVER", "in_game": True, "available_tools": [{"tool": "proceed"}]}

    def execute_actions(self, actions: list[dict]) -> None:
        super().execute_actions(actions)
        if actions == [{"action": "proceed"}]:
            self.proceeded = True


class RunnerTests(unittest.TestCase):
    def test_write_state_record_includes_decision_metadata(self):
        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "run.jsonl"
            record = runner._write_state_record(
                log_path,
                1,
                combat_state(1),
                [{"action": "play_card", "card_index": 1}],
                "One-turn search: test.",
                metadata={"search": {"initial_loss": 16, "projected_loss": 6, "avoided_lethal": True}},
            )

            lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(record["decision"]["metadata"]["search"]["initial_loss"], 16)
        self.assertEqual(lines[0]["decision"]["metadata"]["search"]["projected_loss"], 6)
        self.assertTrue(lines[0]["decision"]["metadata"]["search"]["avoided_lethal"])

    def test_start_recovers_terminal_game_over_before_new_run(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = StartBlockedByGameOverClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(result.status, "max_steps")
        self.assertEqual(client.start_calls, 2)
        self.assertEqual(client.executed, [[{"action": "proceed"}]])
        self.assertIn("get_available_commands", client.tool_calls)

    def test_start_recovers_passive_proceed_screen_before_new_run(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = StartBlockedByPassiveProceedClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(result.status, "max_steps")
        self.assertEqual(client.start_calls, 2)
        self.assertEqual(client.executed, [[{"action": "proceed"}]])
        self.assertIn("get_available_commands", client.tool_calls)

    def test_start_does_not_restart_after_recovery_enters_target_run(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = StartBlockedThenTargetRunClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                    ascension=0,
                )

        self.assertEqual(result.status, "max_steps")
        self.assertEqual(client.start_calls, 1)
        self.assertEqual(client.executed, [[{"action": "proceed"}]])
        self.assertIn("get_available_commands", client.tool_calls)

    def test_start_existing_save_fail_does_not_call_start_game(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = ExistingSaveMenuClient()

            with self.assertRaisesRegex(MCPError, "Existing save blocks new run"):
                runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    existing_save="fail",
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(client.start_calls, 0)
        self.assertFalse(client.abandoned)
        self.assertIn("get_available_commands", client.tool_calls)

    def test_start_existing_save_abandon_handles_save_before_start(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = ExistingSaveMenuClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    existing_save="abandon",
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(result.status, "max_steps")
        self.assertTrue(client.abandoned)
        self.assertEqual(client.start_calls, 1)
        self.assertLess(client.tool_calls.index("abandon_run"), client.tool_calls.index("start_game"))

    def test_start_existing_save_abandon_uses_possible_commands_when_probe_lists_start(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = LyingStartAvailableExistingSaveClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    existing_save="abandon",
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(result.status, "max_steps")
        self.assertTrue(client.abandoned)
        self.assertEqual(client.start_calls, 2)
        start_indexes = [index for index, name in enumerate(client.tool_calls) if name == "start_game"]
        self.assertLess(client.tool_calls.index("abandon_run"), start_indexes[-1])

    def test_continue_uses_current_in_dungeon_run(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = InDungeonContinueClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    continue_run=True,
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(result.status, "max_steps")
        self.assertNotIn("continue_game", client.tool_calls)

    def test_start_existing_save_abandon_handles_in_dungeon_command_surface(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = InDungeonAbandonClient()
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    start=True,
                    existing_save="abandon",
                    max_steps=0,
                    interval=0.01,
                    log_dir=Path(tmp),
                    ascension=0,
                )

        self.assertEqual(result.status, "max_steps")
        self.assertTrue(client.saved)
        self.assertTrue(client.abandoned)
        self.assertEqual(client.start_calls, 1)
        self.assertLess(client.tool_calls.index("save_game"), client.tool_calls.index("abandon_run"))
        self.assertLess(client.tool_calls.index("abandon_run"), client.tool_calls.index("start_game"))

    def test_action_settle_delay_uses_slowest_action_type(self):
        self.assertGreaterEqual(
            runner._settle_delay_for_actions([{"action": "choose", "choice_index": 1}], 0.05),
            0.65,
        )
        self.assertGreaterEqual(
            runner._settle_delay_for_actions([{"action": "play_card", "card_index": 1}], 0.05),
            0.35,
        )
        self.assertGreaterEqual(
            runner._settle_delay_for_actions([{"action": "end_turn"}], 0.05),
            0.9,
        )
        self.assertGreaterEqual(
            runner._settle_delay_for_actions([{"action": "select_cards", "drop": [1]}], 0.05),
            0.6,
        )

    def test_slow_play_card_confirms_state_change_after_long_latency(self):
        before = combat_state(
            turn=2,
            hand=[{"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5, "is_playable": True}],
            energy=1,
        )
        after = combat_state(turn=2, hand=[], energy=0)
        after["game_state"]["combat_state"]["player"] = {"current_energy": 0, "block": 5}
        client = FakeClient([after], available_tools=["play_card", "end_turn"])

        with patch.object(runner.time, "sleep", return_value=None), patch.object(runner, "_elapsed_ms", return_value=3000):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(client.executed, [[{"action": "play_card", "card_index": 1}]])
        self.assertEqual(result.post_action_settle_reason, "slow_action_state_changed")
        self.assertIsNone(result.post_action_settle_error)
        self.assertGreaterEqual(result.settle_ms, 500)

    def test_recoverable_action_error_settles_reads_and_continues(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = FakeClient(
                [map_state(), game_over_state(), game_over_state()],
                MCPError("Error at action 0: Invalid command; Possible commands: proceed"),
            )
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

            self.assertEqual(result.status, "game_over")
            self.assertEqual(client.executed, [[{"action": "choose", "choice_index": 1}]])
            records = [
                json.loads(line)
                for line in result.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            action_results = [record for record in records if record.get("event") == "action_result"]
            self.assertEqual(len(action_results), 1)
            self.assertEqual(action_results[0]["action_status"], "recoverable_error")
            self.assertTrue(action_results[0]["recovered"])
            self.assertIn("Invalid command", action_results[0]["last_error"])
            self.assertGreaterEqual(action_results[0]["settle_ms"], 900)

    def test_repeated_shop_purchase_guard_leaves_stale_shop_loop(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = FakeClient([shop_screen_state()])
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=4,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

            records = [
                json.loads(line)
                for line in result.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not json.loads(line).get("event")
            ]

        self.assertEqual(result.status, "max_steps")
        self.assertEqual(
            client.executed,
            [
                [{"action": "choose", "choice_index": 1}],
                [{"action": "choose", "choice_index": 1}],
                [{"action": "choose", "choice_index": 1}],
                [{"action": "cancel"}],
            ],
        )
        self.assertIn("Repeated shop purchase guard", records[-1]["decision"]["reason"])

    def test_run_episode_writes_per_run_manifest(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = FakeClient([map_state(), game_over_state(), game_over_state()])
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=Path(tmp),
                )
            self.assertEqual(result.status, "game_over")
            self.assertEqual(result.manifest_category, "clean_trainable")
            self.assertEqual(result.manifest_reason, "completed_clean")
            self.assertIsNotNone(result.manifest_path)
            assert result.manifest_path is not None
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["episode"]["status"], "game_over")
        self.assertEqual(manifest["summary"]["clean_trainable"], 1)

    def test_run_episode_writes_per_run_shadow_advice(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = map_state()
            state["game_state"].update(
                {
                    "act": 1,
                    "class": "IRONCLAD",
                    "ascension_level": 0,
                    "route_evaluation": {
                        "options": [
                            {
                                "choice_index": 0,
                                "symbol": "M",
                                "score": 42,
                                "lookahead": {
                                    "forced_elite_within_3": False,
                                    "forced_combat_within_2": True,
                                    "nearest_rest": 2,
                                    "nearest_shop": None,
                                },
                            }
                        ]
                    },
                }
            )
            memory = StrategyMemory.load(learned_path=root / "learned.json")
            client = FakeClient([state, game_over_state(), game_over_state()])
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=root,
                    manifest_advice_dir=root / "advice",
                    route_risk_model_path=root / "missing_route.json",
                    potion_tempo_model_path=root / "missing_potion.json",
                    deck_quality_model_path=root / "missing_deck.json",
                    combat_search_model_path=root / "missing_combat.json",
                )

            self.assertIsNotNone(result.manifest_path)
            self.assertIsNotNone(result.shadow_advice_path)
            assert result.manifest_path is not None
            assert result.shadow_advice_path is not None
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            summary = json.loads((result.shadow_advice_path / "summary.json").read_text(encoding="utf-8"))
            route_rows = [
                json.loads(line)
                for line in (result.shadow_advice_path / "route_risk_advice.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]

        self.assertEqual(manifest["shadow_advice"]["path"], str(result.shadow_advice_path))
        self.assertEqual(manifest["shadow_advice"]["advice_rows"]["route_risk"], 1)
        self.assertEqual(summary["advice_rows"]["route_risk"], 1)
        self.assertEqual(summary["advice_rows"]["combat_search"], 0)
        self.assertFalse(summary["models_available"]["route_risk"])
        self.assertFalse(summary["models_available"]["combat_search"])
        self.assertEqual(route_rows[0]["advice"], "model_missing")
        self.assertEqual(route_rows[0]["selected_symbol"], "M")

    def test_preflight_skips_unavailable_stale_action(self):
        before = {
            "in_game": True,
            "game_state": {
                "screen_type": "EVENT",
                "floor": 2,
                "event_options": [{"choice_index": 1, "label": "Leave"}],
            },
        }
        client = FakeClient([before], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertTrue(result.recovered)
        self.assertIn("choose", result.last_error or "")
        self.assertIn("proceed", result.last_error or "")

    def test_preflight_waits_when_map_choose_only_sees_proceed(self):
        before = map_state()
        before["game_state"]["map_options"] = [{"choice_index": 2, "symbol": "$", "x": 5, "y": 11}]
        client = FakeClient([before], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale map choose/proceed settled.")
        self.assertEqual(result.available_commands, ["proceed"])

    def test_preflight_waits_when_boss_map_choose_only_sees_proceed(self):
        before = map_state()
        before["game_state"]["floor"] = 15
        before["game_state"]["boss_available"] = True
        before["game_state"]["map_options"] = [{"choice_index": 1, "symbol": "BOSS", "x": 3, "y": 15}]
        client = FakeClient([before], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale map choose/proceed settled.")
        self.assertEqual(result.available_commands, ["proceed"])

    def test_preflight_waits_when_stale_map_proceed_becomes_choose(self):
        before = map_state()
        before["game_state"]["map_options"] = [{"choice_index": 1, "symbol": "M", "x": 3, "y": 0}]
        client = SequencedCommandsClient([before], command_tools=[["proceed"], ["choose", "return"]])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale map choose/proceed settled.")
        self.assertEqual(result.available_commands, ["proceed"])

    def test_preflight_rewrites_grid_confirm_to_proceed_when_confirm_unavailable(self):
        before = grid_state(confirm_up=True)
        client = FakeClient([rest_state(room_phase="COMPLETE", options=[])], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "confirm"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "proceed"}]])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.executed_actions, [{"action": "proceed"}])
        self.assertEqual(result.rewrite_reason, "Preflight action rewrite: grid confirm->proceed.")
        self.assertEqual(result.available_commands, ["proceed"])
        self.assertGreater(result.settle_ms, 800)

    def test_preflight_waits_when_stale_grid_proceed_becomes_choose(self):
        before = grid_state(confirm_up=False)
        client = SequencedCommandsClient([before], command_tools=[["proceed"], ["choose"]])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale grid choose command surface settled.")
        self.assertEqual(result.available_commands, ["proceed"])

    def test_preflight_rewrites_completed_chest_choose_to_proceed_when_choose_unavailable(self):
        before = {
            "in_game": True,
            "game_state": {
                "screen_type": "CHEST",
                "room_phase": "COMPLETE",
                "floor": 9,
            },
        }
        client = FakeClient([before], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "proceed"}]])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.executed_actions, [{"action": "proceed"}])
        self.assertEqual(result.rewrite_reason, "Preflight action rewrite: chest choose->proceed.")
        self.assertEqual(result.available_commands, ["proceed"])

    def test_preflight_rewrites_unverified_chest_proceed_to_choose_when_proceed_unavailable(self):
        before = {
            "in_game": True,
            "game_state": {
                "screen_type": "CHEST",
                "room_phase": "COMPLETE",
                "floor": 17,
                "screen_state": {"chest_open": False, "rewards": []},
            },
        }
        client = FakeClient([before], available_tools=["choose"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "proceed"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "choose", "choice_index": 1}]])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.executed_actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(result.rewrite_reason, "Preflight action rewrite: chest proceed->choose.")
        self.assertEqual(result.available_commands, ["choose"])

    def test_action_error_rewrites_unverified_chest_proceed_to_choose(self):
        before = {
            "in_game": True,
            "game_state": {
                "screen_type": "CHEST",
                "room_phase": "COMPLETE",
                "floor": 17,
                "screen_state": {"chest_open": False, "rewards": []},
            },
        }
        client = FakeClient(
            [before],
            action_error=MCPError(
                "Error at action 1: Invalid command: proceed. Possible commands: [choose, key, click, wait, save, state]"
            ),
            available_tools=["proceed"],
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "proceed"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(
            client.executed,
            [[{"action": "proceed"}], [{"action": "choose", "choice_index": 1}]],
        )
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "choose", "choice_index": 1}])
        self.assertEqual(
            result.rewrite_reason,
            "Action rewrite: chest proceed->choose after stale proceed failed.",
        )
        self.assertEqual(result.available_commands, ["choose", "key", "click", "wait", "save", "state"])

    def test_preflight_waits_out_stale_reward_proceed_commands(self):
        before = {
            "in_game": True,
            "game_state": {
                "screen_type": "COMBAT_REWARD",
                "room_phase": "COMPLETE",
                "floor": 13,
                "screen_state": {"rewards": []},
            },
        }
        refreshed = map_state()
        refreshed["game_state"]["floor"] = 13
        client = FakeClient([refreshed], available_tools=["cancel", "choose"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "proceed"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale reward proceed settled.")
        self.assertEqual(result.available_commands, ["cancel", "choose"])

    def test_preflight_waits_out_stale_combat_hand_select_commands(self):
        before = combat_state(turn=1, hand=[{"name": "Intimidate", "id": "Intimidate"}], energy=3)
        before["game_state"]["floor"] = 14
        refreshed = hand_select_state()
        refreshed["game_state"]["floor"] = 14
        client = FakeClient([refreshed], available_tools=["choose", "potion", "confirm"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale combat command surface settled.")
        self.assertEqual(result.available_commands, ["choose", "confirm", "potion"])

    def test_preflight_waits_when_reward_commands_arrive_before_combat_state_updates(self):
        before = combat_state(turn=4, hand=[{"name": "Strike", "id": "Strike_R"}], energy=0)
        before["game_state"]["floor"] = 18
        client = FakeClient([before], available_tools=["choose", "proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "end_turn"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Preflight wait: stale combat command surface settled.")
        self.assertEqual(result.available_commands, ["choose", "proceed"])

    def test_action_error_waits_out_stale_combat_to_reward_transition(self):
        before = combat_state(turn=4, hand=[{"name": "Strike", "id": "Strike_R"}], energy=0)
        before["game_state"]["floor"] = 18
        refreshed = {
            "in_game": True,
            "game_state": {
                "screen_type": "COMBAT_REWARD",
                "room_phase": "COMPLETE",
                "floor": 18,
                "act": 2,
                "current_hp": 57,
                "max_hp": 80,
                "screen_state": {"rewards": [{"reward_type": "GOLD", "choice_index": 1}]},
            },
        }
        client = FakeClient(
            [refreshed],
            action_error=MCPError(
                "Error at action 1: Invalid command: end. "
                "Possible commands: [choose, proceed, key, click, wait, save, state]"
            ),
            available_tools=["end_turn", "play_card"],
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "end_turn"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "end_turn"}]])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Action wait: stale combat transition settled.")
        self.assertEqual(result.available_commands, ["choose", "proceed", "key", "click", "wait", "save", "state"])

    def test_action_error_waits_when_reward_commands_arrive_before_combat_state_updates(self):
        before = combat_state(
            turn=5,
            hand=[
                {"name": "Iron Wave", "id": "Iron Wave", "type": "ATTACK", "cost": 1, "damage": 5},
                {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5},
            ],
            energy=2,
        )
        before["game_state"]["floor"] = 23
        client = FakeClient(
            [before],
            action_error=MCPError(
                "Error at action 1: Invalid command: play. "
                "Possible commands: [choose, proceed, key, click, wait, save, state]"
            ),
            available_tools=["end_turn", "play_card"],
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "play_card", "card_index": 2}]])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Action wait: stale combat transition settled.")
        self.assertEqual(result.available_commands, ["choose", "proceed", "key", "click", "wait", "save", "state"])

    def test_action_error_waits_out_stale_combat_to_reward_after_play_card(self):
        before = combat_state(
            turn=5,
            hand=[
                {"name": "Iron Wave", "id": "Iron Wave", "type": "ATTACK", "cost": 1, "damage": 5},
                {"name": "Defend", "id": "Defend_R", "type": "SKILL", "cost": 1, "block": 5},
            ],
            energy=2,
        )
        before["game_state"]["floor"] = 23
        refreshed = {
            "in_game": True,
            "game_state": {
                "screen_type": "COMBAT_REWARD",
                "room_phase": "COMPLETE",
                "floor": 23,
                "act": 2,
                "current_hp": 18,
                "max_hp": 80,
                "screen_state": {"rewards": [{"reward_type": "GOLD", "choice_index": 1}]},
            },
        }
        client = FakeClient(
            [refreshed],
            action_error=MCPError(
                "Error at action 1: Invalid command: play. "
                "Possible commands: [choose, proceed, key, click, wait, save, state]"
            ),
            available_tools=["end_turn", "play_card"],
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "play_card", "card_index": 2}]])
        self.assertEqual(result.status, "ok")
        self.assertIsNone(result.last_error)
        self.assertIsNone(result.recovered)
        self.assertEqual(result.executed_actions, [{"action": "wait"}])
        self.assertEqual(result.rewrite_reason, "Action wait: stale combat transition settled.")

    def test_preflight_does_not_rewrite_grid_confirm_when_selection_incomplete(self):
        before = grid_state(confirm_up=False, selected_cards=[])
        client = FakeClient([before], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "confirm"}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertIsNone(result.executed_actions)
        self.assertIsNone(result.rewrite_reason)
        self.assertIn("confirm", result.last_error or "")

    def test_preflight_rewrites_hand_select_drop_to_choose_when_select_unavailable(self):
        before = hand_select_state()
        client = FakeClient([before], available_tools=["choose"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "select_cards", "drop": [2]}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [[{"action": "choose", "choice_index": 2}]])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.executed_actions, [{"action": "choose", "choice_index": 2}])
        self.assertEqual(result.rewrite_reason, "Preflight action rewrite: hand select->choose.")
        self.assertEqual(result.available_commands, ["choose"])

    def test_preflight_rewrites_multi_hand_select_drop_to_choose_and_proceed(self):
        before = hand_select_state()
        client = FakeClient([before], available_tools=["choose", "proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "select_cards", "drop": [5, 3, 4, 2, 1]}],
                interval=0.01,
                before_state=before,
            )

        expected = [
            {"action": "choose", "choice_index": 5},
            {"action": "choose", "choice_index": 3},
            {"action": "choose", "choice_index": 4},
            {"action": "choose", "choice_index": 2},
            {"action": "choose", "choice_index": 1},
            {"action": "proceed"},
        ]
        self.assertEqual(client.executed, [expected])
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.executed_actions, expected)
        self.assertEqual(result.rewrite_reason, "Preflight action rewrite: hand select->choose.")
        self.assertEqual(result.available_commands, ["choose", "proceed"])

    def test_targeted_lethal_attack_waits_for_monster_list_refresh(self):
        before = lethal_target_attack_state()
        refreshed = lethal_target_attack_state(
            [{"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 16, "max_hp": 21}]
        )
        client = FakeClient([before, refreshed], available_tools=["play_card", "end_turn"])

        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 1, "target_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(client.executed, [[{"action": "play_card", "card_index": 1, "target_index": 2}]])
        self.assertGreaterEqual(result.settle_ms, 600)

    def test_use_potion_waits_for_combat_surface_after_distilled_chaos(self):
        before = combat_state(
            turn=5,
            hand=[
                {"name": "Bash", "id": "Bash", "type": "ATTACK", "cost": 2, "damage": 8, "is_playable": True},
                {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
            ],
            energy=3,
        )
        before["game_state"]["floor"] = 7
        before["game_state"]["potions"] = [
            {"id": "FairyPotion", "name": "Fairy Potion"},
            {"id": "Ancient Potion", "name": "Ancient Potion", "can_use": True},
            {"id": "DistilledChaos", "name": "Distilled Chaos", "can_use": True},
        ]
        before["game_state"]["combat_state"]["monsters"] = [
            {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 12, "max_hp": 30, "intent": "ATTACK"},
            {"name": "Cultist", "id": "Cultist", "current_hp": 34, "max_hp": 48, "intent": "ATTACK"},
        ]
        stale = json.loads(json.dumps(before))
        stale["game_state"]["potions"] = stale["game_state"]["potions"][:2]
        stale["game_state"]["combat_state"]["hand"].extend(
            [
                {"name": "Strike", "id": "Strike_R", "type": "ATTACK", "cost": 1, "damage": 6, "is_playable": True},
                {"name": "Headbutt", "id": "Headbutt", "type": "ATTACK", "cost": 1, "damage": 9, "is_playable": True},
            ]
        )
        refreshed = json.loads(json.dumps(stale))
        refreshed["game_state"]["combat_state"]["monsters"] = [
            {"name": "Cultist", "id": "Cultist", "current_hp": 34, "max_hp": 48, "intent": "ATTACK"}
        ]
        client = FakeClient([stale, refreshed], available_tools=["play_card", "end_turn", "use_potion"])

        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "use_potion", "potion_slot": 3}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(client.executed, [[{"action": "use_potion", "potion_slot": 3}]])
        self.assertGreaterEqual(result.settle_ms, 950)

    def test_preflight_skips_stale_target_index_out_of_range(self):
        before = lethal_target_attack_state(
            [{"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 16, "max_hp": 21}]
        )
        before["game_state"]["combat_state"]["cards_played_this_turn"] = 1
        client = FakeClient([before], available_tools=["play_card", "end_turn"])

        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 1, "target_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertTrue(result.recovered)
        self.assertIn("stale target_index", result.last_error or "")
        self.assertIn("outside current monster count 1", result.last_error or "")
        self.assertEqual(result.available_commands, ["end_turn", "play_card"])

    def test_preflight_skips_stale_target_index_dead_monster(self):
        before = lethal_target_attack_state(
            [
                {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 16, "max_hp": 21},
                {
                    "name": "Spike Slime",
                    "id": "SpikeSlime_M",
                    "current_hp": 0,
                    "max_hp": 21,
                    "is_dead": True,
                },
            ]
        )
        before["game_state"]["combat_state"]["cards_played_this_turn"] = 1
        client = FakeClient([before], available_tools=["play_card", "end_turn"])

        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "play_card", "card_index": 1, "target_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertTrue(result.recovered)
        self.assertIn("stale target_index", result.last_error or "")
        self.assertIn("defeated monster", result.last_error or "")
        self.assertEqual(result.available_commands, ["end_turn", "play_card"])

    def test_preflight_skips_stale_potion_target_index_out_of_range(self):
        before = combat_state(turn=2, hand=[{"name": "Strike"}], energy=3)
        before["game_state"]["potions"] = [
            {"id": "FirePotion", "name": "Fire Potion", "can_use": True, "requires_target": True}
        ]
        before["game_state"]["combat_state"]["monsters"] = [
            {"name": "Spike Slime", "id": "SpikeSlime_M", "current_hp": 16, "max_hp": 21}
        ]
        before["game_state"]["combat_state"]["cards_played_this_turn"] = 1
        client = FakeClient([before], available_tools=["use_potion", "end_turn"])

        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "use_potion", "potion_slot": 1, "target_index": 2}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertTrue(result.recovered)
        self.assertIn("stale target_index", result.last_error or "")
        self.assertIn("for use_potion", result.last_error or "")
        self.assertEqual(result.available_commands, ["end_turn", "use_potion"])

    def test_preflight_skips_stale_unavailable_potion_slot(self):
        before = combat_state(turn=2, hand=[{"name": "Strike"}], energy=3)
        before["game_state"]["potions"] = [
            {"id": "FirePotion", "name": "Fire Potion", "is_empty": True, "can_use": False}
        ]
        before["game_state"]["combat_state"]["cards_played_this_turn"] = 1
        client = FakeClient([before], available_tools=["use_potion", "end_turn"])

        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "use_potion", "potion_slot": 1}],
                interval=0.01,
                before_state=before,
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertTrue(result.recovered)
        self.assertIn("stale use_potion", result.last_error or "")
        self.assertIn("unavailable potion", result.last_error or "")
        self.assertEqual(result.available_commands, ["end_turn", "use_potion"])

    def test_shop_room_proceeds_after_shop_screen_cancel(self):
        shop_screen = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "room_phase": "COMPLETE",
                "floor": 13,
                "act": 1,
                "current_hp": 59,
                "max_hp": 80,
                "gold": 0,
                "screen_state": {
                    "cards": [{"id": "Flex", "name": "Flex", "type": "SKILL", "price": 99}],
                    "relics": [],
                    "potions": [],
                    "purge_available": False,
                    "purge_cost": 100,
                },
            },
        }
        shop_room_after_cancel = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_ROOM",
                "room_phase": "COMPLETE",
                "floor": 13,
                "current_hp": 59,
                "max_hp": 80,
                "gold": 0,
            },
        }
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = FakeClient([shop_screen, shop_room_after_cancel, game_over_state()])
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

        self.assertEqual(result.status, "game_over")
        self.assertEqual(client.executed, [[{"action": "cancel"}], [{"action": "proceed"}]])

    def test_main_menu_after_in_game_state_records_synthetic_game_over(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = FakeClient([dead_reward_state(), main_menu_state()])
            with patch.object(runner.time, "sleep", return_value=None):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=5,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

            self.assertEqual(result.status, "game_over")
            self.assertEqual(result.steps, 2)
            self.assertEqual(result.floor, 10)
            records = [
                json.loads(line)
                for line in result.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(record.get("event") == "synthetic_terminal_state" for record in records))
            self.assertEqual(records[-1]["state"]["outcome"]["source"], "synthetic_after_main_menu")

    def test_transient_null_state_read_retries(self):
        class NullThenStateClient:
            def __init__(self) -> None:
                self.calls = 0

            def get_game_state(self, include: list[str] | None = None) -> dict:
                self.calls += 1
                if self.calls < 3:
                    raise MCPError("-32603: Internal error: null")
                return game_over_state()

        client = NullThenStateClient()
        with patch.object(runner.time, "sleep", return_value=None):
            state = runner._read_game_state(client, attempts=3, delay=0.01)
        self.assertEqual(state["game_state"]["screen_type"], "GAME_OVER")
        self.assertEqual(client.calls, 3)

    def test_invalid_state_payload_retries_like_null_read(self):
        class InvalidThenStateClient:
            def __init__(self) -> None:
                self.calls = 0
                self.payloads = [
                    None,
                    {},
                    {"in_game": True, "game_state": None},
                    game_over_state(),
                ]

            def get_game_state(self, include: list[str] | None = None) -> dict:
                self.calls += 1
                return self.payloads[self.calls - 1]

        client = InvalidThenStateClient()
        with patch.object(runner.time, "sleep", return_value=None):
            state = runner._read_game_state(client, attempts=4, delay=0.01)
        self.assertEqual(state["game_state"]["screen_type"], "GAME_OVER")
        self.assertEqual(client.calls, 4)

    def test_map_observation_error_is_attached_without_failing_state_read(self):
        client = MapObservationErrorClient()
        with patch.object(runner.time, "sleep", return_value=None):
            state = runner._read_stable_game_state(client, attempts=1, delay=0.01)

        observation = state["game_state"]["map_observation"]
        self.assertEqual(observation["status"], "error")
        self.assertIn("Internal error", observation["error"])
        self.assertIn(["player", "screen", "map"], client.includes)

    def test_map_observation_success_is_compacted_in_snapshot(self):
        client = MapObservationSuccessClient()
        with patch.object(runner.time, "sleep", return_value=None):
            state = runner._read_stable_game_state(client, attempts=1, delay=0.01)

        snapshot = runner._snapshot_state(state)

        self.assertEqual(snapshot["map_observation"]["status"], "success")
        self.assertEqual(snapshot["map_observation"]["node_count"], 1)
        self.assertNotIn("map", snapshot["map_observation"])

    def test_empty_hand_after_first_turn_is_not_stable(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 2,
                    "player": {"current_energy": 0},
                    "hand": [],
                    "monsters": [{"name": "Looter", "current_hp": 20, "move": {"damage": 11}}],
                },
            },
        }
        client = FakeClient([state])
        self.assertFalse(runner._looks_stable(client, state))

    def test_empty_hand_with_energy_is_not_stable(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "NONE",
                "room_phase": "COMBAT",
                "combat_state": {
                    "turn": 2,
                    "player": {"current_energy": 3},
                    "hand": [],
                    "monsters": [{"name": "Louse", "current_hp": 12, "move": {"damage": 6}}],
                },
            },
        }
        client = FakeClient([state])
        self.assertFalse(runner._looks_stable(client, state))

    def test_partial_hand_at_turn_start_is_not_stable(self):
        state = combat_state(turn=2, hand=[{"name": "Dark Embrace"}], energy=3)
        client = FakeClient([state])
        self.assertFalse(runner._looks_stable(client, state))

    def test_partial_hand_after_current_turn_action_can_be_stable(self):
        state = combat_state(turn=2, hand=[{"name": "Strike"}], energy=2)
        state["game_state"]["combat_state"]["cards_played_this_turn"] = 1
        client = FakeClient([state])
        self.assertTrue(runner._looks_stable(client, state))

    def test_debug_monster_intent_before_actions_is_not_stable(self):
        state = combat_state(
            turn=1,
            hand=[{"name": "Strike"}, {"name": "Defend"}, {"name": "Bash"}],
            energy=3,
        )
        state["game_state"]["combat_state"]["monsters"] = [
            {"name": "Acid Slime", "current_hp": 12, "intent": "DEBUG", "move": {}},
            {"name": "Spike Slime", "current_hp": 14, "intent": "DEBUG", "move": {}},
        ]
        client = FakeClient([state])
        self.assertFalse(runner._looks_stable(client, state))

    def test_debug_monster_intent_after_current_turn_action_can_be_stable(self):
        state = combat_state(
            turn=1,
            hand=[{"name": "Strike"}, {"name": "Defend"}, {"name": "Bash"}],
            energy=2,
        )
        state["game_state"]["combat_state"]["cards_played_this_turn"] = 1
        state["game_state"]["combat_state"]["monsters"] = [
            {"name": "Acid Slime", "current_hp": 12, "intent": "DEBUG", "move": {}},
        ]
        client = FakeClient([state])
        self.assertTrue(runner._looks_stable(client, state))

    def test_empty_monsters_combat_snapshot_is_not_stable(self):
        state = combat_state(turn=2, hand=[{"name": "Strike"}], energy=3)
        state["game_state"]["combat_state"]["monsters"] = []
        client = FakeClient([state])
        self.assertFalse(runner._looks_stable(client, state))

    def test_snapshot_state_includes_combat_player_and_monster_details(self):
        state = combat_state(turn=2, hand=[{"name": "Strike", "damage": 6}], energy=2)
        game = state["game_state"]
        game["current_hp"] = 37
        game["max_hp"] = 88
        game["deck"] = [{"id": "Bash", "name": "unmapped display", "type": "ATTACK", "cost": 2, "damage": 8}]
        game["relics"] = [{"id": None, "name": "unmapped display", "relic_id": "Anchor"}]
        game["potions"] = [
            {
                "id": None,
                "name": "unmapped display",
                "potion_id": "FirePotion",
                "can_use": True,
                "requires_target": True,
            }
        ]
        game["combat_state"]["player"] = {
            "current_hp": 37,
            "max_hp": 88,
            "current_energy": 2,
            "block": 5,
            "powers": [{"id": "Vulnerable", "amount": 1}],
        }
        game["combat_state"]["monsters"] = [
            {
                "name": "Red Slaver",
                "id": "SlaverRed",
                "current_hp": 21,
                "max_hp": 46,
                "block": 3,
                "intent": "ATTACK",
                "move": {"damage": 7, "hits": 2},
                "powers": [{"id": "Weak", "amount": 1}],
            }
        ]

        snapshot = runner._snapshot_state(state)

        self.assertEqual(snapshot["combat"]["incoming_damage"], 14)
        self.assertEqual(snapshot["combat"]["player"]["current_energy"], 2)
        self.assertEqual(snapshot["combat"]["player"]["block"], 5)
        self.assertEqual(snapshot["combat"]["monsters"][0]["max_hp"], 46)
        self.assertEqual(snapshot["combat"]["monsters"][0]["block"], 3)
        self.assertEqual(snapshot["deck_cards"][0]["id"], "Bash")
        self.assertEqual(snapshot["deck_cards"][0]["damage"], 8)
        self.assertEqual(snapshot["relic_items"][0]["relic_id"], "Anchor")
        self.assertEqual(snapshot["potions"][0]["potion_id"], "FirePotion")
        self.assertTrue(snapshot["potions"][0]["can_use"])

    def test_snapshot_state_includes_shop_items_and_prices(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "floor": 5,
                "current_hp": 67,
                "max_hp": 88,
                "gold": 156,
                "screen_state": {
                    "cards": [
                        {"name": "Flame Barrier", "id": "Flame Barrier", "type": "SKILL", "cost": 2, "block": 12, "price": 75}
                    ],
                    "relics": [{"name": "Bag of Marbles", "id": "Bag of Marbles", "price": 150}],
                    "potions": [{"name": "Fire Potion", "id": "Fire Potion", "price": 50}],
                    "purge_available": True,
                    "purge_cost": 75,
                },
            },
        }

        snapshot = runner._snapshot_state(state)

        self.assertEqual(snapshot["shop"]["cards"][0]["id"], "Flame Barrier")
        self.assertEqual(snapshot["shop"]["cards"][0]["price"], 75)
        self.assertEqual(snapshot["shop"]["relics"][0]["price"], 150)
        self.assertEqual(snapshot["shop"]["potions"][0]["id"], "Fire Potion")
        self.assertTrue(snapshot["shop"]["purge_available"])
        self.assertEqual(snapshot["shop"]["purge_cost"], 75)

    def test_snapshot_state_preserves_reward_identity_fields(self):
        boss_reward = {
            "in_game": True,
            "game_state": {
                "screen_type": "BOSS_REWARD",
                "screen_state": {
                    "relics": [
                        {
                            "name": "unmapped display",
                            "id": None,
                            "relic_id": "Anchor",
                            "choice_index": 1,
                        }
                    ]
                },
            },
        }
        chest = {
            "in_game": True,
            "game_state": {
                "screen_type": "CHEST",
                "screen_state": {
                    "chest_open": True,
                    "rewards": [
                        {
                            "reward_type": "RELIC",
                            "name": "unmapped display",
                            "id": None,
                            "relic_id": "Anchor",
                            "choice_index": 2,
                        }
                    ],
                },
            },
        }
        shop = {
            "in_game": True,
            "game_state": {
                "screen_type": "SHOP_SCREEN",
                "screen_state": {
                    "cards": [{"name": "unmapped display", "card_id": "Bash", "price": 55}],
                    "relics": [{"name": "unmapped display", "relic_id": "Anchor", "price": 120}],
                    "potions": [{"name": "unmapped display", "potion_id": "FearPotion", "price": 40}],
                },
            },
        }

        boss_snapshot = runner._snapshot_state(boss_reward)
        chest_snapshot = runner._snapshot_state(chest)
        shop_snapshot = runner._snapshot_state(shop)

        self.assertEqual(boss_snapshot["boss_relic_options"][0]["relic_id"], "Anchor")
        self.assertEqual(boss_snapshot["boss_relic_options"][0]["choice_index"], 1)
        self.assertEqual(chest_snapshot["chest"]["rewards"][0]["relic_id"], "Anchor")
        self.assertEqual(chest_snapshot["chest"]["rewards"][0]["choice_index"], 2)
        self.assertEqual(shop_snapshot["shop"]["cards"][0]["card_id"], "Bash")
        self.assertEqual(shop_snapshot["shop"]["relics"][0]["relic_id"], "Anchor")
        self.assertEqual(shop_snapshot["shop"]["potions"][0]["potion_id"], "FearPotion")

    def test_snapshot_game_over_defaults_missing_victory_to_loss(self):
        state = {
            "in_game": True,
            "game_state": {
                "screen_type": "GAME_OVER",
                "floor": 16,
                "screen_state": {"score": None},
            },
        }

        snapshot = runner._snapshot_state(state)

        self.assertFalse(snapshot["outcome"]["victory"])
        self.assertIsNone(snapshot["outcome"]["score"])

    def test_null_after_likely_lethal_end_turn_records_synthetic_game_over(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = NullAfterActionClient()
            with (
                patch.object(runner.time, "sleep", return_value=None),
                patch.object(runner, "_wait_for_end_turn_transition", return_value=0.0),
            ):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

            self.assertEqual(result.status, "game_over")
            self.assertFalse(result.victory)
            self.assertEqual(result.floor, 14)
            records = [
                json.loads(line)
                for line in result.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(record.get("event") == "synthetic_terminal_state" for record in records))
            terminal_records = [
                record
                for record in records
                if record.get("state", {}).get("outcome", {}).get("victory") is False
            ]
            self.assertTrue(terminal_records)

    def test_null_with_game_over_commands_records_synthetic_game_over(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = NullWithGameOverCommandsClient()
            with (
                patch.object(runner.time, "sleep", return_value=None),
                patch.object(runner, "_wait_for_end_turn_transition", return_value=0.0),
            ):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

            self.assertEqual(result.status, "game_over")
            self.assertFalse(result.victory)
            self.assertEqual(result.floor, 16)
            records = [
                json.loads(line)
                for line in result.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(any(record.get("event") == "synthetic_terminal_state" for record in records))

    def test_null_with_game_over_commands_auto_proceeds_terminal_screen(self):
        with TemporaryDirectory() as tmp:
            memory = StrategyMemory.load(learned_path=Path(tmp) / "learned.json")
            client = TerminalRecoveryAfterNullClient()
            with (
                patch.object(runner.time, "sleep", return_value=None),
                patch.object(runner, "_wait_for_end_turn_transition", return_value=0.0),
            ):
                result = runner.run_episode(
                    client=client,
                    memory=memory,
                    max_steps=3,
                    interval=0.01,
                    log_dir=Path(tmp),
                )

            self.assertEqual(result.status, "game_over")
            self.assertIn([{"action": "proceed"}], client.executed)
            records = [
                json.loads(line)
                for line in result.log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            terminal_events = [
                record for record in records if record.get("event") == "synthetic_terminal_state"
            ]
            self.assertTrue(terminal_events)
            self.assertTrue(terminal_events[-1]["terminal_recovery_attempted"])
            self.assertTrue(terminal_events[-1]["terminal_recovery_succeeded"])

    def test_terminal_recovery_waits_until_game_over_clears(self):
        client = TerminalCommandsClearClient()

        with patch.object(runner.time, "sleep", return_value=None):
            recovered = runner._recover_terminal_game_over_after_state_failure(client)

        self.assertTrue(recovered)
        self.assertEqual(client.executed, [[{"action": "proceed"}]])
        self.assertGreaterEqual(client.command_reads, 2)

    def test_end_turn_waits_for_turn_transition(self):
        before = combat_state(turn=2, hand=[], energy=0)
        client = FakeClient(
            [
                combat_state(turn=2, hand=[], energy=0),
                combat_state(turn=3, hand=[], energy=0),
                combat_state(turn=3, hand=[{"name": "Strike"}], energy=3),
                combat_state(
                    turn=3,
                    hand=[{"name": "Strike"}, {"name": "Defend"}, {"name": "Bash"}],
                    energy=3,
                ),
            ]
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "end_turn"}],
                interval=0.01,
                before_state=before,
            )
        self.assertEqual(client.executed, [[{"action": "end_turn"}]])
        self.assertGreater(result.settle_ms, 900)

    def test_rest_choose_waits_for_rest_transition(self):
        before = rest_state(room_phase="INCOMPLETE")
        client = FakeClient(
            [
                rest_state(room_phase="INCOMPLETE"),
                rest_state(room_phase="COMPLETE", options=[]),
            ]
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
        )
        self.assertEqual(client.executed, [[{"action": "choose", "choice_index": 1}]])
        self.assertGreater(result.settle_ms, 650)

    def test_grid_choose_waits_until_confirm_is_available(self):
        before = grid_state(confirm_up=False)
        client = FakeClient([grid_state(confirm_up=True)])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=before,
            )
        self.assertEqual(client.executed, [[{"action": "choose", "choice_index": 1}]])
        self.assertGreater(result.settle_ms, 650)

    def test_grid_confirm_upgrade_waits_for_rest_complete(self):
        before = grid_state(confirm_up=True, for_upgrade=True)
        client = FakeClient(
            [
                rest_state(room_phase="INCOMPLETE"),
                rest_state(room_phase="COMPLETE", options=[]),
            ]
        )
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "confirm"}],
                interval=0.01,
                before_state=before,
            )
        self.assertEqual(client.executed, [[{"action": "confirm"}]])
        self.assertGreater(result.settle_ms, 750)


if __name__ == "__main__":
    unittest.main()
