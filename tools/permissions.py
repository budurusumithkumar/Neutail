"""Agent-to-tool permission policy for the Neu.Tail MCP layer."""

from __future__ import annotations

from enum import Enum
from typing import Union


class AgentName(str, Enum):
    """Agents allowed to receive scoped Neu.Tail tool servers."""

    PROFILING = "profiling_agent"
    DISCOVERY = "discovery_agent"
    FIT = "fit_agent"
    UPSELL = "upsell_agent"


CUSTOMER_PROFILE_TOOLS = frozenset(
    {
        "customer_exists",
        "customer_get_master",
        "customer_get_preferences",
        "customer_get_profile_facts",
    }
)
ORDER_TOOLS = frozenset(
    {
        "customer_get_orders",
        "customer_get_order_items",
        "customer_get_purchase_summary",
        "customer_get_recent_sizes",
    }
)
RETURN_TOOLS = frozenset(
    {
        "customer_get_returns",
        "customer_get_return_summary",
        "customer_get_product_returns",
        "customer_get_size_related_returns",
    }
)
LOYALTY_TOOLS = frozenset(
    {
        "customer_get_loyalty_profile",
        "customer_get_loyalty_transactions",
        "customer_get_loyalty_summary",
    }
)
PROFILE_AGENT_TOOLS = frozenset(
    {
        "get_customer_profile",
        "get_purchase_history",
        "get_return_history",
        "get_loyalty_profile",
        "get_engagement_summary",
    }
)
PRODUCT_TOOLS = frozenset(
    {"product_search", "product_get", "product_get_many", "product_is_active"}
)
INVENTORY_TOOLS = frozenset(
    {
        "inventory_get",
        "inventory_get_by_location",
        "inventory_is_available",
        "inventory_get_available_skus",
    }
)
FIT_TOOLS = frozenset(
    {
        "fit_get_profile",
        "fit_build_evidence",
        "fit_get_brand_adjustment",
        "fit_calculate_risk",
    }
)
UPSELL_TOOLS = frozenset(
    {
        "upsell_get_recent_behavior",
        "upsell_get_behavior_summary",
        "upsell_get_service_engagement",
        "upsell_record_service_event",
        "upsell_evaluate",
        "upsell_is_suppressed",
        "upsell_get_candidate_services",
        "upsell_record_decision",
    }
)


AGENT_TOOL_ALLOWLIST: dict[AgentName, frozenset[str]] = {
    AgentName.PROFILING: PROFILE_AGENT_TOOLS,
    AgentName.DISCOVERY: (
        frozenset(
            {
                "customer_exists",
                "customer_get_preferences",
                "customer_get_profile_facts",
            }
        )
        | PRODUCT_TOOLS
        | INVENTORY_TOOLS
    ),
    AgentName.FIT: (
        frozenset({"customer_exists", "customer_get_preferences", "product_get"})
        | ORDER_TOOLS
        | RETURN_TOOLS
        | INVENTORY_TOOLS
        | FIT_TOOLS
    ),
    AgentName.UPSELL: (
        CUSTOMER_PROFILE_TOOLS
        | LOYALTY_TOOLS
        | frozenset({"customer_get_return_summary", "product_get"})
        | UPSELL_TOOLS
    ),
}


class ToolPermissionError(PermissionError):
    """Raised when an agent attempts to access a disallowed tool."""

    def __init__(self, agent: AgentName, tool_name: str) -> None:
        self.agent = agent
        self.tool_name = tool_name
        super().__init__(f"Agent '{agent.value}' cannot access tool '{tool_name}'")


def normalize_agent(agent: Union[AgentName, str]) -> AgentName:
    """Validate and normalize an agent identifier."""

    if isinstance(agent, AgentName):
        return agent
    try:
        return AgentName(agent)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unknown agent: {agent!r}") from exc


def can_call(agent: Union[AgentName, str], tool_name: str) -> bool:
    """Return whether an agent may discover and invoke a tool."""

    normalized_agent = normalize_agent(agent)
    return tool_name in AGENT_TOOL_ALLOWLIST[normalized_agent]


def require_permission(agent: Union[AgentName, str], tool_name: str) -> None:
    """Raise when an agent is not allowed to access a tool."""

    normalized_agent = normalize_agent(agent)
    if not can_call(normalized_agent, tool_name):
        raise ToolPermissionError(normalized_agent, tool_name)


def allowed_agents(tool_name: str) -> tuple[AgentName, ...]:
    """Return agents allowed to access a tool in stable enum order."""

    return tuple(
        agent
        for agent in AgentName
        if tool_name in AGENT_TOOL_ALLOWLIST[agent]
    )


def allowed_tools(agent: Union[AgentName, str]) -> frozenset[str]:
    """Return the immutable allowlist for one agent."""

    return AGENT_TOOL_ALLOWLIST[normalize_agent(agent)]


ALL_PERMISSIONED_TOOLS = frozenset().union(*AGENT_TOOL_ALLOWLIST.values())


__all__ = [
    "AGENT_TOOL_ALLOWLIST",
    "ALL_PERMISSIONED_TOOLS",
    "AgentName",
    "PROFILE_AGENT_TOOLS",
    "ToolPermissionError",
    "allowed_agents",
    "allowed_tools",
    "can_call",
    "normalize_agent",
    "require_permission",
]
