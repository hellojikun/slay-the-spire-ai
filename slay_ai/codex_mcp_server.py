"""Codex MCP wrapper for the local MCPTheSpire endpoint."""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable

from .core.state_reader import read_stable_game_state
from .mcp.client import MCPClient, MCPError
from .memory import StrategyMemory
from .policy import HeuristicPolicy


ENDPOINT = "http://127.0.0.1:8080/mcp"
CLIENT = MCPClient(ENDPOINT)


def main() -> int:
    while True:
        message = _read_message()
        if message is None:
            return 0
        response = _handle_message(message)
        if response is not None:
            _write_message(response)


def _handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "slay-the-spire-codex", "version": "0.1.0"},
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name not in TOOL_HANDLERS:
                raise ValueError(f"Unknown tool: {name}")
            payload = TOOL_HANDLERS[name](arguments)
            return _tool_result(request_id, payload)
        if method in {"resources/list", "prompts/list"}:
            key = "resources" if method == "resources/list" else "prompts"
            return _result(request_id, {key: []})
        return _error(request_id, -32601, f"Method not found: {method}")
    except Exception as exc:
        return _tool_error(request_id, exc)


def _ensure_client() -> MCPClient:
    CLIENT.ensure_initialized()
    return CLIENT


def _probe_health(_: dict[str, Any]) -> Any:
    return _ensure_client().probe_health()


def _list_spire_tools(_: dict[str, Any]) -> Any:
    return _ensure_client().list_tools()


def _get_game_state(arguments: dict[str, Any]) -> Any:
    include = arguments.get("include")
    if include is not None and not isinstance(include, list):
        raise ValueError("include must be a list of section names")
    return _ensure_client().get_game_state(include)


def _get_screen_state(_: dict[str, Any]) -> Any:
    return _ensure_client().get_screen_state()


def _get_available_commands(_: dict[str, Any]) -> Any:
    return _ensure_client().call_tool("get_available_commands", {})


def _execute_actions(arguments: dict[str, Any]) -> Any:
    actions = arguments.get("actions")
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    return _ensure_client().execute_actions(actions)


def _decide_next(arguments: dict[str, Any]) -> Any:
    state = read_stable_game_state(_ensure_client())
    character = arguments.get("character") or state.get("game_state", {}).get("class") or "IRONCLAD"
    decision = HeuristicPolicy(StrategyMemory.load(), character=str(character)).decide(state)
    return {
        "summary": _summarize_state(state),
        "reason": decision.reason,
        "actions": decision.actions,
        "should_stop": decision.should_stop,
        "learn_card_pick": decision.learn_card_pick,
    }


def _execute_next(arguments: dict[str, Any]) -> Any:
    decision = _decide_next(arguments)
    actions = decision.get("actions") or []
    if not actions or decision.get("should_stop"):
        return {"executed": False, **decision}
    result = _ensure_client().execute_actions(actions)
    return {"executed": True, "result": result, **decision}


def _summarize_state(state: dict[str, Any]) -> str:
    if not state.get("in_game"):
        return "MAIN_MENU"
    game = state.get("game_state", {})
    floor = game.get("floor", "?")
    hp = f"{game.get('current_hp', '?')}/{game.get('max_hp', '?')}"
    screen = game.get("screen_type")
    room = game.get("room_phase")
    if screen == "NONE" and room == "COMBAT":
        combat = game.get("combat_state", {})
        monsters = ", ".join(m.get("name", "?") for m in combat.get("monsters", []))
        return f"F{floor} combat HP {hp} vs {monsters}"
    return f"F{floor} {screen} HP {hp}"


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS = [
    {
        "name": "sts_probe_health",
        "description": "Check whether MCPTheSpire is reachable and game-state tools are healthy.",
        "inputSchema": _schema(),
    },
    {
        "name": "sts_list_spire_tools",
        "description": "List raw tools exposed by MCPTheSpire.",
        "inputSchema": _schema(),
    },
    {
        "name": "sts_get_game_state",
        "description": "Read the current Slay the Spire game state.",
        "inputSchema": _schema(
            {
                "include": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional MCPTheSpire section names, such as player, screen, combat, map.",
                }
            }
        ),
    },
    {
        "name": "sts_get_screen_state",
        "description": "Read the safer screen-only state from MCPTheSpire.",
        "inputSchema": _schema(),
    },
    {
        "name": "sts_get_available_commands",
        "description": "Read the currently available Slay the Spire commands.",
        "inputSchema": _schema(),
    },
    {
        "name": "sts_execute_actions",
        "description": "Execute one or more MCPTheSpire actions. Use only after inspecting state and available commands.",
        "inputSchema": _schema(
            {"actions": {"type": "array", "items": {"type": "object"}}},
            ["actions"],
        ),
    },
    {
        "name": "sts_decide_next",
        "description": "Ask the local heuristic policy for the next Slay the Spire action without executing it.",
        "inputSchema": _schema({"character": {"type": "string"}}),
    },
    {
        "name": "sts_execute_next",
        "description": "Execute one next action batch selected by the local heuristic policy.",
        "inputSchema": _schema({"character": {"type": "string"}}),
    },
]


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "sts_probe_health": _probe_health,
    "sts_list_spire_tools": _list_spire_tools,
    "sts_get_game_state": _get_game_state,
    "sts_get_screen_state": _get_screen_state,
    "sts_get_available_commands": _get_available_commands,
    "sts_execute_actions": _execute_actions,
    "sts_decide_next": _decide_next,
    "sts_execute_next": _execute_next,
}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_result(request_id: Any, payload: Any) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _tool_error(request_id: Any, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, (MCPError, ValueError)):
        text = str(exc)
    else:
        text = traceback.format_exc()
    return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": True})


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in {b"\r\n", b"\n"}:
            break
        key, _, value = line.decode("ascii").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write_message(message: dict[str, Any]) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    raise SystemExit(main())
