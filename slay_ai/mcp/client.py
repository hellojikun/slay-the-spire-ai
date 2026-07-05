"""Small JSON-RPC client for MCPTheSpire's streamable HTTP endpoint."""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any, Callable
from urllib import error, request


class MCPError(RuntimeError):
    """Raised when MCPTheSpire returns an error or cannot be reached."""


@dataclass
class MCPProbe:
    name: str
    ok: bool
    latency_ms: int
    error: str | None = None
    summary: Any | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
        }
        if self.error is not None:
            data["error"] = self.error
        if self.summary is not None:
            data["summary"] = self.summary
        return data


@dataclass
class MCPClient:
    endpoint: str = "http://127.0.0.1:8080/mcp"
    timeout: float = 35.0

    def __post_init__(self) -> None:
        self._ids = itertools.count(1)
        self.session_id: str | None = None
        self.initialized = False
        self._initialize_result: dict[str, Any] | None = None

    def initialize(self) -> dict[str, Any]:
        if self.initialized:
            return self._initialize_result or {}
        result = self.rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "slay-ai", "version": "0.1.0"},
            },
        )
        self.initialized = True
        self._initialize_result = result
        return result

    def ensure_initialized(self) -> dict[str, Any]:
        return self.initialize()

    def list_tools(self) -> list[dict[str, Any]]:
        return self.rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self.rpc(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if result.get("isError"):
            text = _content_text(result)
            raise MCPError(text or f"Tool call failed: {name}")
        text = _content_text(result)
        if text is None:
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def get_game_state(self, include: list[str] | None = None) -> dict[str, Any]:
        args = {"include": include} if include else {}
        return self.call_tool("get_game_state", args)

    def get_screen_state(self) -> dict[str, Any]:
        return self.call_tool("get_screen_state", {})

    def execute_actions(self, actions: list[dict[str, Any]]) -> Any:
        return self.call_tool("execute_actions", {"actions": actions})

    def probe_health(self) -> dict[str, Any]:
        probes = [
            self._probe("list_tools", self.list_tools, _summarize_tools),
            self._probe("get_available_commands", lambda: self.call_tool("get_available_commands", {}), _summarize_mapping),
            self._probe("get_screen_state", self.get_screen_state, _summarize_state_like),
            self._probe(
                "get_game_state_minimal",
                lambda: self.get_game_state(["player", "screen"]),
                _summarize_state_like,
            ),
        ]
        probe_dicts = [probe.as_dict() for probe in probes]
        protocol_ok = any(probe.name == "list_tools" and probe.ok for probe in probes)
        state_ok = any(probe.name in {"get_screen_state", "get_game_state_minimal"} and probe.ok for probe in probes)
        commands_ok = any(probe.name == "get_available_commands" and probe.ok for probe in probes)
        if state_ok:
            status = "healthy"
        elif protocol_ok or commands_ok:
            status = "state_broken"
        else:
            status = "unreachable"
        return {
            "status": status,
            "protocol_ok": protocol_ok,
            "commands_ok": commands_ok,
            "state_ok": state_ok,
            "probes": probe_dicts,
        }

    def _probe(self, name: str, func: Callable[[], Any], summarize: Callable[[Any], Any]) -> MCPProbe:
        started = time.monotonic()
        try:
            result = func()
        except MCPError as exc:
            return MCPProbe(name=name, ok=False, latency_ms=_elapsed_ms(started), error=str(exc))
        except Exception as exc:
            return MCPProbe(name=name, ok=False, latency_ms=_elapsed_ms(started), error=repr(exc))
        return MCPProbe(name=name, ok=True, latency_ms=_elapsed_ms(started), summary=summarize(result))

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        req = request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                body = response.read().decode("utf-8")
        except (error.URLError, RemoteDisconnected, TimeoutError, ConnectionError) as exc:
            raise MCPError(f"Cannot reach MCPTheSpire at {self.endpoint}: {exc}") from exc

        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError as exc:
            preview = (body or "")[:500].replace("\n", "\\n")
            raise MCPError(f"Invalid JSON from MCPTheSpire: {preview}") from exc
        if "error" in data:
            err = data["error"]
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return data.get("result", {})


def _content_text(result: dict[str, Any]) -> str | None:
    content = result.get("content")
    if not content:
        return None
    first = content[0]
    if isinstance(first, dict):
        return first.get("text")
    return None


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _summarize_tools(result: Any) -> dict[str, Any]:
    if not isinstance(result, list):
        return {"type": type(result).__name__}
    return {
        "count": len(result),
        "tools": [str(tool.get("name", tool.get("tool", "?"))) for tool in result[:12] if isinstance(tool, dict)],
    }


def _summarize_mapping(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    summary: dict[str, Any] = {}
    for key in ("screen_type", "screen_name", "room_phase", "ready_for_command", "in_game"):
        if key in result:
            summary[key] = result[key]
    tools = result.get("available_tools")
    if isinstance(tools, list):
        summary["available_tools"] = [
            str(tool.get("tool", tool)) if isinstance(tool, dict) else str(tool)
            for tool in tools[:12]
        ]
    return summary or {"keys": sorted(str(key) for key in result.keys())[:12]}


def _summarize_state_like(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    if "game_state" in result and isinstance(result.get("game_state"), dict):
        game = result["game_state"]
        return {
            "in_game": result.get("in_game"),
            "ready_for_command": result.get("ready_for_command"),
            "screen_type": game.get("screen_type"),
            "room_phase": game.get("room_phase"),
            "floor": game.get("floor"),
            "current_hp": game.get("current_hp"),
        }
    return _summarize_mapping(result)
