"""MCP server demonstrating resources and prompts via stdio.

Exposes:
  - Resources: a few static knowledge-base articles
  - Resource templates: lookup by topic
  - Prompts: reusable prompt templates with arguments
  - Tools: a simple search tool
"""

import asyncio
from typing import Any

from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp import types
import mcp.server.stdio

server = Server("knowledge")

# ── In-memory knowledge base ────────────────────────────────────────

ARTICLES = {
    "mcp-overview": {
        "title": "What is MCP?",
        "body": (
            "The Model Context Protocol (MCP) is an open protocol that standardizes "
            "how applications provide context to LLMs. It uses JSON-RPC 2.0 over "
            "stdio or HTTP transports."
        ),
    },
    "mcp-tools": {
        "title": "MCP Tools",
        "body": (
            "Tools are functions that an LLM can invoke. Each tool has a name, "
            "description, and a JSON Schema defining its input parameters. Tools "
            "are discovered via tools/list and invoked via tools/call."
        ),
    },
    "mcp-resources": {
        "title": "MCP Resources",
        "body": (
            "Resources provide read-only data to clients. They are identified by "
            "URIs and can contain text or binary content. Clients discover resources "
            "via resources/list and read them via resources/read."
        ),
    },
}


# ── Tools ────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search",
            description="Search the knowledge base for articles matching a query",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    if name == "search":
        query = arguments["query"].lower()
        matches = [
            f"- {a['title']}: {a['body'][:80]}..."
            for a in ARTICLES.values()
            if query in a["title"].lower() or query in a["body"].lower()
        ]
        if not matches:
            return [types.TextContent(type="text", text="No articles found.")]
        return [types.TextContent(type="text", text="\n".join(matches))]
    raise ValueError(f"Unknown tool: {name}")


# ── Resources ────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri=f"knowledge://{slug}",
            name=article["title"],
            description=f"Knowledge base article: {article['title']}",
            mimeType="text/plain",
        )
        for slug, article in ARTICLES.items()
    ]


@server.list_resource_templates()
async def list_resource_templates() -> list[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            uriTemplate="knowledge://{topic}",
            name="Knowledge article by topic",
            description="Look up a knowledge base article by its topic slug",
            mimeType="text/plain",
        ),
    ]


@server.read_resource()
async def read_resource(uri: Any) -> str:
    uri_str = str(uri)
    prefix = "knowledge://"
    if not uri_str.startswith(prefix):
        raise ValueError(f"Unknown resource URI: {uri_str}")
    slug = uri_str[len(prefix):]
    article = ARTICLES.get(slug)
    if not article:
        raise ValueError(f"Article not found: {slug}")
    return f"# {article['title']}\n\n{article['body']}"


# ── Prompts ──────────────────────────────────────────────────────────

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="summarize",
            description="Generate a summary prompt for a given topic",
            arguments=[
                types.PromptArgument(
                    name="topic",
                    description="The topic to summarize",
                    required=True,
                ),
                types.PromptArgument(
                    name="style",
                    description="Summary style: brief, detailed, or eli5",
                    required=False,
                ),
            ],
        ),
        types.Prompt(
            name="explain",
            description="Generate an explanation prompt for a concept",
            arguments=[
                types.PromptArgument(
                    name="concept",
                    description="The concept to explain",
                    required=True,
                ),
            ],
        ),
    ]


@server.get_prompt()
async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    arguments = arguments or {}

    if name == "summarize":
        topic = arguments.get("topic", "unknown")
        style = arguments.get("style", "brief")
        return types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Summarize the following topic in a {style} style: {topic}",
                    ),
                ),
            ],
        )

    if name == "explain":
        concept = arguments.get("concept", "unknown")
        return types.GetPromptResult(
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Explain the concept of '{concept}' clearly and concisely.",
                    ),
                ),
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")


# ── Main ─────────────────────────────────────────────────────────────

async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="knowledge",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
