"""Runnable trusted Neu.Tail FastMCP server entrypoint."""

from __future__ import annotations

from tools.registry import TOOL_REGISTRY, create_agent_server


# The full server is intended for the trusted orchestrator. Individual agents
# should receive a server built with ``create_agent_server(agent_name)``.
mcp = TOOL_REGISTRY.build_server()


if __name__ == "__main__":
    mcp.run()


__all__ = ["create_agent_server", "mcp"]
