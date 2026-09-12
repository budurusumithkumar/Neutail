"""FastAPI boundary for Neu.Tail orchestration and customer profiling."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated, AsyncIterator, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.profiling import (
    CustomerNotFoundError,
    ProfileAgent,
    ProfileAgentError,
    ProfileAgentRequest,
)
from api.auth import get_authenticated_customer_id
from models.dto import CustomerContext
from orchestrator import (
    AgentDescriptor,
    AgentRegistry,
    ChatRequest,
    NeuTailOrchestrator,
    OrchestratorCustomerNotFoundError,
    OrchestratorDependencyError,
    OrchestratorRequest,
    OrchestratorResponse,
)
from services.session_context_service import (
    SessionContextService,
    SessionIdentityMismatchError,
)
from tools.contracts import ToolDescriptor
from tools.permissions import AgentName
from tools.registry import TOOL_REGISTRY


class HealthResponse(BaseModel):
    status: str
    service: str
    profiling_agent: str
    orchestrator: str
    langsmith_tracing: bool
    langsmith_project: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    profile_agent = ProfileAgent()
    agent_registry = AgentRegistry(profile_agent)
    app.state.profile_agent = profile_agent
    app.state.orchestrator = NeuTailOrchestrator(
        agent_registry=agent_registry,
        session_service=SessionContextService(),
    )
    yield


app = FastAPI(
    title="Neu.Tail Agent API",
    description=(
        "FastAPI boundary for orchestrated chat and deterministic customer "
        "context construction through an agent-scoped FastMCP registry."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a correlation ID and server timing to every API response."""

    request_id = request.headers.get("x-request-id") or uuid4().hex
    request.state.request_id = request_id
    started = perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    response.headers["x-process-time-ms"] = f"{(perf_counter() - started) * 1000:.2f}"
    return response


@app.exception_handler(CustomerNotFoundError)
async def customer_not_found_handler(
    _request: Request, exc: CustomerNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error_code": "CUSTOMER_NOT_FOUND",
            "detail": str(exc),
            "customer_id": exc.customer_id,
        },
    )


@app.exception_handler(ProfileAgentError)
async def profile_agent_error_handler(
    _request: Request, exc: ProfileAgentError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error_code": "PROFILE_AGENT_UNAVAILABLE", "detail": str(exc)},
    )


@app.exception_handler(OrchestratorCustomerNotFoundError)
async def orchestrator_customer_not_found_handler(
    _request: Request, exc: OrchestratorCustomerNotFoundError
) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "error_code": "CUSTOMER_NOT_FOUND",
            "detail": str(exc),
            "customer_id": exc.customer_id,
        },
    )


@app.exception_handler(SessionIdentityMismatchError)
async def session_identity_mismatch_handler(
    _request: Request, exc: SessionIdentityMismatchError
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error_code": "SESSION_IDENTITY_MISMATCH",
            "detail": str(exc),
            "session_id": exc.session_id,
        },
    )


@app.exception_handler(OrchestratorDependencyError)
async def orchestrator_dependency_error_handler(
    _request: Request, exc: OrchestratorDependencyError
) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error_code": "ORCHESTRATOR_DEPENDENCY_UNAVAILABLE",
            "detail": str(exc),
        },
    )


def get_profile_agent(request: Request) -> ProfileAgent:
    return request.app.state.profile_agent


ProfileAgentDependency = Annotated[ProfileAgent, Depends(get_profile_agent)]


def get_orchestrator(request: Request) -> NeuTailOrchestrator:
    return request.app.state.orchestrator


OrchestratorDependency = Annotated[
    NeuTailOrchestrator, Depends(get_orchestrator)
]


@app.get("/health", response_model=HealthResponse, tags=["operations"])
async def health() -> HealthResponse:
    tracing = os.getenv("LANGSMITH_TRACING", "false").casefold() == "true"
    return HealthResponse(
        status="ok",
        service="neutail-agent-api",
        profiling_agent="ready",
        orchestrator="ready",
        langsmith_tracing=tracing,
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "neutail-mission4"),
    )


@app.post(
    "/api/v1/profile",
    response_model=CustomerContext,
    summary="Build shared customer context",
    tags=["profiling"],
)
async def profile_customer(
    payload: ProfileAgentRequest,
    agent: ProfileAgentDependency,
) -> CustomerContext:
    return await agent.execute(payload)


@app.get(
    "/api/v1/customers/{customer_id}/profile",
    response_model=CustomerContext,
    summary="Get or refresh shared customer context",
    tags=["profiling"],
)
async def get_customer_profile(
    customer_id: str,
    request: Request,
    agent: ProfileAgentDependency,
    session_id: Annotated[str, Query(min_length=1)],
    trace_id: Annotated[Optional[str], Query(min_length=1)] = None,
    refresh: bool = False,
) -> CustomerContext:
    return await agent.execute(
        ProfileAgentRequest(
            customer_id=customer_id,
            session_id=session_id,
            trace_id=trace_id or request.state.request_id,
            refresh=refresh,
        )
    )


@app.get(
    "/api/v1/agents/profiling/tools",
    response_model=list[ToolDescriptor],
    summary="List the Profiling Agent's permitted MCP tools",
    tags=["profiling"],
)
async def list_profile_tools() -> list[ToolDescriptor]:
    return TOOL_REGISTRY.list_tools(AgentName.PROFILING)


@app.post(
    "/api/v1/chat",
    response_model=OrchestratorResponse,
    summary="Handle one orchestrated customer turn",
    tags=["chat"],
)
async def chat(
    payload: ChatRequest,
    request: Request,
    orchestrator: OrchestratorDependency,
    customer_id: Annotated[str, Depends(get_authenticated_customer_id)],
) -> OrchestratorResponse:
    return await orchestrator.handle(
        OrchestratorRequest(
            customer_id=customer_id,
            session_id=payload.session_id,
            message=payload.message,
            trace_id=request.state.request_id,
        )
    )


@app.get(
    "/api/v1/agents",
    response_model=list[AgentDescriptor],
    summary="List orchestrator-visible agents and implementation status",
    tags=["agents"],
)
async def list_agents(
    orchestrator: OrchestratorDependency,
) -> list[AgentDescriptor]:
    return orchestrator.agent_registry.list_agents()


__all__ = ["app"]
