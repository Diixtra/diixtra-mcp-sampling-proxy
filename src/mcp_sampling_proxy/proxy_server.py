"""MCP Server → Claude Code (register tools, stdio transport).

Uses the low-level Server API to pass raw JSON schemas through
without Zod/pydantic conversion — critical for transparent proxying.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

import mcp.server.stdio
import mcp.types as mcp_types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.shared.exceptions import McpError

from mcp_sampling_proxy.types import DiscoveredTool

if TYPE_CHECKING:
    from mcp_sampling_proxy.config import Config
    from mcp_sampling_proxy.upstream import UpstreamClient


def _debug(config: Config, msg: str) -> None:
    if config.debug:
        print(f"[proxy] {msg}", file=sys.stderr)


class ProxyServer:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._server = Server("mcp-sampling-proxy")
        self._tools: list[DiscoveredTool] = []
        self._upstream: UpstreamClient | None = None

    def register_tools(
        self, tools: list[DiscoveredTool], upstream: UpstreamClient
    ) -> None:
        """Register discovered upstream tools on the stdio server."""
        self._tools = tools
        self._upstream = upstream
        config = self._config

        @self._server.list_tools()
        async def handle_list_tools() -> list[mcp_types.Tool]:
            return [
                mcp_types.Tool(
                    name=t.name,
                    description=t.description or "",
                    inputSchema=t.input_schema,
                )
                for t in self._tools
            ]

        @self._server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict[str, Any] | None
        ) -> list[mcp_types.TextContent | mcp_types.ImageContent | mcp_types.EmbeddedResource]:
            _debug(config, f"tool call: {name}")
            if not self._upstream:
                raise McpError(
                    mcp_types.ErrorData(code=mcp_types.INTERNAL_ERROR, message="No upstream connection")
                )
            result = await self._upstream.call_tool(name, arguments or {})
            return result.content

        _debug(config, f"registered {len(tools)} tools on stdio server")

    async def start(self) -> None:
        """Start the stdio server — blocks until connection closes."""
        _debug(self._config, "starting stdio server")
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="mcp-sampling-proxy",
                    server_version="0.1.0",
                    capabilities=self._server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
