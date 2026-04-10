# MCP Gateway

Aggregate multiple MCP servers behind a single Streamable HTTP endpoint.

## Overview

MCP Gateway sits between your AI agents and your MCP tool servers. It connects to
multiple upstream MCP servers (via stdio or HTTP), aggregates their tools, resources,
and prompts, and exposes everything through a single Streamable HTTP MCP endpoint.

Any MCP-compatible client can connect — OpenAI Agent SDK, Claude, Hermes, or your
own agent framework.

```
                                             ┌──────────────────┐
                                         ┌──▶│ MCP Server       │
                                         │   │ (filesystem)     │
                                         │   │ [stdio]          │
┌──────────────┐     ┌───────────────┐   │   └──────────────────┘
│  Agent       │     │               │   │
│  (OpenAI SDK,│     │  MCP Gateway  │   │   ┌──────────────────┐
│   Claude,    │────▶│               │───┼──▶│ MCP Server       │
│   Hermes,    │     │  Aggregation  │   │   │ (github)         │
│   Custom)    │     │  Namespacing  │   │   │ [http]           │
│              │     │  Filtering    │   │   └──────────────────┘
└──────────────┘     └───────────────┘   │
                                         │   ┌──────────────────┐
  Streamable HTTP          /mcp          └──▶│ MCP Server       │
◀──────────────▶       ◀────────────▶        │ (git)            │
    downstream            gateway            │ [stdio]          │
                                             └──────────────────┘
```

## Quick Start

```bash
# Install
uv pip install -e .

# Configure
cp config.example.yaml config.yaml
# Edit config.yaml with your MCP servers

# Run
mcp-gateway -c config.yaml
```

The gateway starts on `http://127.0.0.1:8080/mcp` by default.

## Connecting from OpenAI Agent SDK

```python
from agents.mcp import MCPServerStreamableHttp
from agents import Agent

async with MCPServerStreamableHttp(
    name="Gateway",
    params={"url": "http://localhost:8080/mcp"},
) as server:
    agent = Agent(name="Assistant", mcp_servers=[server])
    # All upstream tools are available as: {server_name}__{tool_name}
```

## Configuration

See `config.example.yaml` for the full reference. Key features:

### Upstream Transports

- **Stdio**: Spawn a local process (`command` + `args` + `env`)
- **HTTP**: Connect to a remote server (`url` + `headers`)

### Tool Filtering

```yaml
tools:
  include: [read_file, list_directory]   # Whitelist (recommended)
  exclude: [delete_repository]           # Blacklist
  resources: true                        # Expose resource utilities
  prompts: false                         # Hide prompt utilities
```

### Tool Namespacing

Tools are namespaced as `{server_name}__{tool_name}` to prevent collisions.
Example: `filesystem__read_file`, `github__create_issue`.

### Endpoints

| Endpoint   | Method       | Description                     |
|------------|--------------|---------------------------------|
| `/mcp`     | POST/GET/DEL | MCP Streamable HTTP endpoint    |
| `/health`  | GET          | Status (`ok`/`degraded`), sessions, tool counts |
| `/reload`  | POST         | Refresh tools and reconnect dead upstreams |

## Supported Patterns (Hermes-inspired)

- [x] Stdio upstream transport
- [x] HTTP upstream transport (Streamable HTTP)
- [x] Tool namespacing (`{server}__{tool}`)
- [x] Tool filtering (include/exclude lists)
- [x] Resource aggregation with per-server toggle
- [x] Prompt aggregation with per-server toggle
- [x] Dynamic tool discovery (`notifications/tools/list_changed`)
- [x] Per-server enable/disable
- [x] Connection/call timeouts
- [x] Session limits (`max_sessions`)
- [x] Health check with degraded status
- [x] Hot reload with upstream reconnection (`/reload`)
- [x] Config validation (transport conflicts, namespace collisions)
