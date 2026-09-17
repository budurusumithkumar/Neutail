from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.entities import Base, Customer, Loyalty, ServiceEngagement
from models.upsell import ServiceOfferType, UpsellEvaluationRequest
from services.upsell_policy_service import UpsellPolicyService


NOW = datetime(2026, 9, 17, 12, 0, 0)


@pytest.fixture
def policy_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                _customer("PRESTIGE", "Prestige Champion", premium=0.91),
                _customer("VALUE", "Value Defender", premium=0.25),
                _customer(
                    "NO_CONSENT",
                    "Prestige Champion",
                    premium=0.80,
                    consent=False,
                ),
                Loyalty(customer_id="PRESTIGE", tier="Platinum"),
                Loyalty(customer_id="VALUE", tier="Silver"),
                Loyalty(customer_id="NO_CONSENT", tier="Gold"),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def _customer(
    customer_id: str,
    segment: str,
    *,
    premium: float,
    consent: bool = True,
) -> Customer:
    return Customer(
        customer_id=customer_id,
        first_name=customer_id.title(),
        segment=segment,
        affluence_band="Affluent",
        loyalty_status="Loyal",
        estimated_clv_gbp=2500,
        price_sensitivity=0.2,
        premium_affinity=premium,
        marketing_consent=consent,
    )


def _request(
    *,
    customer_id: str = "PRESTIGE",
    segment: str = "Prestige Champion",
    trigger: str = "HIGH_PRODUCT_ENGAGEMENT",
    strength: float | None = 0.91,
) -> UpsellEvaluationRequest:
    return UpsellEvaluationRequest(
        customer_id=customer_id,
        segment=segment,
        trigger_type=trigger,
        trigger_strength=strength,
        selected_sku="SKU00123",
    )


def _event(
    event_id: str,
    *,
    customer_id: str = "PRESTIGE",
    service_type: str = "STYLING_ADVISORY",
    outcome: str = "OFFER_SHOWN",
    days_ago: int = 1,
    suppressed: bool = False,
) -> ServiceEngagement:
    return ServiceEngagement(
        engagement_id=event_id,
        customer_id=customer_id,
        event_datetime=NOW - timedelta(days=days_ago),
        service_type=service_type,
        outcome=outcome,
        channel="TEST",
        offer_suppressed=suppressed,
    )


def _policy(session: Session) -> UpsellPolicyService:
    return UpsellPolicyService(session, clock=lambda: NOW)


def test_prestige_high_engagement_returns_value_and_trial_candidates(policy_session):
    result = _policy(policy_session).evaluate_eligibility(_request())

    assert result.eligible is True
    assert result.eligible_offers == [
        ServiceOfferType.STYLING_ADVISORY,
        ServiceOfferType.STYLE_PLUS_TRIAL,
    ]
    assert "PRESTIGE_CHAMPION" in result.eligibility_reasons


def test_recent_style_plus_decline_suppresses_all_offers(policy_session):
    policy_session.add(
        _event(
            "DECLINE",
            service_type="STYLE_PLUS_TRIAL",
            outcome="DECLINED",
        )
    )
    policy_session.flush()

    result = _policy(policy_session).evaluate_eligibility(_request())

    assert result.eligible is False
    assert result.suppression_reasons == ["RECENT_STYLE_PLUS_DECLINE"]
    assert result.cooldown_until == NOW - timedelta(days=1) + timedelta(days=30)


def test_active_style_plus_member_cannot_receive_subscription_offers(policy_session):
    policy_session.add(
        _event("MEMBER", service_type="STYLE_PLUS", outcome="ACCEPTED")
    )
    policy_session.flush()

    result = _policy(policy_session).evaluate_eligibility(_request())

    assert result.eligible is True
    assert result.eligible_offers == [ServiceOfferType.STYLING_ADVISORY]
    assert result.suppression_reasons == ["ALREADY_STYLE_PLUS_MEMBER"]


def test_frequency_limit_suppresses_offer(policy_session):
    policy_session.add_all([_event(f"FREQ-{index}") for index in range(3)])
    policy_session.flush()

    result = _policy(policy_session).evaluate_eligibility(_request())

    assert result.eligible is False
    assert result.suppression_reasons == ["SERVICE_FREQUENCY_LIMIT"]


def test_insufficient_engagement_is_deterministically_suppressed(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(strength=0.69)
    )

    assert result.eligible is False
    assert result.suppression_reasons == ["INSUFFICIENT_ENGAGEMENT"]


def test_chronic_fit_risk_only_approves_styling_advisory(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(trigger="CHRONIC_FIT_RISK", strength=0.82)
    )

    assert result.eligible_offers == [ServiceOfferType.STYLING_ADVISORY]
    assert result.eligibility_reasons[0] == "CHRONIC_FIT_RISK"


def test_value_defender_does_not_receive_premium_subscription(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(customer_id="VALUE", segment="Value Defender")
    )

    assert result.eligible_offers == [ServiceOfferType.STYLING_ADVISORY]


def test_unknown_trigger_returns_invalid_trigger(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(trigger="NOT_A_REAL_TRIGGER")
    )

    assert result.eligible is False
    assert result.suppression_reasons == ["INVALID_TRIGGER"]


def test_missing_marketing_consent_fails_closed(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(customer_id="NO_CONSENT")
    )

    assert result.eligible is False
    assert result.suppression_reasons == ["CUSTOMER_CONSENT_REQUIRED"]


def test_mismatched_segment_is_incomplete_context(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(segment="Value Defender")
    )

    assert result.eligible is False
    assert result.suppression_reasons == ["CUSTOMER_CONTEXT_INCOMPLETE"]


def test_repeated_styling_engagement_supports_style_plus(policy_session):
    result = _policy(policy_session).evaluate_eligibility(
        _request(trigger="STYLING_ENGAGEMENT", strength=0.90)
    )

    assert ServiceOfferType.STYLE_PLUS in result.eligible_offers


def test_explicit_suppression_fails_closed(policy_session):
    policy_session.add(_event("SUPPRESSED", suppressed=True))
    policy_session.flush()

    result = _policy(policy_session).evaluate_eligibility(_request())

    assert result.eligible is False
    assert result.suppression_reasons == ["SERVICE_NOT_AVAILABLE"]
