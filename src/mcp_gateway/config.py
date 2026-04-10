"""Configuration models for MCP Gateway."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class ToolFilterConfig(BaseModel):
    """Per-server tool filtering configuration."""

    include: list[str] | None = None
    exclude: list[str] | None = None
    resources: bool = True
    prompts: bool = True


class UpstreamServerConfig(BaseModel):
    """Configuration for a single upstream MCP server."""

    # Stdio transport
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    # HTTP transport
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    # Common
    enabled: bool = True
    timeout: float = 30.0
    connect_timeout: float = 10.0

    # Tool filtering
    tools: ToolFilterConfig = Field(default_factory=ToolFilterConfig)

    @model_validator(mode="after")
    def _validate_transport(self) -> UpstreamServerConfig:
        if self.command and self.url:
            raise ValueError(
                "Server cannot have both 'command' (stdio) and 'url' (http) — pick one"
            )
        if not self.command and not self.url:
            if self.enabled:
                raise ValueError(
                    "Server must have either 'command' (stdio) or 'url' (http)"
                )
        return self

    @property
    def transport_type(self) -> str:
        if self.command is not None:
            return "stdio"
        if self.url is not None:
            return "http"
        raise ValueError("Server must have either 'command' (stdio) or 'url' (http)")


_SERVER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class GatewayConfig(BaseModel):
    """Top-level gateway configuration."""

    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/mcp"
    name: str = "mcp-gateway"
    version: str = "0.1.0"
    log_level: str = "info"
    max_sessions: int = 100

    mcp_servers: dict[str, UpstreamServerConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_server_names(self) -> GatewayConfig:
        for name in self.mcp_servers:
            if "__" in name:
                raise ValueError(
                    f"Server name '{name}' must not contain '__' "
                    f"(reserved as namespace separator)"
                )
            if not _SERVER_NAME_RE.match(name):
                raise ValueError(
                    f"Server name '{name}' must match [a-zA-Z0-9][a-zA-Z0-9_-]*"
                )
        return self


def load_config(path: str | Path) -> GatewayConfig:
    """Load gateway configuration from a YAML file."""
    path = Path(path)
    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return GatewayConfig(**raw)
