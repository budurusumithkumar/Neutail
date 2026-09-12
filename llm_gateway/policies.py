"""Consistent model behavior and resilience policy."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GatewayPolicy(BaseModel):
    """Per-use-case limits controlled by the gateway, never by agents."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(default=300, ge=1, le=4096)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    allow_fallback: bool = True
    structured_output_retries: int = Field(default=1, ge=0, le=2)
    log_request_body: bool = False
    log_response_body: bool = False


__all__ = ["GatewayPolicy"]

