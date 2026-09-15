"""Read-only customer summary projection for the Neu.Tail UI."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.dto import CustomerPreferences, CustomerSummary
from models.entities import Customer, Loyalty
from services.customer_profile_service import CustomerNotFoundError


class CustomerSummaryService:
    """Build the compact customer and loyalty record required by the UI."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_summary(self, customer_id: str) -> CustomerSummary:
        normalized_id = customer_id.strip()
        if not normalized_id:
            raise ValueError("customer_id must be a non-empty string")

        statement = (
            select(Customer, Loyalty)
            .outerjoin(Loyalty, Loyalty.customer_id == Customer.customer_id)
            .where(Customer.customer_id == normalized_id)
        )
        row = self._session.execute(statement).one_or_none()
        if row is None:
            raise CustomerNotFoundError(normalized_id)

        customer, loyalty = row
        preferences = CustomerPreferences.model_validate(customer)
        display_name = " ".join(
            part.strip()
            for part in (customer.first_name, customer.last_name)
            if part and part.strip()
        )
        return CustomerSummary(
            customer_id=customer.customer_id,
            display_name=display_name or customer.customer_id,
            city=customer.city,
            segment=customer.segment,
            loyalty_tier=loyalty.tier if loyalty is not None else None,
            points_balance=(
                loyalty.points_balance if loyalty is not None else None
            ),
            preferred_categories=preferences.preferred_categories,
            preferred_colors=preferences.preferred_colors,
            preferred_styles=preferences.preferred_styles,
            usual_size=preferences.usual_size,
            fit_preference=preferences.fit_preference,
        )


__all__ = ["CustomerSummaryService"]
