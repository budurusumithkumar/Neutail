"""Replaceable vector retrieval over historical fit outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from langsmith import trace
from sqlalchemy.orm import Session

from models.dto import FitProfile, Product
from models.fit import FitCaseRecord, SimilarFitCase, SimilarFitCaseRequest
from repositories.fit_evidence_repository import FitEvidenceRepository
from services.fit_evidence_service import normalize_fit_return_reason
from services.fit_profile_service import FitProfileNotFoundError, FitProfileService
from services.product_catalog_service import ProductCatalogService
from services.product_retrieval_service import HashingEmbeddingProvider


def build_fit_document(case: FitCaseRecord) -> str:
    """Create a stable, non-identifying semantic fit-outcome document."""

    values = (
        ("Brand", case.brand),
        ("Category", case.category),
        ("Fit type", case.fit_type),
        ("Material", case.material),
        ("Purchased size", case.purchased_size),
        ("Outcome", case.outcome),
        ("Return reason", normalize_fit_return_reason(case.return_reason).value),
        ("Exchange size", case.exchange_size),
    )
    return "\n".join(f"{label}: {value}" for label, value in values if value)


@dataclass(frozen=True)
class FitVectorRecord:
    evidence_id: str
    customer_id: str
    vector: list[float]
    case: FitCaseRecord


class InMemoryFitVectorStore:
    """Small thread-safe cosine store for the seeded fit-history corpus."""

    def __init__(self) -> None:
        self._records: dict[str, FitVectorRecord] = {}
        self._lock = RLock()

    def replace(self, records: list[FitVectorRecord]) -> None:
        with self._lock:
            self._records = {record.evidence_id: record for record in records}

    def search(
        self,
        vector: list[float],
        *,
        excluded_customer_id: str,
        limit: int,
    ) -> list[tuple[float, FitCaseRecord]]:
        with self._lock:
            records = list(self._records.values())
        ranked = []
        for record in records:
            if record.customer_id == excluded_customer_id:
                continue
            similarity = sum(
                left * right for left, right in zip(vector, record.vector)
            )
            if similarity > 0:
                ranked.append((min(round(similarity, 6), 1.0), record.case))
        return sorted(ranked, key=lambda item: (-item[0], item[1].evidence_id))[
            :limit
        ]

    def count(self) -> int:
        with self._lock:
            return len(self._records)


class FitRetrievalService:
    """Own fit-case document construction, indexing, and semantic retrieval."""

    def __init__(
        self,
        session: Session,
        *,
        repository: FitEvidenceRepository | None = None,
        embedding_provider: HashingEmbeddingProvider | None = None,
        vector_store: InMemoryFitVectorStore | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or FitEvidenceRepository(session)
        self._embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self._vector_store = vector_store or get_fit_vector_store()

    def refresh_index(self) -> int:
        cases = self._repository.list_all()
        documents = [build_fit_document(case) for case in cases]
        with trace(
            name="build_fit_embedding_index",
            run_type="retriever",
            inputs={"fit_case_count": len(cases)},
            tags=["fit-agent", "embeddings", "fit-history"],
        ) as run:
            vectors = self._embedding_provider.embed_documents(documents)
            self._vector_store.replace(
                [
                    FitVectorRecord(
                        evidence_id=case.evidence_id,
                        customer_id=case.customer_id,
                        vector=vector,
                        case=case,
                    )
                    for case, vector in zip(cases, vectors)
                ]
            )
            run.end(outputs={"indexed_count": len(cases)})
        return len(cases)

    def retrieve(self, request: SimilarFitCaseRequest) -> list[SimilarFitCase]:
        if not isinstance(request, SimilarFitCaseRequest):
            request = SimilarFitCaseRequest.model_validate(request)
        product = ProductCatalogService(self._session).get_product(request.sku)
        try:
            profile = FitProfileService(self._session).get_fit_profile(
                request.customer_id
            )
        except FitProfileNotFoundError:
            profile = FitProfile(customer_id=request.customer_id)
        if self._vector_store.count() == 0:
            self.refresh_index()

        query = self._build_query(product, profile, request.requested_size)
        with trace(
            name="vector_fit_search",
            run_type="retriever",
            inputs={
                "sku": request.sku,
                "requested_size": request.requested_size,
                "limit": request.limit,
            },
            tags=["fit-agent", "vector-store", "fit-history"],
        ) as run:
            vector = self._embedding_provider.embed_query(query)
            matches = self._vector_store.search(
                vector,
                excluded_customer_id=request.customer_id,
                limit=request.limit,
            )
            results = [
                SimilarFitCase(
                    evidence_id=case.evidence_id,
                    similarity_score=score,
                    brand=case.brand,
                    category=case.category,
                    fit_type=case.fit_type,
                    purchased_size=case.purchased_size,
                    outcome=case.outcome,
                    return_reason=(
                        normalize_fit_return_reason(case.return_reason)
                        if case.outcome == "RETURNED"
                        else None
                    ),
                    exchange_size=case.exchange_size or None,
                )
                for score, case in matches
            ]
            run.end(outputs={"match_count": len(results)})
            return results

    @staticmethod
    def _build_query(
        product: Product,
        profile: FitProfile,
        requested_size: str | None,
    ) -> str:
        values = (
            ("Brand", product.brand),
            ("Category", product.category),
            ("Fit type", product.fit_type),
            ("Material", product.material),
            ("Requested size", requested_size),
            ("Usual size", profile.usual_size),
            ("Preferred fit", profile.preferred_fit),
        )
        return "\n".join(f"{label}: {value}" for label, value in values if value)


_fit_vector_store = InMemoryFitVectorStore()


def get_fit_vector_store() -> InMemoryFitVectorStore:
    return _fit_vector_store


def clear_fit_vector_store() -> None:
    _fit_vector_store.replace([])


__all__ = [
    "FitRetrievalService",
    "InMemoryFitVectorStore",
    "build_fit_document",
    "clear_fit_vector_store",
    "get_fit_vector_store",
]
