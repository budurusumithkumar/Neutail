"""Authoritative customer facts and normalized profile access."""

from __future__ import annotations

from sqlalchemy import exists as sql_exists, select
from sqlalchemy.orm import Session

from models.dto import CustomerMaster, CustomerPreferences, CustomerProfileFacts
from models.entities import Customer


class CustomerNotFoundError(LookupError):
    """Raised when a requested customer does not exist."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Customer '{customer_id}' was not found")


class CustomerProfileService:
    """Read customer master data used by profiling and session workflows.

    A SQLAlchemy session is injected so transaction and connection ownership
    remains with the API or tool-call boundary. This service never commits or
    mutates customer records.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_customer(self, customer_id: str) -> CustomerMaster:
        """Fetch core customer master fields.

        Raises:
            ValueError: If ``customer_id`` is empty.
            CustomerNotFoundError: If the customer does not exist.
        """

        customer = self._get_customer_entity(customer_id)
        return CustomerMaster.model_validate(customer)

    def get_preferences(self, customer_id: str) -> CustomerPreferences:
        """Return normalized category, colour, style, occasion, and fit data.

        Pipe-delimited persistence values are converted to lists by the DTO.

        Raises:
            ValueError: If ``customer_id`` is empty.
            CustomerNotFoundError: If the customer does not exist.
        """

        customer = self._get_customer_entity(customer_id)
        return CustomerPreferences.model_validate(customer)

    def get_profile_facts(self, customer_id: str) -> CustomerProfileFacts:
        """Return the facts needed for deterministic segment derivation.

        The stored ``segment`` field is intentionally excluded. The profiling
        agent should derive it from ``affluence_band`` and ``loyalty_status``.

        Raises:
            ValueError: If ``customer_id`` is empty.
            CustomerNotFoundError: If the customer does not exist.
        """

        customer = self._get_customer_entity(customer_id)
        return CustomerProfileFacts.model_validate(customer)

    def exists(self, customer_id: str) -> bool:
        """Return whether a non-empty customer identifier exists."""

        normalized_id = self._normalize_customer_id(customer_id, required=False)
        if normalized_id is None:
            return False

        statement = select(
            sql_exists().where(Customer.customer_id == normalized_id)
        )
        return bool(self._session.scalar(statement))

    def _get_customer_entity(self, customer_id: str) -> Customer:
        normalized_id = self._normalize_customer_id(customer_id, required=True)
        customer = self._session.get(Customer, normalized_id)
        if customer is None:
            raise CustomerNotFoundError(normalized_id)
        return customer

    @staticmethod
    def _normalize_customer_id(
        customer_id: str, *, required: bool
    ) -> str | None:
        if not isinstance(customer_id, str):
            if required:
                raise ValueError("customer_id must be a non-empty string")
            return None

        normalized_id = customer_id.strip()
        if normalized_id:
            return normalized_id
        if required:
            raise ValueError("customer_id must be a non-empty string")
        return None


__all__ = ["CustomerNotFoundError", "CustomerProfileService"]
