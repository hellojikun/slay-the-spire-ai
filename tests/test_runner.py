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


class InDungeonContinueClient(FakeClient):
    def __init__(self) -> None:
        super().__init__([target_a0_combat_state()])
        self.tool_calls: list[str] = []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict | str:
        self.tool_calls.append(name)
        if name == "continue_game":
            raise MCPError("continue_game should not be called while already in game")
        return super().call_tool(name, arguments)


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


class RunnerTests(unittest.TestCase):
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

    def test_preflight_skips_unavailable_stale_action(self):
        client = FakeClient([map_state()], available_tools=["proceed"])
        with patch.object(runner.time, "sleep", return_value=None):
            result = runner._execute_actions_with_settle(
                client,
                [{"action": "choose", "choice_index": 1}],
                interval=0.01,
                before_state=map_state(),
            )

        self.assertEqual(client.executed, [])
        self.assertEqual(result.status, "preflight_mismatch")
        self.assertTrue(result.recovered)
        self.assertIn("choose", result.last_error or "")
        self.assertIn("proceed", result.last_error or "")

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
        game["potions"] = [{"id": "FirePotion", "name": "Fire Potion", "can_use": True, "requires_target": True}]
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
