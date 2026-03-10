"""MCP Client → upstream server (connect, discover tools, callTool)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.context import RequestContext

from mcp_sampling_proxy.types import DiscoveredTool

if TYPE_CHECKING:
    from mcp_sampling_proxy.config import Config
    from mcp_sampling_proxy.sampling import SamplingExecutor

import mcp.types as mcp_types
from mcp.shared.exceptions import McpError


def _debug(config: Config, msg: str) -> None:
    if config.debug:
        print(f"[upstream] {msg}", file=sys.stderr)


class UpstreamClient:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._session: ClientSession | None = None
        self._streams_ctx: Any = None
        self._session_ctx: Any = None

    async def connect(self, sampling: SamplingExecutor) -> list[DiscoveredTool]:
        """Connect to upstream, register sampling handler, discover tools."""
        config = self._config

        async def sampling_callback(
            context: RequestContext[ClientSession, None],
            params: mcp_types.CreateMessageRequestParams,
        ) -> mcp_types.CreateMessageResult:
            _debug(config, "received sampling/createMessage request")
            return await sampling.execute(params)

        _debug(config, f"connecting to {config.upstream_url}")

        self._streams_ctx = streamable_http_client(config.upstream_url)
        read_stream, write_stream, _ = await self._streams_ctx.__aenter__()

        self._session_ctx = ClientSession(
            read_stream,
            write_stream,
            sampling_callback=sampling_callback,
        )
        self._session = await self._session_ctx.__aenter__()
        await self._session.initialize()

        _debug(config, "connected, discovering tools...")

        # Paginated tool discovery
        tools: list[DiscoveredTool] = []
        cursor: str | None = None
        while True:
            result = await self._session.list_tools(cursor=cursor)
            for t in result.tools:
                tools.append(
                    DiscoveredTool(
                        name=t.name,
                        description=t.description,
                        input_schema=t.inputSchema,
                        output_schema=t.outputSchema if hasattr(t, "outputSchema") else None,
                    )
                )
            cursor = result.nextCursor
            if not cursor:
                break

        _debug(config, f"discovered {len(tools)} tools: {[t.name for t in tools]}")
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> mcp_types.CallToolResult:
        """Forward a tool call to the upstream server."""
        if not self._session:
            raise McpError(
                mcp_types.ErrorData(code=mcp_types.INTERNAL_ERROR, message="Not connected to upstream")
            )
        _debug(self._config, f"calling upstream tool: {name}")
        return await self._session.call_tool(name, arguments)

    async def disconnect(self) -> None:
        """Disconnect from upstream."""
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        if self._streams_ctx:
            try:
                await self._streams_ctx.__aexit__(None, None, None)
            except Exception:
                pass
        self._session = None
        _debug(self._config, "disconnected from upstream")
