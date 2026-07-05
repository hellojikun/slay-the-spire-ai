import unittest
from http.client import RemoteDisconnected
from unittest.mock import patch

from slay_ai.mcp.client import MCPClient, MCPError
from slay_ai.mcp_client import MCPClient as LegacyMCPClient


class FakeResponse:
    def __init__(self, body: str, session_id: str | None = "session-1") -> None:
        self.body = body
        self.headers = {}
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode("utf-8")


class MCPClientTests(unittest.TestCase):
    def test_remote_disconnect_is_wrapped_as_mcp_error(self):
        client = MCPClient(timeout=0.1)
        with patch("slay_ai.mcp.client.request.urlopen", side_effect=RemoteDisconnected("closed")):
            with self.assertRaises(MCPError) as raised:
                client.get_screen_state()
        self.assertIn("Cannot reach MCPTheSpire", str(raised.exception))

    def test_legacy_import_still_exports_client(self):
        self.assertIs(LegacyMCPClient, MCPClient)

    def test_initialize_is_idempotent_for_reused_session(self):
        client = MCPClient(timeout=0.1)
        response = FakeResponse('{"jsonrpc":"2.0","id":1,"result":{"server":"ok"}}')
        with patch("slay_ai.mcp.client.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(client.initialize(), {"server": "ok"})
            self.assertEqual(client.initialize(), {"server": "ok"})
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(client.initialized)

    def test_invalid_json_response_is_wrapped_as_mcp_error(self):
        client = MCPClient(timeout=0.1)
        with patch("slay_ai.mcp.client.request.urlopen", return_value=FakeResponse("<html>bad gateway</html>", None)):
            with self.assertRaises(MCPError) as raised:
                client.get_screen_state()
        self.assertIn("Invalid JSON from MCPTheSpire", str(raised.exception))
        self.assertIn("bad gateway", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
