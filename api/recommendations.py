"""Authenticated personalized recommendation routes for NeutailUI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from agents.profiling import ProfileAgentError
from api.auth import ErrorResponse, get_authenticated_customer_id
from models.recommendations import HomeRecommendationsResponse
from orchestrator import (
    NeuTailOrchestrator,
    OrchestratorDependencyError,
)


def _orchestrator(request: Request) -> NeuTailOrchestrator:
    return request.app.state.orchestrator


CustomerIdentity = Annotated[str, Depends(get_authenticated_customer_id)]
OrchestratorDependency = Annotated[NeuTailOrchestrator, Depends(_orchestrator)]

router = APIRouter(prefix="/api/v1/recommendations", tags=["Recommendations"])


@router.get(
    "/home",
    response_model=HomeRecommendationsResponse,
    response_description="Personalized homepage recommendations",
    operation_id="getHomeRecommendations",
    summary="Get in-stock products personalized for the authenticated customer",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def get_home_recommendations(
    request: Request,
    customer_id: CustomerIdentity,
    orchestrator: OrchestratorDependency,
    limit: Annotated[int, Query(ge=1, le=12)] = 8,
) -> HomeRecommendationsResponse | JSONResponse:
    try:
        return await orchestrator.handle_home_recommendations(
            customer_id=customer_id,
            trace_id=request.state.request_id,
            limit=limit,
        )
    except (OrchestratorDependencyError, ProfileAgentError):
        error = ErrorResponse(
            error_code="HOME_RECOMMENDATIONS_UNAVAILABLE",
            message="Personalized recommendations are temporarily unavailable",
            trace_id=request.state.request_id,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error.model_dump(mode="json"),
        )


__all__ = ["get_home_recommendations", "router"]
