"""Central, explainable policy thresholds for deterministic fit decisions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FitPolicyConfig:
    low_risk_max: float = 0.30
    medium_risk_max: float = 0.65
    chronic_return_rate: float = 0.50
    minimum_evidence_strength: float = 0.20


DEFAULT_FIT_POLICY = FitPolicyConfig()


__all__ = ["DEFAULT_FIT_POLICY", "FitPolicyConfig"]
