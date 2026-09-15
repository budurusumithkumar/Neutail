"""Replaceable product-embedding and vector-retrieval boundary."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from langsmith import trace
from sqlalchemy.orm import Session

from models.dto import Product, SemanticProductMatch, SemanticProductSearchInput
from repositories.product_repository import ProductRepository


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_SEMANTIC_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "affordable": ("value", "discount", "accessible"),
    "budget": ("value", "discount", "accessible"),
    "cheap": ("value", "discount", "accessible"),
    "elegant": ("formal", "classic", "tailored", "premium", "wedding"),
    "evening": ("formal", "party", "wedding"),
    "luxury": ("premium", "exclusive", "cashmere"),
    "sophisticated": ("classic", "tailored", "formal", "premium"),
    "trendy": ("modern", "contemporary", "statement", "new", "arrival"),
}


def build_product_document(product: Product) -> str:
    """Build a stable semantic document without price or inventory values."""

    fields = (
        ("Product", product.product_name),
        ("Category", product.category),
        ("Subcategory", product.subcategory),
        ("Brand", product.brand),
        ("Brand tier", product.brand_tier),
        ("Color", product.color),
        ("Style", product.style),
        ("Occasion", product.occasion),
        ("Material", product.material),
        ("Fit", product.fit_type),
        ("New arrival", "yes" if product.new_arrival else "no"),
        ("Exclusive", "yes" if product.exclusive_flag else "no"),
        ("Private label", "yes" if product.private_label_flag else "no"),
    )
    return "\n".join(
        f"{label}: {value}" for label, value in fields if value is not None
    )


class EmbeddingProvider(Protocol):
    """Provider-neutral embedding contract used by product retrieval."""

    def embed_documents(self, documents: list[str]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


class VectorStore(Protocol):
    """Minimal replaceable vector-store contract."""

    def replace(self, records: list["VectorRecord"]) -> None: ...

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        filters: dict[str, str],
    ) -> list[SemanticProductMatch]: ...

    def count(self) -> int: ...


class HashingEmbeddingProvider:
    """Dependency-free deterministic embeddings for the local PoC.

    It deliberately implements the provider abstraction rather than pretending
    to be a production semantic model. A hosted embedding model can replace it
    without changing the Discovery Agent or MCP contract.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 64:
            raise ValueError("dimensions must be at least 64")
        self.dimensions = dimensions

    def embed_documents(self, documents: list[str]) -> list[list[float]]:
        return [self._embed(document, expand=False) for document in documents]

    def embed_query(self, query: str) -> list[float]:
        return self._embed(query, expand=True)

    def _embed(self, value: str, *, expand: bool) -> list[float]:
        tokens = _TOKEN_PATTERN.findall(value.casefold())
        if expand:
            tokens = [
                expanded
                for token in tokens
                for expanded in (token, *_SEMANTIC_EXPANSIONS.get(token, ()))
            ]

        vector = [0.0] * self.dimensions
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign

        magnitude = math.sqrt(sum(component * component for component in vector))
        if magnitude:
            vector = [component / magnitude for component in vector]
        return vector


@dataclass(frozen=True)
class VectorRecord:
    sku: str
    vector: list[float]
    metadata: dict[str, str]


class InMemoryVectorStore:
    """Thread-safe cosine store suitable for the seeded demo catalogue."""

    def __init__(self) -> None:
        self._records: dict[str, VectorRecord] = {}
        self._lock = RLock()

    def replace(self, records: list[VectorRecord]) -> None:
        with self._lock:
            self._records = {record.sku: record for record in records}

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        filters: dict[str, str],
    ) -> list[SemanticProductMatch]:
        with self._lock:
            records = list(self._records.values())

        matches: list[SemanticProductMatch] = []
        for record in records:
            if any(
                record.metadata.get(key, "").casefold() != value.casefold()
                for key, value in filters.items()
                if value
            ):
                continue
            similarity = sum(
                left * right for left, right in zip(vector, record.vector)
            )
            if similarity <= 0:
                continue
            matches.append(
                SemanticProductMatch(
                    sku=record.sku,
                    similarity_score=min(round(similarity, 6), 1.0),
                )
            )
        return sorted(
            matches,
            key=lambda match: (-match.similarity_score, match.sku),
        )[:limit]

    def count(self) -> int:
        with self._lock:
            return len(self._records)


class ProductRetrievalService:
    """Own product document construction, embedding, and vector search."""

    def __init__(
        self,
        session: Session | None = None,
        *,
        repository: ProductRepository | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        if repository is None and session is None:
            raise ValueError("session or repository is required")
        if repository is not None:
            self._repository = repository
        else:
            assert session is not None
            self._repository = ProductRepository(session)
        self._embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self._vector_store = vector_store or get_product_vector_store()

    def refresh_index(self) -> int:
        products = self._repository.list_active()
        documents = [build_product_document(product) for product in products]
        with trace(
            name="build_product_embedding_index",
            run_type="retriever",
            inputs={"product_count": len(products)},
            tags=["discovery", "embeddings", "product-catalogue"],
        ) as run:
            vectors = self._embedding_provider.embed_documents(documents)
            self._vector_store.replace(
                [
                    VectorRecord(
                        sku=product.sku,
                        vector=vector,
                        metadata={
                            "category": product.category or "",
                            "occasion": product.occasion or "",
                        },
                    )
                    for product, vector in zip(products, vectors)
                ]
            )
            run.end(outputs={"indexed_count": len(products)})
        return len(products)

    def semantic_search(
        self,
        request: SemanticProductSearchInput,
    ) -> list[SemanticProductMatch]:
        if not isinstance(request, SemanticProductSearchInput):
            request = SemanticProductSearchInput.model_validate(request)
        if self._vector_store.count() == 0:
            self.refresh_index()
        filters = {
            key: value
            for key, value in {
                "category": request.category,
                "occasion": request.occasion,
            }.items()
            if value
        }
        with trace(
            name="vector_search",
            run_type="retriever",
            inputs={
                "query_characters": len(request.query),
                "filters": filters,
                "limit": request.limit,
            },
            tags=["discovery", "vector-store", "product-catalogue"],
        ) as run:
            query_vector = self._embedding_provider.embed_query(request.query)
            matches = self._vector_store.search(
                query_vector,
                limit=request.limit,
                filters=filters,
            )
            run.end(outputs={"match_count": len(matches)})
            return matches


_product_vector_store = InMemoryVectorStore()


def get_product_vector_store() -> InMemoryVectorStore:
    return _product_vector_store


def clear_product_vector_store() -> None:
    _product_vector_store.replace([])


__all__ = [
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "InMemoryVectorStore",
    "ProductRetrievalService",
    "VectorRecord",
    "VectorStore",
    "build_product_document",
    "clear_product_vector_store",
    "get_product_vector_store",
]
