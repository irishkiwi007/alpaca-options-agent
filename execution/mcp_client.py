"""
Wraps Alpaca's official MCP server (alpacahq/alpaca-mcp-server) as a
stdio subprocess and exposes its tools through a small async client.

This is what actually satisfies the hackathon's requirement to use
Alpaca's Trading API "via its MCP server or CLI" — execution in this
repo goes through the same MCP tool surface Claude Desktop/Cursor use,
not a direct alpaca-py call. See execution/alpaca_client.py for the
higher-level wrapper built on top of this.
"""
import json
import shutil
import os
from contextlib import AsyncExitStack
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import CONFIG


def _resolve_server_command() -> str:
    """
    Locate the alpaca-mcp-server executable robustly. Interactive shells
    typically have ~/.local/bin on PATH (where pip --user installs land),
    but systemd services get a minimal default PATH that doesn't include
    it — this caused a real deployment failure (FileNotFoundError) the
    first time this ran as a systemd service despite working fine when
    run manually. Checking common install locations directly, not just
    relying on PATH, makes this robust regardless of how it's launched.
    """
    found = shutil.which("alpaca-mcp-server")
    if found:
        return found

    candidates = [
        os.path.expanduser("~/.local/bin/alpaca-mcp-server"),
        "/usr/local/bin/alpaca-mcp-server",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    raise FileNotFoundError(
        "alpaca-mcp-server executable not found on PATH or in common install "
        f"locations ({candidates}). Ensure it's installed (`pip install alpaca-mcp-server`) "
        "and, if running under systemd, that the service's PATH includes the install directory."
    )


def unwrap_data(result: Any) -> Any:
    """
    The Alpaca MCP server wraps every response as
    {"_alpaca_mcp_security": {...}, "data": {...actual fields...}}.
    Callers that read specific fields (equity, positions, etc.) need the
    unwrapped payload, not the envelope. This was a real bug found via
    live testing — the drawdown monitor was silently reading equity as
    0.0 because callers were reading top-level fields that only exist
    under "data". Safe to call on non-enveloped results too (returns
    them unchanged).
    """
    if isinstance(result, dict) and "data" in result and "_alpaca_mcp_security" in result:
        return result["data"]
    return result


class AlpacaMCPToolError(Exception):
    """Raised when an Alpaca MCP tool call fails, wrapping the server's error text."""
    pass


class AlpacaMCPClient:
    """
    Async context manager. Usage:

        async with AlpacaMCPClient() as mcp:
            account = await mcp.call_tool("get_account_info", {})
    """

    def __init__(self, config=CONFIG):
        self.config = config
        self._session: Optional[ClientSession] = None
        self._stack: Optional[AsyncExitStack] = None

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        server_params = StdioServerParameters(
            command=_resolve_server_command(),
            args=["--transport", "stdio"],
            env={
                "ALPACA_API_KEY": self.config.alpaca.api_key,
                "ALPACA_SECRET_KEY": self.config.alpaca.secret_key,
                "ALPACA_PAPER_TRADE": "True",
            },
        )
        read, write = await self._stack.enter_async_context(stdio_client(server_params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._stack:
            await self._stack.aclose()

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """
        Raises AlpacaMCPToolError on failure rather than returning an
        error string as if it were data. The underlying server (FastMCP)
        catches its own internal exceptions and returns them as text
        content instead of propagating a protocol-level error, so this
        client normalizes that back into a real exception — callers
        throughout fast_layer/execution rely on try/except, not on
        duck-typing an error string.
        """
        assert self._session is not None, "AlpacaMCPClient used outside 'async with' block"
        result = await self._session.call_tool(name, arguments)

        if getattr(result, "isError", False):
            text = result.content[0].text if result.content and hasattr(result.content[0], "text") else str(result.content)
            raise AlpacaMCPToolError(f"Tool '{name}' returned an error: {text}")

        if result.content and hasattr(result.content[0], "text"):
            text = result.content[0].text
            if isinstance(text, str) and text.startswith("Error calling tool"):
                raise AlpacaMCPToolError(text)
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
        return result.content

    async def list_tools(self) -> list:
        assert self._session is not None
        tools = await self._session.list_tools()
        return [t.name for t in tools.tools]
