"""Authenticated UI routes for engagement-driven governed upsell flows."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agents.upsell import FastMCPUpsellToolClient
from api.auth import ErrorResponse, get_authenticated_customer_id
from models.upsell import (
    ServiceOffer,
    UpsellEventInput,
    UpsellResult as DomainUpsellResult,
    UpsellTrigger,
)
from orchestrator import NeuTailOrchestrator, OrchestratorResponse
from services._date_utils import utc_now
from services.context_bus import ContextBusDispatcher
from services.engagement_event_service import (
    EngagementEventService,
    EngagementIdempotencyConflictError,
    EngagementProductUnavailableError,
)
from services.session_context_service import SessionIdentityMismatchError
from services.upsell_decision_service import (
    IdempotencyConflictError,
    UpsellDecisionConflictError,
    UpsellDecisionNotFoundError,
    UpsellDecisionService,
)
from tools.runtime import get_runtime


class UpsellAPIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngagementEventType(str, Enum):
    PRODUCT_VIEWED = "PRODUCT_VIEWED"


class UpsellDecisionEventType(str, Enum):
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_DECLINED = "OFFER_DECLINED"
    OFFER_DISMISSED = "OFFER_DISMISSED"


class UpsellDecisionState(str, Enum):
    INTEREST_RECORDED = "INTEREST_RECORDED"
    DECLINE_RECORDED = "DECLINE_RECORDED"
    DISMISSAL_RECORDED = "DISMISSAL_RECORDED"


class EngagementEventInput(UpsellAPIModel):
    session_id: str = Field(min_length=1, max_length=128)
    event_type: EngagementEventType
    sku: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpsellResult(UpsellAPIModel):
    status: Literal["OFFER_AVAILABLE", "NO_OFFER", "FAILED"]
    should_offer: bool
    offer: Optional[ServiceOffer]
    opportunity_score: Optional[float] = Field(ge=0, le=1)
    opportunity_band: Optional[Literal["LOW", "MEDIUM", "HIGH"]]
    eligibility_reasons: list[str]
    suppression_reasons: list[str]
    message: Optional[str]
    requires_customer_consent: Literal[True]
    decision_id: str = Field(min_length=1)
    trigger: UpsellTrigger
    llm_invoked: bool


class EngagementEventResponse(UpsellAPIModel):
    event_id: str
    recorded: bool
    engagement_count: int = Field(ge=0)
    trigger: Optional[UpsellTrigger]
    trace_id: str
    upsell_result: Optional[UpsellResult]


class PendingUpsellDecision(UpsellAPIModel):
    decision_id: str
    session_id: str
    trace_id: Optional[str]
    created_at: datetime
    upsell_result: UpsellResult


class UpsellAwareChatResponse(OrchestratorResponse):
    """Existing orchestrator response with a typed optional Upsell result."""

    upsell_result: Optional[UpsellResult] = None


class UpsellDecisionEventInput(UpsellAPIModel):
    session_id: str = Field(min_length=1, max_length=128)
    event_type: UpsellDecisionEventType
    idempotency_key: str = Field(min_length=1, max_length=200)


class UpsellDecisionEventResponse(UpsellAPIModel):
    recorded: bool
    decision_id: str
    event_type: UpsellDecisionEventType
    state: UpsellDecisionState
    message: str


def _orchestrator(request: Request) -> NeuTailOrchestrator:
    return request.app.state.orchestrator


def _decision_service(request: Request) -> UpsellDecisionService:
    return request.app.state.orchestrator.upsell_decision_service


def _context_bus(request: Request) -> ContextBusDispatcher:
    return request.app.state.context_bus


CustomerIdentity = Annotated[str, Depends(get_authenticated_customer_id)]
OrchestratorDependency = Annotated[NeuTailOrchestrator, Depends(_orchestrator)]
DecisionServiceDependency = Annotated[
    UpsellDecisionService, Depends(_decision_service)
]
ContextBusDependency = Annotated[
    ContextBusDispatcher, Depends(_context_bus)
]
DecisionIdPath = Annotated[str, Path(min_length=1, max_length=128)]


def _error(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    message: str,
    details: Optional[dict[str, Any]] = None,
) -> JSONResponse:
    body = ErrorResponse(
        error_code=error_code,
        message=message,
        trace_id=getattr(request.state, "request_id", None),
        details=details,
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _api_result(result: DomainUpsellResult) -> UpsellResult:
    if result.trigger is None:
        raise ValueError("A UI upsell result requires its originating trigger")
    return UpsellResult.model_validate(result.model_dump(mode="json"))


router = APIRouter(tags=["Upsell"])


@router.post(
    "/api/v1/engagement/events",
    response_model=EngagementEventResponse,
    response_description="Event recorded",
    operation_id="recordEngagementEvent",
    summary="Record product engagement and evaluate an upsell trigger",
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
async def record_engagement_event(
    payload: EngagementEventInput,
    request: Request,
    customer_id: CustomerIdentity,
    orchestrator: OrchestratorDependency,
    context_bus: ContextBusDependency,
) -> EngagementEventResponse | JSONResponse:
    fingerprint = json.dumps(
        {
            "customer_id": customer_id,
            **payload.model_dump(mode="json", exclude={"idempotency_key"}),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        with get_runtime().session() as database_session:
            receipt = EngagementEventService(database_session).find_receipt(
                customer_id=customer_id,
                idempotency_key=payload.idempotency_key,
                fingerprint=fingerprint,
            )
    except EngagementIdempotencyConflictError as exc:
        return _error(
            request,
            status_code=status.HTTP_409_CONFLICT,
            error_code="IDEMPOTENCY_CONFLICT",
            message=str(exc),
        )
    if receipt is not None and receipt.completed_response is not None:
        return EngagementEventResponse.model_validate(
            receipt.completed_response
        )

    if receipt is None:
        try:
            session = orchestrator.session_service.get_context(
                payload.session_id, customer_id
            )
        except SessionIdentityMismatchError:
            session = None
        if session is None:
            return _error(
                request,
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="INVALID_SESSION",
                message=(
                    "The session does not exist for the authenticated customer"
                ),
            )

    try:
        if receipt is None:
            with get_runtime().session(write=True) as database_session:
                recorded = EngagementEventService(
                    database_session
                ).record_product_view(
                    customer_id=customer_id,
                    session_id=payload.session_id,
                    sku=payload.sku,
                    idempotency_key=payload.idempotency_key,
                    fingerprint=fingerprint,
                    trace_id=request.state.request_id,
                    metadata=payload.metadata,
                )
        else:
            recorded = receipt

        upsell_result: Optional[UpsellResult] = None
        if recorded.outbox_id is not None:
            deliveries = await context_bus.dispatch([recorded.outbox_id])
            subscriber_result = deliveries[recorded.outbox_id][
                "upsell_high_engagement"
            ]["upsell_result"]
            upsell_result = _api_result(
                DomainUpsellResult.model_validate(subscriber_result)
            )

        response = EngagementEventResponse(
            event_id=recorded.event_id,
            recorded=True,
            engagement_count=recorded.engagement_count,
            trigger=recorded.trigger,
            trace_id=request.state.request_id,
            upsell_result=upsell_result,
        )
        with get_runtime().session(write=True) as database_session:
            EngagementEventService(database_session).complete(
                recorded.receipt_id,
                response.model_dump(mode="json"),
            )
        return response
    except EngagementIdempotencyConflictError as exc:
        return _error(
            request,
            status_code=status.HTTP_409_CONFLICT,
            error_code="IDEMPOTENCY_CONFLICT",
            message=str(exc),
        )
    except EngagementProductUnavailableError:
        return _error(
            request,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="PRODUCT_NOT_AVAILABLE",
            message="The product cannot be used for engagement",
            details={"sku": payload.sku},
        )
    except Exception:
        return _error(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="UPSELL_EVALUATION_FAILED",
            message="The engagement could not be evaluated safely",
        )


@router.get(
    "/api/v1/upsell/decisions/pending",
    response_model=list[PendingUpsellDecision],
    operation_id="listPendingUpsellDecisions",
    summary="List durable actionable offers for the authenticated customer",
)
async def list_pending_upsell_decisions(
    customer_id: CustomerIdentity,
    decisions: DecisionServiceDependency,
) -> list[PendingUpsellDecision]:
    return [
        PendingUpsellDecision(
            decision_id=record.decision_id,
            session_id=record.session_id,
            trace_id=record.trace_id,
            created_at=record.created_at,
            upsell_result=UpsellResult.model_validate(record.result),
        )
        for record in decisions.list_pending(customer_id)
    ]


_DECISION_STATE = {
    UpsellDecisionEventType.OFFER_ACCEPTED: (
        UpsellDecisionState.INTEREST_RECORDED,
        "Your interest has been recorded.",
    ),
    UpsellDecisionEventType.OFFER_DECLINED: (
        UpsellDecisionState.DECLINE_RECORDED,
        "Your preference has been recorded.",
    ),
    UpsellDecisionEventType.OFFER_DISMISSED: (
        UpsellDecisionState.DISMISSAL_RECORDED,
        "The offer has been dismissed.",
    ),
}


@router.post(
    "/api/v1/upsell/decisions/{decision_id}/events",
    response_model=UpsellDecisionEventResponse,
    response_description="Customer response recorded",
    operation_id="recordUpsellDecisionEvent",
    summary="Record an explicit customer response to an upsell offer",
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
async def record_upsell_decision_event(
    decision_id: DecisionIdPath,
    payload: UpsellDecisionEventInput,
    request: Request,
    customer_id: CustomerIdentity,
    decisions: DecisionServiceDependency,
) -> UpsellDecisionEventResponse | JSONResponse:
    try:
        record, replay = decisions.claim_decision_event(
            decision_id=decision_id,
            customer_id=customer_id,
            session_id=payload.session_id,
            event_type=payload.event_type.value,
            idempotency_key=payload.idempotency_key,
        )
    except UpsellDecisionNotFoundError:
        return _error(
            request,
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="UPSELL_DECISION_NOT_FOUND",
            message="The upsell decision was not found",
        )
    except (IdempotencyConflictError, UpsellDecisionConflictError) as exc:
        return _error(
            request,
            status_code=status.HTTP_409_CONFLICT,
            error_code="UPSELL_DECISION_CONFLICT",
            message=str(exc),
        )
    if replay is not None:
        return UpsellDecisionEventResponse.model_validate(replay)

    try:
        event = UpsellEventInput(
            customer_id=customer_id,
            session_id=payload.session_id,
            offer_type=record.offer_type,
            event_type=payload.event_type.value,
            trigger_type=record.trigger_type,
            timestamp=utc_now(),
        )
        await FastMCPUpsellToolClient().call_tool(
            "record_upsell_event",
            {"event": event.model_dump(mode="json")},
        )
        decision_state, message = _DECISION_STATE[payload.event_type]
        response = UpsellDecisionEventResponse(
            recorded=True,
            decision_id=decision_id,
            event_type=payload.event_type,
            state=decision_state,
            message=message,
        )
        decisions.complete_decision_event(
            decision_id=decision_id,
            idempotency_key=payload.idempotency_key,
            event_type=payload.event_type.value,
            response=response.model_dump(mode="json"),
        )
        return response
    except Exception:
        decisions.abort_decision_event(payload.idempotency_key)
        return _error(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="UPSELL_EVENT_RECORDING_FAILED",
            message="The customer response could not be recorded",
        )


__all__ = [
    "EngagementEventInput",
    "EngagementEventResponse",
    "EngagementEventType",
    "list_pending_upsell_decisions",
    "PendingUpsellDecision",
    "UpsellDecisionEventInput",
    "UpsellDecisionEventResponse",
    "UpsellDecisionEventType",
    "UpsellDecisionState",
    "UpsellAwareChatResponse",
    "UpsellResult",
    "router",
]
