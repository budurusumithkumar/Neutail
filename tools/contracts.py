"""FastMCP tool metadata, schemas, and reusable contract helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from fastmcp.tools import tool
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from tools.permissions import AgentName, allowed_agents


class ToolDescriptor(BaseModel):
    """Serializable registry view of one MCP tool contract."""

    model_config = ConfigDict(extra="forbid")

    name: str
    title: Optional[str] = None
    description: str
    capability: str
    input_schema: dict[str, Any]
    output_schema: Optional[dict[str, Any]] = None
    allowed_agents: list[AgentName] = Field(default_factory=list)
    read_only: bool
    idempotent: bool
    destructive: bool


class ToolAcknowledgement(BaseModel):
    """Structured acknowledgement returned by command-style tool adapters."""

    success: bool
    message: str


def tool_contract(
    *,
    name: str,
    title: str,
    description: str,
    capability: str,
    read_only: bool = True,
    idempotent: bool = True,
    destructive: bool = False,
) -> Callable[[Callable[..., Any]], Any]:
    """Decorate a function with consistent schemas, safety hints, and policy meta."""

    permitted_agents = allowed_agents(name)
    if not permitted_agents:
        raise ValueError(f"Tool '{name}' has no allowed agents")

    domain = name.split("_", 1)[0]
    mode = "read" if read_only else "write"
    return tool(
        name=name,
        title=title,
        description=description,
        tags={domain, capability, mode},
        annotations=ToolAnnotations(
            title=title,
            readOnlyHint=read_only,
            destructiveHint=destructive,
            idempotentHint=idempotent,
            openWorldHint=False,
        ),
        meta={
            "neutail": {
                "capability": capability,
                "allowed_agents": [agent.value for agent in permitted_agents],
                "read_only": read_only,
            }
        },
    )


__all__ = ["ToolAcknowledgement", "ToolDescriptor", "tool_contract"]
