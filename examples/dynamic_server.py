"""MCP server demonstrating dynamic tool discovery via stdio.

Starts with a base set of tools, then adds/removes tools every 30 seconds
and emits notifications/tools/list_changed so the gateway picks up changes.

Demonstrates:
  - Dynamic tool registration at runtime
  - notifications/tools/list_changed forwarding through the gateway
  - Tool include filtering (gateway can whitelist specific tools)
"""

import asyncio
from typing import Any

from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp import types
import mcp.server.stdio

server = Server("dynamic")

# Mutable tool registry
_tools: dict[str, types.Tool] = {}
_cycle = 0


def _register_base_tools() -> None:
    """Register the always-available tools."""
    _tools["timestamp"] = types.Tool(
        name="timestamp",
        description="Return the current UTC timestamp",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    )
    _tools["uptime"] = types.Tool(
        name="uptime",
        description="Return how many tool-cycle rotations have occurred",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    )


# Rotating tools — only one is available at a time
_ROTATING_TOOLS = [
    types.Tool(
        name="greet",
        description="Return a greeting message",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name to greet"},
            },
            "required": ["name"],
        },
    ),
    types.Tool(
        name="reverse",
        description="Reverse a string",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to reverse"},
            },
            "required": ["text"],
        },
    ),
    types.Tool(
        name="count_words",
        description="Count words in a text",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to count words in"},
            },
            "required": ["text"],
        },
    ),
]

_register_base_tools()
# Start with the first rotating tool
_tools[_ROTATING_TOOLS[0].name] = _ROTATING_TOOLS[0]


# ── Handlers ─────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return list(_tools.values())


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    import datetime

    if name == "timestamp":
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return [types.TextContent(type="text", text=now)]

    if name == "uptime":
        return [types.TextContent(type="text", text=f"Cycle: {_cycle}")]

    if name == "greet":
        return [types.TextContent(type="text", text=f"Hello, {arguments['name']}!")]

    if name == "reverse":
        return [types.TextContent(type="text", text=arguments["text"][::-1])]

    if name == "count_words":
        count = len(arguments["text"].split())
        return [types.TextContent(type="text", text=f"{count} words")]

    raise ValueError(f"Unknown tool: {name}")


# ── Tool rotation loop ──────────────────────────────────────────────

async def _rotate_tools() -> None:
    """Rotate which extra tool is available and notify the client."""
    global _cycle
    while True:
        await asyncio.sleep(30)
        _cycle += 1

        # Remove current rotating tool, add the next one
        for rt in _ROTATING_TOOLS:
            _tools.pop(rt.name, None)

        next_tool = _ROTATING_TOOLS[_cycle % len(_ROTATING_TOOLS)]
        _tools[next_tool.name] = next_tool

        # Notify connected clients that the tool list changed
        try:
            await server.request_context.session.send_tools_list_changed()
        except Exception:
            pass  # No active session


# ── Main ─────────────────────────────────────────────────────────────

async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read, write):
        # Start the rotation loop in the background
        rotation_task = asyncio.create_task(_rotate_tools())
        try:
            await server.run(
                read,
                write,
                InitializationOptions(
                    server_name="dynamic",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(
                            tools_changed=True,
                        ),
                        experimental_capabilities={},
                    ),
                ),
            )
        finally:
            rotation_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
