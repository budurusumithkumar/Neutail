"""Deterministic discovery criteria extraction and strategy selection."""

from __future__ import annotations

import re

from agents.discovery.constants import (
    CATEGORY_TERMS,
    COLOR_TERMS,
    GENDER_TERMS,
    OCCASION_TERMS,
    SEMANTIC_TERMS,
    SIMILAR_ITEM_TERMS,
    STYLE_TERMS,
    SUBCATEGORY_TERMS,
)
from agents.discovery.models import (
    DiscoveryCriteria,
    DiscoveryRequest,
    RetrievalStrategy,
)


_MAX_PRICE = re.compile(
    r"(?:under|below|less\s+than|up\s+to|max(?:imum)?)\s*"
    r"(?:[£$€]|(?:gbp|usd|eur)\s*)?(\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_MIN_PRICE = re.compile(
    r"(?:over|above|more\s+than|at\s+least|min(?:imum)?)\s*"
    r"(?:[£$€]|(?:gbp|usd|eur)\s*)?(\d+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_SIZE = re.compile(r"\bsize\s+([a-z0-9]+)\b", re.IGNORECASE)


def _contains_term(query: str, term: str) -> bool:
    return bool(re.search(rf"\b{re.escape(term)}\b", query))


def _first_mapping_match(query: str, values: dict[str, str]) -> str | None:
    for term in sorted(values, key=len, reverse=True):
        if _contains_term(query, term):
            return values[term]
    return None


class DiscoveryQueryBuilder:
    """Merge orchestrator entities with deterministic query constraints."""

    def build(self, request: DiscoveryRequest) -> DiscoveryCriteria:
        query = request.query.casefold()
        supplied = request.criteria or DiscoveryCriteria()
        session = request.session_context

        gender = (
            supplied.gender
            or _first_mapping_match(query, GENDER_TERMS)
            or request.customer_context.preferences.preferred_gender
        )
        subcategory = supplied.subcategory or _first_mapping_match(
            query, SUBCATEGORY_TERMS
        )
        category = (
            supplied.category
            or session.category
            or _first_mapping_match(query, CATEGORY_TERMS)
        )
        occasion = (
            supplied.occasion
            or session.occasion
            or _first_mapping_match(query, OCCASION_TERMS)
        )
        colors = supplied.colors or [
            color.title() for color in COLOR_TERMS if _contains_term(query, color)
        ]
        styles = supplied.styles or [
            style.title() for style in STYLE_TERMS if _contains_term(query, style)
        ]

        max_match = _MAX_PRICE.search(request.query)
        min_match = _MIN_PRICE.search(request.query)
        size_match = _SIZE.search(request.query)
        semantic_query = supplied.semantic_query
        if semantic_query is None and any(
            _contains_term(query, term) for term in SEMANTIC_TERMS
        ):
            semantic_query = request.query
        if semantic_query is None and not any(
            (
                category,
                subcategory,
                occasion,
                colors,
                styles,
                max_match,
                min_match,
                size_match,
            )
        ):
            semantic_query = request.query

        return DiscoveryCriteria(
            gender=gender,
            category=category,
            subcategory=subcategory,
            occasion=occasion,
            colors=colors,
            styles=styles,
            min_price=(
                supplied.min_price
                if supplied.min_price is not None
                else float(min_match.group(1)) if min_match else None
            ),
            max_price=(
                supplied.max_price
                if supplied.max_price is not None
                else float(max_match.group(1)) if max_match else None
            ),
            requested_size=(
                supplied.requested_size
                or session.requested_size
                or (size_match.group(1).upper() if size_match else None)
            ),
            semantic_query=semantic_query,
        )


class RetrievalStrategySelector:
    """Choose structured, vector, similar-item, or hybrid retrieval."""

    def select(
        self,
        request: DiscoveryRequest,
        criteria: DiscoveryCriteria,
    ) -> RetrievalStrategy:
        query = request.query.casefold()
        if request.session_context.selected_sku and any(
            term in query for term in SIMILAR_ITEM_TERMS
        ):
            return RetrievalStrategy.SIMILAR_ITEM
        if criteria.semantic_query is None:
            return RetrievalStrategy.STRUCTURED

        has_exact_constraint = bool(
            criteria.colors
            or criteria.styles
            or criteria.subcategory
            or criteria.min_price is not None
            or criteria.max_price is not None
            or criteria.requested_size
        )
        return (
            RetrievalStrategy.HYBRID
            if has_exact_constraint
            else RetrievalStrategy.SEMANTIC
        )


__all__ = ["DiscoveryQueryBuilder", "RetrievalStrategySelector"]
