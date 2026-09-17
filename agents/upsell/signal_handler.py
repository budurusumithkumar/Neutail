"""Translate specialist-agent signals into governed Upsell triggers."""

from typing import Any, Optional

from models.upsell import UpsellTrigger, UpsellTriggerType


class UpsellSignalHandler:
    SUPPORTED_SIGNALS = frozenset(
        {
            UpsellTriggerType.HIGH_PRODUCT_ENGAGEMENT.value,
            UpsellTriggerType.CHRONIC_FIT_RISK.value,
        }
    )

    @classmethod
    def from_agent_result(
        cls, *, source_agent: str, result: dict[str, Any]
    ) -> Optional[UpsellTrigger]:
        signals = result.get("downstream_signals", [])
        if not isinstance(signals, list):
            return None
        for raw in signals:
            if not isinstance(raw, dict):
                continue
            signal_type = raw.get("signal_type")
            if signal_type not in cls.SUPPORTED_SIGNALS:
                continue
            metadata = {
                key: value
                for key, value in raw.items()
                if key not in {"signal_type", "sku", "strength"}
            }
            return UpsellTrigger(
                trigger_type=signal_type,
                source_agent=source_agent,
                sku=raw.get("sku"),
                strength=raw.get("strength"),
                metadata=metadata,
            )
        return None


__all__ = ["UpsellSignalHandler"]
