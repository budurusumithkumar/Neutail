from __future__ import annotations

from models.dto import Product, SemanticProductSearchInput
from services.product_retrieval_service import (
    InMemoryVectorStore,
    ProductRetrievalService,
    build_product_document,
)


def _product(
    sku: str,
    *,
    style: str,
    occasion: str,
    gender: str = "Women",
) -> Product:
    return Product(
        sku=sku,
        product_name=f"Product {sku}",
        gender=gender,
        category="Dresses",
        subcategory="Midi Dress",
        brand="Neu Test",
        brand_tier="Premium",
        color="Navy",
        style=style,
        occasion=occasion,
        material="Silk",
        fit_type="Regular",
        base_price_gbp=250,
        current_price_gbp=200,
        discount_pct=20,
        sizes=["10", "12"],
        new_arrival=True,
        exclusive_flag=True,
        private_label_flag=False,
        active=True,
    )


class CatalogueRepository:
    def __init__(self, products: list[Product]) -> None:
        self.products = products

    def list_active(self) -> list[Product]:
        return self.products


def test_embedding_document_contains_stable_facts_not_operational_values():
    document = build_product_document(
        _product("DOCUMENT", style="Classic", occasion="Wedding")
    )

    assert "Product DOCUMENT" in document
    assert "Occasion: Wedding" in document
    assert "200" not in document
    assert "inventory" not in document.casefold()
    assert "reserved" not in document.casefold()
    assert "availability" not in document.casefold()


def test_catalogue_can_be_indexed_and_searched_through_replaceable_store():
    products = [
        _product("ELEGANT", style="Classic", occasion="Wedding"),
        _product("RELAXED", style="Relaxed", occasion="Everyday"),
    ]
    service = ProductRetrievalService(
        repository=CatalogueRepository(products),  # type: ignore[arg-type]
        vector_store=InMemoryVectorStore(),
    )

    assert service.refresh_index() == 2
    matches = service.semantic_search(
        SemanticProductSearchInput(
            query="elegant wedding outfit",
            category="Dresses",
            occasion="Wedding",
        )
    )

    assert matches
    assert matches[0].sku == "ELEGANT"
    assert 0 < matches[0].similarity_score <= 1


def test_semantic_search_applies_gender_metadata_filter():
    products = [
        _product(
            "WOMENS",
            style="Classic",
            occasion="Wedding",
            gender="Women",
        ),
        _product(
            "MENS",
            style="Classic",
            occasion="Wedding",
            gender="Men",
        ),
    ]
    service = ProductRetrievalService(
        repository=CatalogueRepository(products),  # type: ignore[arg-type]
        vector_store=InMemoryVectorStore(),
    )
    service.refresh_index()

    matches = service.semantic_search(
        SemanticProductSearchInput(
            query="elegant wedding outfit",
            gender="Men",
            occasion="Wedding",
        )
    )

    assert [match.sku for match in matches] == ["MENS"]
