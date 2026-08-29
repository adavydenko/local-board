"""Protocol conformance: drive our hand-rolled MCP server with the official SDK client.

The `mcp` package is a CI-only dev dependency (see ci.yml); the shipped package
stays dependency-free. Locally, without the SDK installed, this module skips.
Its job is to catch silent drift between our hand-rolled JSON-RPC/Streamable
HTTP implementation and what a real, spec-tracking client actually sends and
expects — the risk accepted in the build-vs-buy decision on PR #25.
"""

import asyncio
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from local_board.db import Board
from local_board.web import make_handler


def _is_error(result):
    """SDK 2.x uses is_error; 1.x used isError."""
    return getattr(result, "is_error", None) if hasattr(result, "is_error") else result.isError

try:
    from mcp import ClientSession

    try:  # SDK >= 2.x
        from mcp.client.streamable_http import streamable_http_client
    except ImportError:  # SDK 1.x
        from mcp.client.streamable_http import streamablehttp_client as streamable_http_client
    try:
        import httpx2 as _httpx
    except ImportError:  # pragma: no cover - depends on SDK's HTTP backend
        import httpx as _httpx
    SDK_IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - exercised only without the dev dep
    SDK_IMPORT_ERROR = exc


@unittest.skipIf(
    SDK_IMPORT_ERROR is not None,
    f"official MCP SDK not installed (CI-only dev dependency): {SDK_IMPORT_ERROR}",
)
class McpSdkConformanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.actor = self.board.create_actor("sdk-agent")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def _run(self, scenario):
        async def main():
            async with _httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.actor['token']}"}
            ) as http_client:
                async with streamable_http_client(self.url, http_client=http_client) as streams:
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        init = await session.initialize()
                        return await scenario(session, init)

        return asyncio.run(main())

    def test_initialize_carries_briefing_and_identity(self):
        async def scenario(session, init):
            server_info = getattr(init, "server_info", None) or init.serverInfo
            self.assertEqual(server_info.name, "local-board")
            self.assertIn("sdk-agent", init.instructions)
            self.assertIn("APP", init.instructions)

        self._run(scenario)

    def test_tools_list_and_call_roundtrip(self):
        async def scenario(session, init):
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            self.assertIn("create_issue", names)
            self.assertIn("whoami", names)

            created = await session.call_tool("create_issue", {"title": "SDK conformance"})
            self.assertFalse(_is_error(created))
            self.assertIn("APP-1", created.content[0].text)

            fetched = await session.call_tool("get_issue", {"issue": "APP-1"})
            self.assertFalse(_is_error(fetched))
            self.assertIn("SDK conformance", fetched.content[0].text)

        self._run(scenario)

    def test_invalid_arguments_surface_as_tool_error(self):
        async def scenario(session, init):
            result = await session.call_tool("get_issue", {"issue_id": "APP-1"})
            self.assertTrue(_is_error(result))
            self.assertIn("issue", result.content[0].text)

        self._run(scenario)

    def test_ping(self):
        async def scenario(session, init):
            await session.send_ping()

        self._run(scenario)


if __name__ == "__main__":
    unittest.main()
