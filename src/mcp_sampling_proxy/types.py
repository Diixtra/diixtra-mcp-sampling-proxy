"""Shared types for mcp-sampling-proxy."""

from dataclasses import dataclass

import mcp.types as mcp_types


@dataclass
class DiscoveredTool:
    """A tool discovered from the upstream MCP server."""

    name: str
    description: str | None
    input_schema: dict
    output_schema: dict | None = None


# Re-export commonly used MCP types
CreateMessageRequestParams = mcp_types.CreateMessageRequestParams
CreateMessageResult = mcp_types.CreateMessageResult
TextContent = mcp_types.TextContent
Tool = mcp_types.Tool
CallToolResult = mcp_types.CallToolResult
