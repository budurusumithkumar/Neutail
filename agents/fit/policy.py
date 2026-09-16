"""Downstream fit signals that preserve specialist-agent ownership."""

from agents.fit.constants import DEFAULT_FIT_POLICY, FitPolicyConfig
from models.fit import FitDecision, FitEvidence, FitSignal


class FitPolicy:
    def __init__(self, config: FitPolicyConfig = DEFAULT_FIT_POLICY) -> None:
        self.config = config

    def evaluate_signals(
        self,
        *,
        decision: FitDecision,
        evidence: FitEvidence,
    ) -> list[FitSignal]:
        signals: list[FitSignal] = []
        if evidence.historical_return_rate > self.config.chronic_return_rate:
            signals.append(
                FitSignal(
                    signal_type="CHRONIC_FIT_RISK",
                    severity="HIGH",
                    strength=evidence.historical_return_rate,
                )
            )
        if decision.risk_band == "HIGH":
            signals.append(
                FitSignal(
                    signal_type="HIGH_FIT_RISK",
                    severity="HIGH",
                    strength=decision.risk_score,
                )
            )
        return signals


__all__ = ["FitPolicy"]
