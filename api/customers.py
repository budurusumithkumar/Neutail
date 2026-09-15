"""Authenticated customer routes used by the Neu.Tail UI."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import get_authenticated_customer_id
from models.dto import CustomerSummary
from services.customer_profile_service import CustomerNotFoundError
from services.customer_summary_service import CustomerSummaryService
from tools.runtime import get_runtime


CustomerIdentityDependency = Annotated[
    str,
    Depends(get_authenticated_customer_id),
]

router = APIRouter(prefix="/api/v1/customers", tags=["Customer"])


@router.get(
    "/me/summary",
    response_model=CustomerSummary,
    response_description="Customer summary",
    summary="Get customer summary for UI",
    operation_id="getCustomerSummary",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Unauthorized"}},
)
async def get_customer_summary(
    customer_id: CustomerIdentityDependency,
) -> CustomerSummary:
    try:
        with get_runtime().session() as session:
            return CustomerSummaryService(session).get_summary(customer_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The authenticated customer no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


__all__ = ["get_customer_summary", "router"]
