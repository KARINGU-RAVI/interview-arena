"""MCP client: connects to our own MCP server (mcp_server/server.py) via langchain-mcp-adapters.

Why this shape:
- MCP sessions are async; our LangGraph nodes are plain (sync) functions. So the client
  runs ONE background event loop thread that owns ONE long-lived stdio session.
  Nodes call tools with a normal blocking call_tool(...).
- "Start once and reuse": by default MultiServerMCPClient opens a NEW server process and
  session for every tool call (adapter README). We open client.session(...) once and keep
  the loaded tools, so the server starts once per app process.
- The adapter returns a tool's dict as JSON text inside content blocks; we parse it back.

Note: langchain-mcp-adapters was archived on 17 Sep 2026 (successor: the beta
`langchain.mcp`). We use the installed, pinned 0.3.2, which works with mcp 1.30.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

import config

log = logging.getLogger("interview_arena.mcp_client")

SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server" / "server.py"
SERVER_NAME = "interview-arena"


class MCPToolError(RuntimeError):
    """A tool ran but reported an error (message is safe to show)."""


class MCPToolbox:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="mcp-client-loop")
        self._thread.start()
        self._stack: AsyncExitStack | None = None
        self.tools: dict = {}
        self._run(self._open(), timeout=60)

    def _run(self, coro, timeout: float):
        """Run a coroutine on the background loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    async def _open(self) -> None:
        client = MultiServerMCPClient(
            {
                SERVER_NAME: {
                    "command": sys.executable,  # same Python / venv as the app
                    "args": [str(SERVER_SCRIPT)],
                    "transport": "stdio",
                }
            }
        )
        self._stack = AsyncExitStack()
        session = await self._stack.enter_async_context(client.session(SERVER_NAME))
        self.tools = {t.name: t for t in await load_mcp_tools(session)}
        log.info("MCP server started; tools: %s", sorted(self.tools))

    def call_tool(self, name: str, **args) -> dict:
        """Call a tool and return its result dict, e.g. {"path": ...} or {"draft_id": ...}."""
        if name not in self.tools:
            raise MCPToolError(f"Unknown MCP tool {name!r}. Available: {sorted(self.tools)}")
        result = self._run(self.tools[name].ainvoke(args), timeout=config.MCP_CALL_TIMEOUT_SECONDS)
        # Result = list of content blocks ([{"type": "text", "text": "<json>"}]) or a string.
        text = "".join(b.get("text", "") for b in result) if isinstance(result, list) else str(result)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Tool errors come back as plain text (adapter: handle_tool_errors=True).
            raise MCPToolError(text.strip()[:500]) from None

    def close(self) -> None:
        if self._stack is not None:
            try:
                self._run(self._stack.aclose(), timeout=15)
            except Exception as exc:  # shutting down anyway — log, don't crash on exit
                log.debug("MCP close: %s", type(exc).__name__)
            self._stack = None
        self._loop.call_soon_threadsafe(self._loop.stop)


_toolbox: MCPToolbox | None = None
_lock = threading.Lock()


def get_toolbox() -> MCPToolbox:
    """The shared MCP client for this process (started on first use, then reused)."""
    global _toolbox
    with _lock:
        if _toolbox is None:
            _toolbox = MCPToolbox()
            atexit.register(_toolbox.close)
        return _toolbox
