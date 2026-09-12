"""Read-only loyalty status, activity, and tier-progress service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.dto import LoyaltyProfile, LoyaltySummary, LoyaltyTransaction
from models.entities import Loyalty, LoyaltyTransaction as LoyaltyTransactionEntity
from services._date_utils import sqlite_datetime, utc_now, validate_positive_int


class LoyaltyProfileNotFoundError(LookupError):
    """Raised when a customer has no loyalty profile."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Loyalty profile for customer '{customer_id}' was not found")


class LoyaltyService:
    """Expose authoritative loyalty facts without changing points balances."""

    RECENT_ACTIVITY_DAYS = 30

    def __init__(
        self,
        session: Session,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now

    def get_loyalty_profile(self, customer_id: str) -> LoyaltyProfile:
        """Fetch a customer's current loyalty tier and balance."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        entity = self._session.get(Loyalty, normalized_customer_id)
        if entity is None:
            raise LoyaltyProfileNotFoundError(normalized_customer_id)
        return LoyaltyProfile.model_validate(entity)

    def get_loyalty_transactions(
        self, customer_id: str, limit: int = 20
    ) -> list[LoyaltyTransaction]:
        """Return recent points activity, newest first."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        validate_positive_int(limit, "limit")
        if limit > 100:
            raise ValueError("limit cannot exceed 100")

        statement = (
            select(LoyaltyTransactionEntity)
            .where(LoyaltyTransactionEntity.customer_id == normalized_customer_id)
            .order_by(
                LoyaltyTransactionEntity.event_datetime.desc(),
                LoyaltyTransactionEntity.loyalty_txn_id.desc(),
            )
            .limit(limit)
        )
        return [
            LoyaltyTransaction.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_loyalty_summary(self, customer_id: str) -> LoyaltySummary:
        """Return tier progress and recent loyalty engagement indicators."""

        profile = self.get_loyalty_profile(customer_id)
        cutoff = self._clock() - timedelta(days=self.RECENT_ACTIVITY_DAYS)
        recent_activity_statement = select(func.count()).where(
            LoyaltyTransactionEntity.customer_id == profile.customer_id,
            func.datetime(LoyaltyTransactionEntity.event_datetime)
            >= sqlite_datetime(cutoff),
            func.datetime(LoyaltyTransactionEntity.event_datetime)
            <= sqlite_datetime(self._clock()),
        )
        recent_activity_count = int(
            self._session.scalar(recent_activity_statement) or 0
        )

        points_balance = max(profile.points_balance or 0, 0)
        next_tier_points = max(profile.next_tier_points or 0, 0)
        return LoyaltySummary(
            tier=profile.tier,
            points_balance=points_balance,
            points_to_next_tier=max(next_tier_points - points_balance, 0),
            lifetime_points=max(profile.lifetime_points or 0, 0),
            streak_days=max(profile.streak_days or 0, 0),
            recent_activity_count=recent_activity_count,
        )

    @staticmethod
    def _normalize_customer_id(customer_id: Any) -> str:
        if isinstance(customer_id, str) and customer_id.strip():
            return customer_id.strip()
        raise ValueError("customer_id must be a non-empty string")


__all__ = ["LoyaltyProfileNotFoundError", "LoyaltyService"]
