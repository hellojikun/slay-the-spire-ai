import unittest
from http.client import RemoteDisconnected
from unittest.mock import patch

from slay_ai.mcp_client import MCPClient, MCPError


class MCPClientTests(unittest.TestCase):
    def test_remote_disconnect_is_wrapped_as_mcp_error(self):
        client = MCPClient(timeout=0.1)
        with patch("slay_ai.mcp_client.request.urlopen", side_effect=RemoteDisconnected("closed")):
            with self.assertRaises(MCPError) as raised:
                client.get_screen_state()
        self.assertIn("Cannot reach MCPTheSpire", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
