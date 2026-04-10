"""Core gateway logic - aggregation, namespacing, and filtering."""

from __future__ import annotations

import logging
from typing import Any

from mcp import types

from mcp_gateway.upstream import UpstreamManager

logger = logging.getLogger(__name__)


class Gateway:
    """Aggregates tools/resources/prompts from upstream MCP servers
    and exposes them under a unified namespace."""

    NAMESPACE_SEP = "__"

    def __init__(self, upstream: UpstreamManager) -> None:
        self.upstream = upstream

    # ── Tools ────────────────────────────────────────────────────────────

    def list_tools(self) -> list[types.Tool]:
        """Return all tools from all upstream servers, namespaced."""
        tools: list[types.Tool] = []
        for name, server in self.upstream.servers.items():
            if not server.connected:
                continue
            for tool in server.tools:
                namespaced = types.Tool(
                    name=f"{name}{self.NAMESPACE_SEP}{tool.name}",
                    description=f"[{name}] {tool.description or ''}",
                    inputSchema=tool.inputSchema,
                )
                tools.append(namespaced)
        return tools

    async def call_tool(
        self, namespaced_name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """Route a tool call to the correct upstream server."""
        result = self.upstream.get_server_for_tool(namespaced_name)
        if result is None:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Unknown tool: {namespaced_name}",
                    )
                ],
                isError=True,
            )

        server, original_name = result
        logger.info("Routing tool call %s -> %s.%s", namespaced_name, server.name, original_name)
        try:
            return await server.call_tool(original_name, arguments)
        except Exception as exc:
            logger.exception("Tool call failed: %s", namespaced_name)
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"Tool call failed: {exc}",
                    )
                ],
                isError=True,
            )

    # ── Resources ────────────────────────────────────────────────────────

    def list_resources(self) -> list[types.Resource]:
        """Return all resources from all upstream servers."""
        resources: list[types.Resource] = []
        for name, server in self.upstream.servers.items():
            if not server.connected:
                continue
            for resource in server.resources:
                # Resources are identified by URI, so we prefix the name only
                prefixed = types.Resource(
                    uri=resource.uri,
                    name=f"[{name}] {resource.name or ''}",
                    description=resource.description,
                    mimeType=resource.mimeType,
                )
                resources.append(prefixed)
        return resources

    def list_resource_templates(self) -> list[types.ResourceTemplate]:
        """Return all resource templates from all upstream servers."""
        templates: list[types.ResourceTemplate] = []
        for name, server in self.upstream.servers.items():
            if not server.connected:
                continue
            for tmpl in server.resource_templates:
                prefixed = types.ResourceTemplate(
                    uriTemplate=tmpl.uriTemplate,
                    name=f"[{name}] {tmpl.name or ''}",
                    description=tmpl.description,
                    mimeType=tmpl.mimeType,
                )
                templates.append(prefixed)
        return templates

    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        """Route a resource read to the correct upstream server."""
        server = self.upstream.get_server_for_resource(uri)
        if server is None:
            raise ValueError(f"Unknown resource: {uri}")
        return await server.read_resource(uri)

    # ── Prompts ──────────────────────────────────────────────────────────

    def list_prompts(self) -> list[types.Prompt]:
        """Return all prompts from all upstream servers, namespaced."""
        prompts: list[types.Prompt] = []
        for name, server in self.upstream.servers.items():
            if not server.connected:
                continue
            for prompt in server.prompts:
                namespaced = types.Prompt(
                    name=f"{name}{self.NAMESPACE_SEP}{prompt.name}",
                    description=f"[{name}] {prompt.description or ''}",
                    arguments=prompt.arguments,
                )
                prompts.append(namespaced)
        return prompts

    async def get_prompt(
        self, namespaced_name: str, arguments: dict[str, str] | None = None
    ) -> types.GetPromptResult:
        """Route a prompt request to the correct upstream server."""
        result = self.upstream.get_server_for_prompt(namespaced_name)
        if result is None:
            raise ValueError(f"Unknown prompt: {namespaced_name}")
        server, original_name = result
        return await server.get_prompt(original_name, arguments)

    # ── Refresh ──────────────────────────────────────────────────────────

    async def refresh(self) -> None:
        """Refresh all upstream servers' tool/resource/prompt lists."""
        await self.upstream.refresh_all()
