"""Lightweight MCPTheSpire health probe.

This intentionally does not restart Slay the Spire. It gives campaign scripts
and humans a quick answer: healthy, state_broken, or unreachable.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from .mcp_client import MCPClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe MCPTheSpire health without mutating game state by default.")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/mcp")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--json", action="store_true", help="Print raw JSON probe output.")
    parser.add_argument(
        "--recover-terminal",
        action="store_true",
        help="If state tools are broken on a terminal GAME_OVER screen, send proceed and probe again.",
    )
    args = parser.parse_args(argv)

    client = MCPClient(args.endpoint, timeout=8.0)
    last: dict[str, Any] | None = None
    for attempt in range(1, max(1, args.attempts) + 1):
        last = client.probe_health()
        last["attempt"] = attempt
        if last["status"] == "healthy":
            break
        if attempt < args.attempts:
            time.sleep(args.delay)

    assert last is not None
    if args.recover_terminal and last["status"] == "state_broken":
        recovered = _recover_terminal_screen(client)
        last["recovery_attempted"] = True
        last["recovery_succeeded"] = recovered
        if recovered:
            last = client.probe_health()
            last["attempt"] = "recovery"
            last["recovery_attempted"] = True
            last["recovery_succeeded"] = True
    if args.json:
        print(json.dumps(last, ensure_ascii=False, indent=2))
    else:
        _print_summary(last)
    return _exit_code(last["status"])


def _print_summary(result: dict[str, Any]) -> None:
    print(f"MCP status: {result['status']}")
    for probe in result.get("probes", []):
        marker = "ok" if probe.get("ok") else "fail"
        line = f"- {probe.get('name')}: {marker} ({probe.get('latency_ms')} ms)"
        if probe.get("error"):
            line += f" - {probe['error']}"
        print(line)
    if result["status"] == "state_broken":
        if result.get("recovery_attempted") and result.get("recovery_succeeded"):
            print("Terminal screen recovery succeeded.")
        else:
            print("State tools are failing while the MCP protocol still responds. Try --recover-terminal, or restart ModTheSpire.")
    elif result["status"] == "unreachable":
        print("MCP endpoint is unreachable. Check whether ModTheSpire and MCPTheSpire are running.")


def _exit_code(status: str) -> int:
    if status == "healthy":
        return 0
    if status == "state_broken":
        return 1
    return 2


def _recover_terminal_screen(client: MCPClient) -> bool:
    try:
        commands = client.call_tool("get_available_commands", {})
    except Exception:
        return False
    if commands.get("screen_type") != "GAME_OVER":
        return False
    tools = commands.get("available_tools", [])
    tool_names = {
        str(tool.get("tool", tool.get("name", ""))) if isinstance(tool, dict) else str(tool)
        for tool in tools
    }
    if "proceed" not in tool_names:
        return False
    try:
        client.execute_actions([{"action": "proceed"}])
    except Exception:
        return False
    time.sleep(0.5)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
