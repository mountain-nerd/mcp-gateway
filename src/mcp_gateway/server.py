"""MCP Gateway server - exposes aggregated upstream tools via Streamable HTTP."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import types
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions

from mcp_gateway.config import GatewayConfig
from mcp_gateway.gateway import Gateway
from mcp_gateway.upstream import UpstreamManager

logger = logging.getLogger(__name__)


def _build_notification_options() -> NotificationOptions:
    return NotificationOptions(
        tools_changed=True,
        resources_changed=True,
        prompts_changed=True,
    )


@asynccontextmanager
async def create_gateway_server(
    config: GatewayConfig,
) -> AsyncIterator[tuple[Server, Gateway]]:
    """Create and configure the MCP gateway server with upstream connections.

    Yields (server, gateway) inside an async context that manages
    upstream connection lifecycle.
    """
    upstream = UpstreamManager()
    gateway = Gateway(upstream)

    # Connect to all upstream MCP servers
    await upstream.connect_all(config.mcp_servers)

    server = Server(config.name)

    # ── Tool handlers ────────────────────────────────────────────────

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return gateway.list_tools()

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> types.CallToolResult:
        return await gateway.call_tool(name, arguments or {})

    # ── Resource handlers ────────────────────────────────────────────

    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return gateway.list_resources()

    @server.list_resource_templates()
    async def handle_list_resource_templates() -> list[types.ResourceTemplate]:
        return gateway.list_resource_templates()

    @server.read_resource()
    async def handle_read_resource(uri: Any) -> str | bytes:
        result = await gateway.read_resource(str(uri))
        content = result.contents[0]
        if isinstance(content, types.TextResourceContents):
            return content.text
        if isinstance(content, types.BlobResourceContents):
            return content.blob
        return str(content)

    # ── Prompt handlers ──────────────────────────────────────────────

    @server.list_prompts()
    async def handle_list_prompts() -> list[types.Prompt]:
        return gateway.list_prompts()

    @server.get_prompt()
    async def handle_get_prompt(
        name: str, arguments: dict[str, str] | None
    ) -> types.GetPromptResult:
        return await gateway.get_prompt(name, arguments)

    # ── Tool change notification forwarding ──────────────────────────

    async def on_upstream_tools_changed() -> None:
        """Forward tool change notifications to downstream clients."""
        try:
            await server.request_context.session.send_tools_list_changed()
        except Exception:
            logger.debug("Could not send tool change notification (no active session)")

    upstream.on_tool_change(on_upstream_tools_changed)

    try:
        yield server, gateway
    finally:
        await upstream.close()


def get_initialization_options(config: GatewayConfig, server: Server) -> InitializationOptions:
    """Build initialization options for the server."""
    return InitializationOptions(
        server_name=config.name,
        server_version=config.version,
        capabilities=server.get_capabilities(
            notification_options=_build_notification_options(),
            experimental_capabilities={},
        ),
    )
