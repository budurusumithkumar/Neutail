"""Central FastMCP tool registry and agent-scoped server construction."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any, Optional, Union

from fastmcp import FastMCP

from tools.contracts import ToolDescriptor
from tools.customer_tools import CUSTOMER_TOOLS
from tools.fit_tools import FIT_TOOLS
from tools.permissions import (
    ALL_PERMISSIONED_TOOLS,
    AgentName,
    allowed_agents,
    allowed_tools,
    normalize_agent,
    require_permission,
)
from tools.product_tools import PRODUCT_TOOLS
from tools.upsell_tools import UPSELL_TOOLS


class ToolRegistry:
    """Own tool definitions, descriptions, capabilities, and scoped servers."""

    def __init__(self, tools: Iterable[Any]) -> None:
        self._tools: dict[str, Any] = {}
        materializer = FastMCP(name="Neu.Tail Tool Contract Builder")
        for candidate in tools:
            registered_tool = materializer.add_tool(candidate)
            name = registered_tool.name
            if name in self._tools:
                raise ValueError(f"Duplicate tool name: {name}")
            self._tools[name] = registered_tool

        registered_names = frozenset(self._tools)
        if registered_names != ALL_PERMISSIONED_TOOLS:
            missing_contracts = registered_names - ALL_PERMISSIONED_TOOLS
            missing_adapters = ALL_PERMISSIONED_TOOLS - registered_names
            raise ValueError(
                "Tool registry and permission policy differ: "
                f"missing policy={sorted(missing_contracts)}, "
                f"missing adapters={sorted(missing_adapters)}"
            )

    def list_tools(
        self, agent: Optional[Union[AgentName, str]] = None
    ) -> list[ToolDescriptor]:
        """List visible tool contracts in stable name order."""

        visible_names = self._visible_names(agent)
        return [self._descriptor(self._tools[name]) for name in visible_names]

    def describe_tool(
        self,
        tool_name: str,
        agent: Optional[Union[AgentName, str]] = None,
    ) -> ToolDescriptor:
        """Return one tool's description and input/output schemas."""

        if tool_name not in self._tools:
            raise KeyError(f"Unknown tool: {tool_name}")
        if agent is not None:
            require_permission(agent, tool_name)
        return self._descriptor(self._tools[tool_name])

    def capabilities(
        self, agent: Optional[Union[AgentName, str]] = None
    ) -> dict[str, list[str]]:
        """Group visible tool names by advertised capability."""

        grouped: dict[str, list[str]] = defaultdict(list)
        for descriptor in self.list_tools(agent):
            grouped[descriptor.capability].append(descriptor.name)
        return dict(sorted(grouped.items()))

    def get_tool(
        self, tool_name: str, agent: Union[AgentName, str]
    ) -> Any:
        """Resolve a tool only after enforcing the agent permission policy."""

        if tool_name not in self._tools:
            raise KeyError(f"Unknown tool: {tool_name}")
        require_permission(agent, tool_name)
        return self._tools[tool_name]

    def build_server(
        self, agent: Optional[Union[AgentName, str]] = None
    ) -> FastMCP:
        """Build a full trusted server or an enforced agent-scoped server."""

        normalized_agent = normalize_agent(agent) if agent is not None else None
        server_name = (
            f"Neu.Tail Tools — {normalized_agent.value}"
            if normalized_agent is not None
            else "Neu.Tail Tool Registry"
        )
        instructions = (
            "Agent-scoped Neu.Tail retail tools. Only tools allowed by the "
            "static permission policy are registered on this server."
            if normalized_agent is not None
            else "Trusted full Neu.Tail tool registry for orchestration and inspection."
        )
        server = FastMCP(
            name=server_name,
            instructions=instructions,
            version="0.1.0",
        )
        for name in self._visible_names(normalized_agent):
            server.add_tool(self._tools[name])
        return server

    def _visible_names(
        self, agent: Optional[Union[AgentName, str]]
    ) -> list[str]:
        if agent is None:
            return sorted(self._tools)
        return sorted(allowed_tools(agent))

    @staticmethod
    def _descriptor(registered_tool: Any) -> ToolDescriptor:
        metadata = (registered_tool.meta or {}).get("neutail", {})
        annotations = registered_tool.annotations
        return ToolDescriptor(
            name=registered_tool.name,
            title=registered_tool.title,
            description=registered_tool.description or "",
            capability=metadata["capability"],
            input_schema=registered_tool.parameters,
            output_schema=registered_tool.output_schema,
            allowed_agents=list(allowed_agents(registered_tool.name)),
            read_only=bool(annotations and annotations.read_only_hint),
            idempotent=bool(annotations and annotations.idempotent_hint),
            destructive=bool(annotations and annotations.destructive_hint),
        )


ALL_TOOLS = CUSTOMER_TOOLS + PRODUCT_TOOLS + FIT_TOOLS + UPSELL_TOOLS
TOOL_REGISTRY = ToolRegistry(ALL_TOOLS)


def create_agent_server(agent: Union[AgentName, str]) -> FastMCP:
    """Create a FastMCP server that exposes only one agent's allowed tools."""

    return TOOL_REGISTRY.build_server(agent)


__all__ = [
    "ALL_TOOLS",
    "TOOL_REGISTRY",
    "ToolRegistry",
    "create_agent_server",
]
