"""ASGI application for the MCP Gateway Streamable HTTP endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.lowlevel import Server
from mcp.server.models import InitializationOptions
from mcp.server.streamable_http import StreamableHTTPServerTransport

from mcp_gateway.config import GatewayConfig
from mcp_gateway.server import create_gateway_server, get_initialization_options

logger = logging.getLogger(__name__)


class MCPEndpoint:
    """ASGI app managing MCP sessions — delegates directly to transports
    so SSE streaming works correctly."""

    def __init__(self, max_sessions: int = 100) -> None:
        self._sessions: dict[str, StreamableHTTPServerTransport] = {}
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._session_ready: dict[str, asyncio.Event] = {}
        self._server: Server | None = None
        self._init_options: InitializationOptions | None = None
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions

    def set_server(self, server: Server, init_options: InitializationOptions) -> None:
        self._server = server
        self._init_options = init_options

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI entry point."""
        if scope["type"] != "http":
            return

        headers = dict(scope.get("headers", []))
        method = scope.get("method", "GET")
        session_id_bytes = headers.get(b"mcp-session-id", b"")
        session_id = session_id_bytes.decode() if session_id_bytes else None

        if method == "DELETE":
            await self._handle_delete(session_id, send)
            return

        # Existing session -> delegate to its transport
        if session_id and session_id in self._sessions:
            transport = self._sessions[session_id]
            await transport.handle_request(scope, receive, send)
            return

        # New POST -> create session
        if method == "POST":
            await self._handle_new_session(scope, receive, send)
            return

        # GET without session
        if method == "GET":
            await _send_json(send, 400, {"error": "Missing or invalid Mcp-Session-Id"})
            return

        await _send_json(send, 405, {"error": "Method not allowed"})

    async def _handle_new_session(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        async with self._lock:
            if len(self._sessions) >= self._max_sessions:
                await _send_json(send, 503, {
                    "error": f"Max sessions ({self._max_sessions}) reached"
                })
                return

            new_session_id = uuid.uuid4().hex
            transport = StreamableHTTPServerTransport(
                mcp_session_id=new_session_id,
                is_json_response_enabled=False,
            )
            self._sessions[new_session_id] = transport

            # Create an event for synchronization — no more sleep() race
            ready_event = asyncio.Event()
            self._session_ready[new_session_id] = ready_event

            task = asyncio.create_task(
                self._run_server_session(transport, new_session_id, ready_event)
            )
            self._session_tasks[new_session_id] = task

        # Wait for connect() to establish streams (with timeout)
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error("Session %s: connect() timed out", new_session_id)
            self._sessions.pop(new_session_id, None)
            self._session_tasks.pop(new_session_id, None)
            self._session_ready.pop(new_session_id, None)
            await _send_json(send, 503, {"error": "Session initialization timed out"})
            return

        await transport.handle_request(scope, receive, send)

    async def _run_server_session(
        self,
        transport: StreamableHTTPServerTransport,
        session_id: str,
        ready_event: asyncio.Event,
    ) -> None:
        try:
            async with transport.connect() as (read_stream, write_stream):
                # Signal that streams are ready
                ready_event.set()
                await self._server.run(
                    read_stream,
                    write_stream,
                    self._init_options,
                )
        except asyncio.CancelledError:
            logger.debug("Session %s cancelled", session_id)
        except Exception:
            logger.exception("Session %s error", session_id)
        finally:
            # Ensure event is set even on failure so request doesn't hang
            ready_event.set()
            self._sessions.pop(session_id, None)
            self._session_tasks.pop(session_id, None)
            self._session_ready.pop(session_id, None)
            logger.debug("Session %s closed", session_id)

    async def _handle_delete(self, session_id: str | None, send: Send) -> None:
        if not session_id or session_id not in self._sessions:
            await _send_json(send, 404, {"error": "Session not found"})
            return
        transport = self._sessions.pop(session_id, None)
        task = self._session_tasks.pop(session_id, None)
        self._session_ready.pop(session_id, None)
        if transport:
            await transport.terminate()
        if task and not task.done():
            task.cancel()
        await _send_json(send, 200, {"status": "terminated"})

    async def terminate_all(self) -> None:
        """Shut down all active sessions with proper cleanup."""
        for sid, transport in list(self._sessions.items()):
            try:
                await transport.terminate()
            except Exception:
                logger.warning("Failed to terminate session %s", sid, exc_info=True)

        for sid, task in list(self._session_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        self._sessions.clear()
        self._session_tasks.clear()
        self._session_ready.clear()


async def _send_json(send: Send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(data)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": data,
    })


def create_app(config: GatewayConfig) -> Starlette:
    """Create the Starlette ASGI application for the MCP Gateway."""
    mcp_endpoint = MCPEndpoint(max_sessions=config.max_sessions)
    gateway_ref: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        ctx = create_gateway_server(config)
        server, gateway = await ctx.__aenter__()
        try:
            init_options = get_initialization_options(config, server)
            mcp_endpoint.set_server(server, init_options)
            gateway_ref["gw"] = gateway
            gateway_ref["ctx"] = ctx
            logger.info(
                "Gateway ready — %d tools from %d servers",
                len(gateway.list_tools()),
                sum(1 for s in gateway.upstream.servers.values() if s.connected),
            )
            yield
        finally:
            await mcp_endpoint.terminate_all()
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                logger.warning("Shutdown cleanup error", exc_info=True)

    async def health(request: Request) -> JSONResponse:
        gw = gateway_ref.get("gw")
        servers = {}
        all_connected = True
        if gw:
            for name, srv in gw.upstream.servers.items():
                servers[name] = {
                    "connected": srv.connected,
                    "tools": len(srv.tools),
                    "resources": len(srv.resources),
                    "prompts": len(srv.prompts),
                }
                if not srv.connected:
                    all_connected = False

        status = "ok" if all_connected else "degraded"
        return JSONResponse({
            "status": status,
            "sessions": len(mcp_endpoint._sessions),
            "servers": servers,
            "total_tools": sum(s.get("tools", 0) for s in servers.values()),
        })

    async def reload(request: Request) -> JSONResponse:
        gw = gateway_ref.get("gw")
        if gw:
            await gw.refresh()
        tools = gw.list_tools() if gw else []
        return JSONResponse({"status": "reloaded", "tools": len(tools)})

    starlette_app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/reload", reload, methods=["POST"]),
        ],
        lifespan=lifespan,
    )

    mcp_path = config.path.rstrip("/")

    # Wrap the MCP endpoint in CORS middleware so it actually applies
    cors_mcp = CORSMiddleware(
        app=_ASGICallable(mcp_endpoint.handle),
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Accept", "Mcp-Session-Id"],
        expose_headers=["Mcp-Session-Id"],
    )

    # Also wrap Starlette routes in the same CORS policy
    cors_rest = CORSMiddleware(
        app=starlette_app,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    class GatewayASGI:
        """Top-level ASGI app that splits traffic between MCP and REST."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                await starlette_app(scope, receive, send)
                return

            path = scope.get("path", "")
            if path == mcp_path or path == mcp_path + "/":
                await cors_mcp(scope, receive, send)
            else:
                await cors_rest(scope, receive, send)

    return GatewayASGI()  # type: ignore[return-value]


class _ASGICallable:
    """Wrap an async callable as a proper ASGI app for CORSMiddleware."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._handler(scope, receive, send)
