"""FastAPI boundary for Neu.Tail orchestration and customer profiling."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated, AsyncIterator, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.profiling import (
    CustomerNotFoundError,
    ProfileAgent,
    ProfileAgentError,
    ProfileAgentRequest,
)
from api.auth import get_authenticated_customer_id, router as auth_router
from api.customers import router as customers_router
from api.events import demo_router, internal_router
from api.recommendations import router as recommendations_router
from api.sessions import router as sessions_router
from api.upsell import UpsellAwareChatResponse, router as upsell_router
from models.dto import CustomerContext
from llm_gateway import LLMGateway
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
from orchestrator.purchase_event_graph import PurchaseEventGraph
from services.session_context_service import (
    SessionContextService,
    SessionIdentityMismatchError,
)
from services.context_bus import (
    ContextBusDispatcher,
    HighProductEngagementSubscriber,
    ProfileContextSubscriber,
)
from tools.contracts import ToolDescriptor
from tools.permissions import AgentName
from tools.registry import TOOL_REGISTRY


CORS_ORIGINS_ENV = "NEUTAIL_CORS_ORIGINS"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
)


def resolve_cors_origins() -> list[str]:
    """Return unique configured UI origins without trailing slashes."""

    configured = os.getenv(CORS_ORIGINS_ENV)
    candidates = (
        configured.split(",")
        if configured is not None
        else DEFAULT_CORS_ORIGINS
    )
    return list(
        dict.fromkeys(
            origin.strip().rstrip("/")
            for origin in candidates
            if origin.strip()
        )
    )


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
    llm_gateway = LLMGateway()
    agent_registry = AgentRegistry(profile_agent, llm_gateway=llm_gateway)
    session_service = SessionContextService()
    app.state.profile_agent = profile_agent
    app.state.orchestrator = NeuTailOrchestrator(
        agent_registry=agent_registry,
        llm_gateway=llm_gateway,
        session_service=session_service,
    )
    context_bus = ContextBusDispatcher()
    profile_subscriber = ProfileContextSubscriber(
        profile_agent=profile_agent,
        session_service=session_service,
    )
    context_bus.subscribe(
        "CUSTOMER_SEGMENT_CHANGED",
        profile_subscriber.name,
        profile_subscriber,
    )
    context_bus.subscribe(
        "CUSTOMER_PROFILE_UPDATED",
        profile_subscriber.name,
        profile_subscriber,
    )
    upsell_subscriber = HighProductEngagementSubscriber(
        orchestrator=app.state.orchestrator
    )
    context_bus.subscribe(
        "HIGH_PRODUCT_ENGAGEMENT",
        upsell_subscriber.name,
        upsell_subscriber,
    )
    app.state.context_bus = context_bus
    app.state.purchase_event_graph = PurchaseEventGraph(
        profile_agent=profile_agent,
        session_service=session_service,
        context_bus=context_bus,
    )
    stop_context_bus = asyncio.Event()
    context_bus_task = asyncio.create_task(
        context_bus.run(stop_context_bus),
        name="neutail-context-bus",
    )
    try:
        yield
    finally:
        stop_context_bus.set()
        await context_bus_task


app = FastAPI(
    title="Neu.Tail Agent API",
    description=(
        "FastAPI boundary for orchestrated chat and deterministic customer "
        "context construction through an agent-scoped FastMCP registry."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
    max_age=600,
)
app.include_router(auth_router)
app.include_router(customers_router)
app.include_router(demo_router)
app.include_router(internal_router)
app.include_router(recommendations_router)
app.include_router(sessions_router)
app.include_router(upsell_router)


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
    response_model=UpsellAwareChatResponse,
    summary="Handle one orchestrated customer turn",
    tags=["chat"],
    operation_id="chatWithUpsellResult",
)
async def chat(
    payload: ChatRequest,
    request: Request,
    orchestrator: OrchestratorDependency,
    customer_id: Annotated[str, Depends(get_authenticated_customer_id)],
) -> UpsellAwareChatResponse:
    return await orchestrator.handle(
        OrchestratorRequest(
            customer_id=customer_id,
            session_id=payload.session_id,
            message=payload.message,
            trace_id=request.state.request_id,
            selected_sku=payload.selected_sku,
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


__all__ = [
    "CORS_ORIGINS_ENV",
    "DEFAULT_CORS_ORIGINS",
    "app",
    "resolve_cors_origins",
]
