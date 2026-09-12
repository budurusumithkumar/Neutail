"""Backward-compatible import surface for the Profiling Agent."""

from agents.profiling import (
    CustomerNotFoundError,
    EnrichedCustomerProfileFacts,
    NeuTailSegment,
    ProfileAgent,
    ProfileAgentError,
    ProfileAgentRequest,
    ProfileToolDiscoveryError,
    SegmentClassifier,
)

__all__ = [
    "CustomerNotFoundError",
    "EnrichedCustomerProfileFacts",
    "NeuTailSegment",
    "ProfileAgent",
    "ProfileAgentError",
    "ProfileAgentRequest",
    "ProfileToolDiscoveryError",
    "SegmentClassifier",
]
