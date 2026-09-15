"""Authenticated session-management routes for the Neu.Tail UI API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Path,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from api.auth import get_authenticated_customer_id
from orchestrator.models import SessionContext as OrchestratorSessionContext
from services.session_context_service import (
    SessionContextService,
    SessionIdentityMismatchError,
)


class SessionAPIModel(BaseModel):
    """Strict request/response behavior for the session API."""

    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(SessionAPIModel):
    """Optional settings used when starting a customer session."""

    channel: Literal["web", "mobile", "demo"] = "web"


class SessionResponse(SessionAPIModel):
    """UI-facing session lifecycle state."""

    session_id: str
    customer_id: str
    status: Literal["ACTIVE", "CLOSED"]
    created_at: datetime
    updated_at: Optional[datetime] = None
    turn_count: int = Field(ge=0)


class SessionContext(SessionAPIModel):
    """Sanitized conversational state safe to expose to the UI."""

    session_id: str
    customer_id: str
    current_intent: Optional[str] = None
    occasion: Optional[str] = None
    category: Optional[str] = None
    selected_sku: Optional[str] = None
    requested_size: Optional[str] = None
    last_agent: Optional[str] = None
    turn_count: int = Field(ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


def get_session_service(request: Request) -> SessionContextService:
    """Return the session store shared with the orchestrator."""

    return request.app.state.orchestrator.session_service


SessionServiceDependency = Annotated[
    SessionContextService,
    Depends(get_session_service),
]
CustomerIdentityDependency = Annotated[
    str,
    Depends(get_authenticated_customer_id),
]
SessionIdPath = Annotated[str, Path(min_length=1, max_length=128)]


def _session_not_found(session_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Session '{session_id}' was not found",
    )


def _get_owned_session(
    service: SessionContextService,
    session_id: str,
    customer_id: str,
) -> OrchestratorSessionContext:
    """Load an owned session without disclosing another customer's IDs."""

    try:
        context = service.get_context(session_id, customer_id)
    except SessionIdentityMismatchError as exc:
        raise _session_not_found(session_id) from exc
    if context is None:
        raise _session_not_found(session_id)
    return context


def _session_response(context: OrchestratorSessionContext) -> SessionResponse:
    return SessionResponse(
        session_id=context.session_id,
        customer_id=context.customer_id,
        status="ACTIVE",
        created_at=context.created_at,
        updated_at=context.updated_at,
        turn_count=context.turn_count,
    )


def _sanitized_context(context: OrchestratorSessionContext) -> SessionContext:
    safe_attributes = {
        key: value
        for key, value in context.attributes.items()
        if key in {"channel"}
    }
    return SessionContext(
        session_id=context.session_id,
        customer_id=context.customer_id,
        current_intent=context.last_intent,
        occasion=context.occasion,
        category=context.category,
        selected_sku=context.selected_sku,
        requested_size=context.requested_size,
        last_agent=context.last_agent,
        turn_count=context.turn_count,
        attributes=safe_attributes,
    )


router = APIRouter(prefix="/api/v1/sessions", tags=["Sessions"])


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    response_description="Session created",
    summary="Start a customer session",
    operation_id="createSession",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"},
    },
)
async def create_session(
    customer_id: CustomerIdentityDependency,
    service: SessionServiceDependency,
    payload: CreateSessionRequest = Body(default_factory=CreateSessionRequest),
) -> SessionResponse:
    context = service.create_context(
        customer_id,
        attributes={"channel": payload.channel},
    )
    return _session_response(context)


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    response_description="Session",
    summary="Get session",
    operation_id="getSession",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Session not found"}},
)
async def get_session(
    session_id: SessionIdPath,
    customer_id: CustomerIdentityDependency,
    service: SessionServiceDependency,
) -> SessionResponse:
    return _session_response(_get_owned_session(service, session_id, customer_id))


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_description="Session closed",
    summary="Close session",
    operation_id="closeSession",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Session not found"}},
)
async def close_session(
    session_id: SessionIdPath,
    customer_id: CustomerIdentityDependency,
    service: SessionServiceDependency,
    request: Request,
) -> Response:
    try:
        cleared = service.clear_context(session_id, customer_id)
    except SessionIdentityMismatchError as exc:
        raise _session_not_found(session_id) from exc
    if not cleared:
        raise _session_not_found(session_id)
    await request.app.state.profile_agent.clear_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{session_id}/context",
    response_model=SessionContext,
    response_description="Session context",
    summary="Get sanitized session context",
    operation_id="getSessionContext",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Session not found"}},
)
async def get_session_context(
    session_id: SessionIdPath,
    customer_id: CustomerIdentityDependency,
    service: SessionServiceDependency,
) -> SessionContext:
    context = _get_owned_session(service, session_id, customer_id)
    return _sanitized_context(context)


__all__ = [
    "CreateSessionRequest",
    "SessionContext",
    "SessionResponse",
    "get_session_service",
    "router",
]
