"""Configuration models for MCP Gateway."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ToolFilterConfig(BaseModel):
    """Per-server tool filtering configuration."""

    include: list[str] | None = None
    exclude: list[str] | None = None
    resources: bool = True
    prompts: bool = True


class SamplingConfig(BaseModel):
    """Per-server MCP sampling configuration."""

    enabled: bool = True
    model: str | None = None
    max_tokens_cap: int = 4096
    timeout: float = 30.0
    max_rpm: int = 10
    max_tool_rounds: int = 5
    allowed_models: list[str] = Field(default_factory=list)


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

    # Sampling
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)

    @property
    def transport_type(self) -> str:
        if self.command is not None:
            return "stdio"
        if self.url is not None:
            return "http"
        raise ValueError("Server must have either 'command' (stdio) or 'url' (http)")


class GatewayConfig(BaseModel):
    """Top-level gateway configuration."""

    host: str = "127.0.0.1"
    port: int = 8080
    path: str = "/mcp"
    name: str = "mcp-gateway"
    version: str = "0.1.0"
    log_level: str = "info"

    mcp_servers: dict[str, UpstreamServerConfig] = Field(default_factory=dict)


def load_config(path: str | Path) -> GatewayConfig:
    """Load gateway configuration from a YAML file."""
    path = Path(path)
    with path.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return GatewayConfig(**raw)
