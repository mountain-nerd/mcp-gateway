"""Upstream MCP server connection manager."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from mcp_gateway.config import UpstreamServerConfig

logger = logging.getLogger(__name__)


@dataclass
class UpstreamServer:
    """A live connection to an upstream MCP server."""

    name: str
    config: UpstreamServerConfig
    session: ClientSession | None = None
    tools: list[types.Tool] = field(default_factory=list)
    resources: list[types.Resource] = field(default_factory=list)
    resource_templates: list[types.ResourceTemplate] = field(default_factory=list)
    prompts: list[types.Prompt] = field(default_factory=list)
    connected: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def refresh_tools(self) -> None:
        """Fetch and cache the tool list from this server."""
        if not self.session:
            return
        try:
            result = await self.session.list_tools()
            self.tools = self._filter_tools(result.tools)
            logger.info("Server %s: discovered %d tools", self.name, len(self.tools))
        except Exception:
            logger.exception("Failed to list tools from %s", self.name)

    async def refresh_resources(self) -> None:
        """Fetch and cache resources from this server."""
        if not self.session or not self.config.tools.resources:
            self.resources = []
            self.resource_templates = []
            return
        try:
            result = await self.session.list_resources()
            self.resources = result.resources
            logger.info("Server %s: discovered %d resources", self.name, len(self.resources))
        except Exception:
            logger.debug("Server %s does not support resources", self.name)
            self.resources = []

        try:
            result = await self.session.list_resource_templates()
            self.resource_templates = result.resourceTemplates
        except Exception:
            self.resource_templates = []

    async def refresh_prompts(self) -> None:
        """Fetch and cache prompts from this server."""
        if not self.session or not self.config.tools.prompts:
            self.prompts = []
            return
        try:
            result = await self.session.list_prompts()
            self.prompts = result.prompts
            logger.info("Server %s: discovered %d prompts", self.name, len(self.prompts))
        except Exception:
            logger.debug("Server %s does not support prompts", self.name)
            self.prompts = []

    async def refresh_all(self) -> None:
        """Refresh tools, resources, and prompts."""
        async with self._lock:
            await asyncio.gather(
                self.refresh_tools(),
                self.refresh_resources(),
                self.refresh_prompts(),
            )

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """Call a tool on this upstream server."""
        if not self.session:
            raise RuntimeError(f"Server {self.name} is not connected")
        return await self.session.call_tool(tool_name, arguments)

    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        """Read a resource from this upstream server."""
        if not self.session:
            raise RuntimeError(f"Server {self.name} is not connected")
        return await self.session.read_resource(uri)

    async def get_prompt(
        self, prompt_name: str, arguments: dict[str, str] | None = None
    ) -> types.GetPromptResult:
        """Get a prompt from this upstream server."""
        if not self.session:
            raise RuntimeError(f"Server {self.name} is not connected")
        return await self.session.get_prompt(prompt_name, arguments)

    def _filter_tools(self, tools: list[types.Tool]) -> list[types.Tool]:
        """Apply include/exclude filters to a tool list."""
        cfg = self.config.tools
        if cfg.include is not None:
            tools = [t for t in tools if t.name in cfg.include]
        if cfg.exclude is not None:
            tools = [t for t in tools if t.name not in cfg.exclude]
        return tools


class UpstreamManager:
    """Manages connections to all upstream MCP servers."""

    def __init__(self) -> None:
        self.servers: dict[str, UpstreamServer] = {}
        self._exit_stack = AsyncExitStack()
        self._tool_change_callbacks: list[Any] = []

    def on_tool_change(self, callback: Any) -> None:
        """Register a callback for when upstream tools change."""
        self._tool_change_callbacks.append(callback)

    async def _notify_tool_change(self) -> None:
        for cb in self._tool_change_callbacks:
            try:
                await cb()
            except Exception:
                logger.exception("Tool change callback failed")

    async def connect(self, name: str, config: UpstreamServerConfig) -> UpstreamServer:
        """Connect to a single upstream MCP server."""
        server = UpstreamServer(name=name, config=config)

        try:
            if config.transport_type == "stdio":
                server_params = StdioServerParameters(
                    command=config.command,
                    args=config.args,
                    env=config.env if config.env else None,
                )
                transport = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
            else:
                transport = await self._exit_stack.enter_async_context(
                    streamable_http_client(
                        config.url,
                        headers=config.headers if config.headers else None,
                        timeout=config.connect_timeout,
                    )
                )
                # streamable_http_client returns (read, write, session_id)
                transport = (transport[0], transport[1])

            read_stream, write_stream = transport

            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            server.session = session
            server.connected = True
            self.servers[name] = server

            await server.refresh_all()

            logger.info("Connected to upstream server: %s (%s)", name, config.transport_type)

        except Exception:
            logger.exception("Failed to connect to upstream server: %s", name)
            server.connected = False
            self.servers[name] = server

        return server

    async def connect_all(self, configs: dict[str, UpstreamServerConfig]) -> None:
        """Connect to all configured upstream servers."""
        tasks = []
        for name, config in configs.items():
            if not config.enabled:
                logger.info("Skipping disabled server: %s", name)
                continue
            tasks.append(self.connect(name, config))
        await asyncio.gather(*tasks)

    async def refresh_server(self, name: str) -> None:
        """Refresh a single server's tools/resources/prompts."""
        server = self.servers.get(name)
        if server and server.connected:
            await server.refresh_all()
            await self._notify_tool_change()

    async def refresh_all(self) -> None:
        """Refresh all connected servers."""
        tasks = [
            s.refresh_all()
            for s in self.servers.values()
            if s.connected
        ]
        await asyncio.gather(*tasks)
        await self._notify_tool_change()

    def get_server_for_tool(self, namespaced_name: str) -> tuple[UpstreamServer, str] | None:
        """Resolve a namespaced tool name to (server, original_tool_name).

        Namespaced format: {server_name}__{tool_name}
        """
        for server_name, server in self.servers.items():
            prefix = f"{server_name}__"
            if namespaced_name.startswith(prefix):
                original_name = namespaced_name[len(prefix):]
                if any(t.name == original_name for t in server.tools):
                    return server, original_name
        return None

    def get_server_for_resource(self, uri: str) -> UpstreamServer | None:
        """Find which server owns a given resource URI."""
        for server in self.servers.values():
            if not server.connected:
                continue
            for r in server.resources:
                if str(r.uri) == uri:
                    return server
        return None

    def get_server_for_prompt(self, namespaced_name: str) -> tuple[UpstreamServer, str] | None:
        """Resolve a namespaced prompt name to (server, original_prompt_name)."""
        for server_name, server in self.servers.items():
            prefix = f"{server_name}__"
            if namespaced_name.startswith(prefix):
                original_name = namespaced_name[len(prefix):]
                if any(p.name == original_name for p in server.prompts):
                    return server, original_name
        return None

    async def close(self) -> None:
        """Close all upstream connections."""
        await self._exit_stack.aclose()
        self.servers.clear()
