"""Test MCP Gateway with OpenAI Agent SDK.

Two test modes:
  1. Tool discovery test (no API key needed) — verifies the SDK can
     connect to the gateway, list tools, and call them directly.
  2. Full agent test (requires OPENAI_API_KEY) — runs an agent that
     uses gateway tools to answer a question.

Usage:
  # Start example servers first:
  #   python examples/http_calc_server.py &
  #   mcp-gateway -c config.example.yaml &

  # Run tool discovery test (no API key needed):
  python tests/test_openai_agents.py

  # Run full agent test (needs API key):
  OPENAI_API_KEY=sk-... python tests/test_openai_agents.py --agent
"""

import argparse
import asyncio
import os
import sys

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp


GATEWAY_URL = os.environ.get("MCP_GATEWAY_URL", "http://127.0.0.1:8080/mcp")


async def test_tool_discovery():
    """Verify the SDK can discover and call tools through the gateway."""
    print(f"Connecting to gateway at {GATEWAY_URL}...")

    async with MCPServerStreamableHttp(
        name="MCP Gateway",
        params={"url": GATEWAY_URL},
        cache_tools_list=True,
    ) as server:
        # List tools
        tools = await server.list_tools()
        tool_names = sorted(t.name for t in tools)
        print(f"\nDiscovered {len(tools)} tools:")
        for name in tool_names:
            print(f"  {name}")

        # Verify expected tools are present
        assert any("echo" in n for n in tool_names), "No echo tools found"
        print("\n[OK] Echo tools discovered")

        # Verify filtering works
        assert not any("divide" in n for n in tool_names), "divide should be filtered out"
        print("[OK] Blacklist filtering verified (no divide)")

        # Check that knowledge tools are filtered
        knowledge_tools = [n for n in tool_names if "knowledge" in n]
        assert knowledge_tools == ["knowledge__search"], (
            f"Expected only knowledge__search, got {knowledge_tools}"
        )
        print("[OK] Whitelist filtering verified (knowledge = [search])")

        # Create an agent to verify wiring (no LLM call needed)
        agent = Agent(
            name="Test Agent",
            instructions="You are a test agent.",
            mcp_servers=[server],
        )
        print(f"\n[OK] Agent created with {len(agent.mcp_servers)} MCP server(s)")

        print("\n=== TOOL DISCOVERY TEST PASSED ===")


async def test_agent_run():
    """Run a full agent interaction through the gateway (requires API key)."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("Skipping agent test: OPENAI_API_KEY not set")
        return

    print(f"\nConnecting to gateway at {GATEWAY_URL}...")

    async with MCPServerStreamableHttp(
        name="MCP Gateway",
        params={"url": GATEWAY_URL},
        cache_tools_list=True,
    ) as server:
        agent = Agent(
            name="Gateway Agent",
            instructions=(
                "You have access to tools from multiple MCP servers through "
                "a gateway. Use the appropriate tools to answer questions. "
                "Tools are namespaced as server__tool (e.g. echo__echo, "
                "calc__multiply, knowledge__search)."
            ),
            mcp_servers=[server],
        )

        # Test 1: Math via calc server
        print("\n--- Test: calc__multiply ---")
        result = await Runner.run(agent, "What is 13 times 7? Use the calc multiply tool.")
        print(f"Result: {result.final_output}")

        # Test 2: Echo via echo server
        print("\n--- Test: echo__echo ---")
        result = await Runner.run(agent, 'Echo the message "Hello from OpenAI Agent SDK"')
        print(f"Result: {result.final_output}")

        # Test 3: Knowledge search
        print("\n--- Test: knowledge__search ---")
        result = await Runner.run(agent, "Search the knowledge base for information about tools")
        print(f"Result: {result.final_output}")

        print("\n=== AGENT TEST PASSED ===")


async def main(run_agent: bool = False):
    await test_tool_discovery()
    if run_agent:
        await test_agent_run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agent",
        action="store_true",
        help="Run the full agent test (requires OPENAI_API_KEY)",
    )
    args = parser.parse_args()
    asyncio.run(main(run_agent=args.agent))
