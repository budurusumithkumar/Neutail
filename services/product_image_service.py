"""Resolve the locally bundled demo catalogue images exposed by NeutailUI."""

from __future__ import annotations

from typing import Optional


DEMO_PRODUCT_IMAGE_SKUS = frozenset(
    {
        "SKU00008",
        "SKU00064",
        "SKU00081",
        "SKU00094",
        "SKU00136",
        "SKU00158",
        "SKU00165",
        "SKU00185",
    }
)


def get_product_image_url(sku: str) -> Optional[str]:
    """Return the frontend-public URL for a generated demo product image."""

    normalized_sku = sku.strip().upper()
    if normalized_sku not in DEMO_PRODUCT_IMAGE_SKUS:
        return None
    return f"/products/{normalized_sku}.webp"


__all__ = ["DEMO_PRODUCT_IMAGE_SKUS", "get_product_image_url"]
