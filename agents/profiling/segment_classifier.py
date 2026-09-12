"""Deterministic and explainable Neu.Tail segmentation rules."""

from __future__ import annotations

from enum import Enum
from typing import Any

from agents.profiling.models import EnrichedCustomerProfileFacts


class NeuTailSegment(str, Enum):
    """Canonical segment values used by the seeded data and downstream tools."""

    PRESTIGE_CHAMPION = "Prestige Champion"
    VALUE_DEFENDER = "Value Defender"
    ASPIRING_LOYALIST = "Aspiring Loyalist"
    PRICE_EXPLORER = "Price Explorer"


class SegmentClassificationError(ValueError):
    """Raised when mandatory segmentation facts cannot be normalized."""


class SegmentClassifier:
    """Own the four-way affluence and loyalty rule matrix."""

    _RULES = {
        ("affluent", "loyal"): NeuTailSegment.PRESTIGE_CHAMPION,
        ("less affluent", "loyal"): NeuTailSegment.VALUE_DEFENDER,
        ("affluent", "new"): NeuTailSegment.ASPIRING_LOYALIST,
        ("less affluent", "new"): NeuTailSegment.PRICE_EXPLORER,
    }

    def classify(self, facts: EnrichedCustomerProfileFacts) -> NeuTailSegment:
        return self.classify_values(facts.affluence_band, facts.loyalty_status)

    def classify_values(
        self, affluence_band: Any, loyalty_status: Any
    ) -> NeuTailSegment:
        key = (
            self.normalize_affluence(affluence_band).casefold(),
            self.normalize_loyalty(loyalty_status).casefold(),
        )
        return self._RULES[key]

    @staticmethod
    def normalize_affluence(value: Any) -> str:
        normalized = SegmentClassifier._normalize(value)
        aliases = {
            "affluent": "Affluent",
            "less affluent": "Less Affluent",
            "non affluent": "Less Affluent",
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise SegmentClassificationError(
                f"Unsupported affluence_band: {value!r}"
            ) from exc

    @staticmethod
    def normalize_loyalty(value: Any) -> str:
        normalized = SegmentClassifier._normalize(value)
        aliases = {"loyal": "Loyal", "new": "New"}
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise SegmentClassificationError(
                f"Unsupported loyalty_status: {value!r}"
            ) from exc

    @staticmethod
    def _normalize(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            return ""
        return " ".join(
            value.replace("_", " ").replace("-", " ").casefold().split()
        )


__all__ = [
    "NeuTailSegment",
    "SegmentClassificationError",
    "SegmentClassifier",
]

