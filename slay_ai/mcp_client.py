"""Compatibility imports for the MCP adapter.

New code should import from ``slay_ai.mcp.client``.
"""

from __future__ import annotations

from .mcp.client import MCPClient, MCPError, MCPProbe

__all__ = ["MCPClient", "MCPError", "MCPProbe"]
