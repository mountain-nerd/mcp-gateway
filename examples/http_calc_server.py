"""MCP server demonstrating Streamable HTTP transport.

Exposes math tools (add, subtract, multiply, divide) over HTTP.
Use with the gateway to demonstrate:
  - HTTP upstream transport
  - Tool filtering (e.g. exclude: [divide])

Usage:
  python examples/http_calc_server.py              # default port 8081
  python examples/http_calc_server.py --port 9000
"""

import argparse
import asyncio
import json
import uuid
from typing import Any

import uvicorn
from starlette.middleware.cors import CORSMiddleware
from starlette.types import Receive, Scope, Send

from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp import types

server = Server("calc")


# ── Tools ────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "number", "description": "First operand"},
            "b": {"type": "number", "description": "Second operand"},
        },
        "required": ["a", "b"],
    }
    return [
        types.Tool(name="add", description="Add two numbers", inputSchema=schema),
        types.Tool(name="subtract", description="Subtract b from a", inputSchema=schema),
        types.Tool(name="multiply", description="Multiply two numbers", inputSchema=schema),
        types.Tool(name="divide", description="Divide a by b", inputSchema=schema),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    a, b = arguments["a"], arguments["b"]
    ops = {
        "add": a + b,
        "subtract": a - b,
        "multiply": a * b,
    }
    if name == "divide":
        if b == 0:
            return [types.TextContent(type="text", text="Error: division by zero")]
        return [types.TextContent(type="text", text=str(a / b))]
    if name in ops:
        return [types.TextContent(type="text", text=str(ops[name]))]
    raise ValueError(f"Unknown tool: {name}")


# ── Streamable HTTP ASGI app ────────────────────────────────────────

init_options = InitializationOptions(
    server_name="calc",
    server_version="0.1.0",
    capabilities=server.get_capabilities(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    ),
)


class CalcMCPApp:
    """Minimal ASGI app that manages sessions for the calc MCP server."""

    def __init__(self) -> None:
        self._sessions: dict[str, StreamableHTTPServerTransport] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return

        headers = dict(scope.get("headers", []))
        method = scope.get("method", "GET")
        session_id = (headers.get(b"mcp-session-id", b"") or b"").decode() or None

        if method == "DELETE":
            if session_id and session_id in self._sessions:
                transport = self._sessions.pop(session_id)
                await transport.terminate()
            await self._send_json(send, 200, {"status": "ok"})
            return

        if session_id and session_id in self._sessions:
            await self._sessions[session_id].handle_request(scope, receive, send)
            return

        if method == "POST":
            new_id = uuid.uuid4().hex
            transport = StreamableHTTPServerTransport(
                mcp_session_id=new_id,
                is_json_response_enabled=False,
            )
            self._sessions[new_id] = transport
            asyncio.create_task(self._run_session(transport, new_id))
            await asyncio.sleep(0.01)
            await transport.handle_request(scope, receive, send)
            return

        await self._send_json(send, 400, {"error": "Invalid request"})

    async def _run_session(
        self, transport: StreamableHTTPServerTransport, session_id: str
    ) -> None:
        try:
            async with transport.connect() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, init_options)
        except Exception:
            pass
        finally:
            self._sessions.pop(session_id, None)

    @staticmethod
    async def _send_json(send: Send, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [[b"content-type", b"application/json"]],
        })
        await send({"type": "http.response.body", "body": data})


class CalcASGI:
    """Top-level ASGI app routing /mcp to the MCP handler."""

    def __init__(self) -> None:
        self._mcp = CalcMCPApp()
        self._cors = CORSMiddleware(
            app=self._mcp,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Content-Type", "Accept", "Mcp-Session-Id"],
            expose_headers=["Mcp-Session-Id"],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Simple lifespan passthrough
            await receive()
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
            return

        path = scope.get("path", "")
        if path == "/mcp" or path == "/mcp/":
            await self._cors(scope, receive, send)
        else:
            await self._mcp._send_json(send, 404, {"error": "Not found"})


def create_app() -> CalcASGI:
    return CalcASGI()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calc MCP server (HTTP)")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    print(f"Calc MCP server running on http://127.0.0.1:{args.port}/mcp")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")
