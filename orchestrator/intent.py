"""Hybrid deterministic and LLM-assisted intent detection."""

from __future__ import annotations

import re
from typing import Optional

from llm_gateway import IntentResult, LLMGateway, LLMGatewayError
from orchestrator.models import NeuTailState


_CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bdress(?:es)?\b", "Dresses"),
    (r"\bshirts?\b", "Shirts"),
    (r"\btops?\b", "Tops"),
    (r"\b(?:trousers|pants|shorts|bottoms?)\b", "Bottoms"),
    (r"\b(?:coats?|jackets?|outerwear)\b", "Outerwear"),
    (r"\b(?:shoes?|footwear|trainers?|sneakers?)\b", "Footwear"),
    (r"\b(?:bags?|handbags?|accessories)\b", "Accessories"),
)
_OCCASION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwedding\b", "Wedding"),
    (r"\bformal\b", "Formal"),
    (r"\bparty\b", "Party"),
    (r"\bwork(?:wear)?\b|\boffice\b", "Work"),
    (r"\beveryday\b|\bcasual\b", "Everyday"),
)
_SIZE_PATTERN = re.compile(
    r"\bsize\s*(?:is\s*)?([0-9]{1,2}|XXS|XS|S|M|L|XL|XXL|XXXL)\b",
    re.IGNORECASE,
)
_SKU_PATTERN = re.compile(r"\bSKU[\s_-]?(\d{1,8})\b", re.IGNORECASE)


class IntentDetector:
    """Use deterministic rules for clear requests and the gateway for ambiguity."""

    DETERMINISTIC_CONFIDENCE = 0.9

    def __init__(self, gateway: Optional[LLMGateway] = None) -> None:
        self.gateway = gateway or LLMGateway()

    async def detect(self, state: NeuTailState) -> IntentResult:
        request = state["request"]
        session = state["session"]
        deterministic = self._detect_deterministically(request.message)
        if deterministic.confidence >= self.DETERMINISTIC_CONFIDENCE:
            return deterministic

        try:
            detected = await self.gateway.invoke(
                use_case="intent_detection",
                context={
                    "message": request.message,
                    "session_context": {
                        **session.entity_context(),
                        "last_intent": session.last_intent,
                    },
                },
                agent_name="Orchestrator",
                trace_id=request.trace_id,
                session_id=request.session_id,
                prompt_version="v3",
                response_model=IntentResult,
            )
        except (LLMGatewayError, LookupError):
            return deterministic

        if not isinstance(detected, IntentResult):
            return deterministic
        return self._merge_extracted_entities(detected, deterministic)

    @classmethod
    def _detect_deterministically(cls, message: str) -> IntentResult:
        normalized = " ".join(message.casefold().split())
        category = cls._first_match(message, _CATEGORY_PATTERNS)
        occasion = cls._first_match(message, _OCCASION_PATTERNS)
        size_match = _SIZE_PATTERN.search(message)
        requested_size = size_match.group(1).upper() if size_match else None
        sku_match = _SKU_PATTERN.search(message)
        selected_sku = (
            f"SKU{sku_match.group(1).zfill(5)}" if sku_match else None
        )

        profile_request = any(
            phrase in normalized
            for phrase in (
                "my profile",
                "my preferences",
                "customer context",
                "my segment",
                "loyalty status",
            )
        )
        fit_request = any(
            token in normalized
            for token in (
                " fit ",
                "fits me",
                "fit me",
                "right size",
                "size advice",
                "sizing",
            )
        ) or requested_size is not None
        service_request = any(
            phrase in normalized
            for phrase in (
                "styling support",
                "personal stylist",
                "personal shopper",
                "style service",
                "subscription",
                "style plus",
            )
        )
        explicit_product_request = category is not None or "outfit" in normalized
        generic_product_request = any(
            phrase in normalized
            for phrase in (
                "find me",
                "show me",
                "recommend",
                "looking for",
                "what should i wear",
            )
        )
        product_request = explicit_product_request or (
            generic_product_request and not profile_request
        )

        matched: list[str] = []
        if product_request:
            matched.append("PRODUCT_DISCOVERY")
        if fit_request:
            matched.append("FIT_QUERY")
        if service_request:
            matched.append("SERVICE_QUERY")
        if profile_request:
            matched.append("CUSTOMER_CONTEXT")

        if not matched:
            primary = "GENERAL_QUERY"
            confidence = 0.4
        else:
            primary = matched[0]
            confidence = 0.96 if len(matched) == 1 else 0.93

        secondary = [
            item
            for item in matched[1:]
            if item in {"PRODUCT_DISCOVERY", "FIT_QUERY", "SERVICE_QUERY"}
        ]
        return IntentResult(
            intent=primary,
            secondary_intents=secondary,
            category=category,
            occasion=occasion,
            requested_size=requested_size,
            selected_sku=selected_sku,
            confidence=confidence,
        )

    @staticmethod
    def _first_match(
        message: str, patterns: tuple[tuple[str, str], ...]
    ) -> Optional[str]:
        for pattern, value in patterns:
            if re.search(pattern, message, re.IGNORECASE):
                return value
        return None

    @staticmethod
    def _merge_extracted_entities(
        detected: IntentResult, deterministic: IntentResult
    ) -> IntentResult:
        updates = {
            field: getattr(detected, field) or getattr(deterministic, field)
            for field in ("category", "occasion", "requested_size", "selected_sku")
        }
        unique_secondary = list(
            dict.fromkeys(
                [
                    *detected.secondary_intents,
                    *deterministic.secondary_intents,
                ]
            )
        )
        if detected.intent in unique_secondary:
            unique_secondary.remove(detected.intent)
        return detected.model_copy(
            update={**updates, "secondary_intents": unique_secondary}
        )


__all__ = ["IntentDetector"]
