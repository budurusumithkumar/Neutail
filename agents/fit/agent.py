"""MCP-only Size & Fit Agent with deterministic recommendation ownership."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Optional, Protocol

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ClientError, ToolError
from langsmith import get_current_run_tree, trace, traceable

from agents.fit.fit_calculator import FitCalculator
from agents.fit.models import FitRequest
from agents.fit.policy import FitPolicy
from llm_gateway import LLMGateway, LLMGatewayError
from models.dto import FitProfile, Product
from models.fit import (
    FitEvidence,
    FitResult,
    ProductFitContext,
    SimilarFitCase,
    SimilarFitCaseRequest,
)
from tools.permissions import AgentName, FIT_AGENT_TOOLS
from tools.registry import create_agent_server


FIT_EXPLANATIONS_ENV = "NEUTAIL_FIT_EXPLANATIONS_LLM"


class FitAgentError(RuntimeError):
    """Base error raised at the Fit Agent boundary."""


class FitToolDiscoveryError(FitAgentError):
    """Raised when runtime discovery does not match the Fit capability scope."""


class FitToolInvocationError(FitAgentError):
    """Raised when an unpermitted Fit tool is requested."""


class FitToolClient(Protocol):
    async def discover_tools(self) -> list[str]: ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


def _tool_payload(result: Any) -> Any:
    payload = result.structured_content
    if payload is None:
        payload = result.data
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


class FastMCPFitToolClient:
    """Discover and invoke only the registry-scoped Fit MCP server."""

    def __init__(
        self,
        server_factory: Callable[[], FastMCP] = lambda: create_agent_server(
            AgentName.FIT
        ),
    ) -> None:
        self._server_factory = server_factory

    async def discover_tools(self) -> list[str]:
        with trace(
            name="fit_tool_discovery",
            run_type="tool",
            inputs={"agent": AgentName.FIT.value},
            tags=["fastmcp", "tool-discovery", "fit-agent"],
        ) as run:
            async with Client(self._server_factory()) as client:
                names = sorted(tool.name for tool in await client.list_tools())
            run.end(outputs={"tools": names})
            return names

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in FIT_AGENT_TOOLS:
            raise FitToolInvocationError(
                f"Fit Agent is not permitted to call '{tool_name}'"
            )
        with trace(
            name=tool_name,
            run_type="tool",
            inputs={"argument_keys": sorted(arguments)},
            tags=["fastmcp", "fit-agent"],
            metadata={"agent": AgentName.FIT.value},
        ) as run:
            async with Client(self._server_factory()) as client:
                result = await client.call_tool(tool_name, arguments)
            payload = _tool_payload(result)
            run.end(outputs={"result_type": type(payload).__name__})
            return payload


def _trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    if isinstance(request, FitRequest):
        return {
            "customer_id": request.customer_context.customer_id,
            "session_id": request.session_context.session_id,
            "sku": request.sku,
            "requested_size": request.requested_size,
            "mode": request.mode,
        }
    return {"request_type": type(request).__name__}


def _trace_outputs(output: Any) -> dict[str, Any]:
    if isinstance(output, FitResult):
        return {
            "status": output.status,
            "sku": output.sku,
            "recommended_size": output.recommended_size,
            "risk_band": output.risk_band,
            "confidence": output.confidence,
            "signal_count": len(output.downstream_signals),
        }
    return {"output_type": type(output).__name__}


class FitAgent:
    """Retrieve evidence through MCP and make deterministic fit decisions."""

    def __init__(
        self,
        *,
        tool_client: FitToolClient | None = None,
        calculator: FitCalculator | None = None,
        policy: FitPolicy | None = None,
        llm_gateway: LLMGateway | None = None,
        explanations_enabled: Optional[bool] = None,
    ) -> None:
        self.tool_client = tool_client or FastMCPFitToolClient()
        self.calculator = calculator or FitCalculator()
        self.policy = policy or FitPolicy()
        self.llm_gateway = llm_gateway or LLMGateway()
        self.explanations_enabled = (
            os.getenv(FIT_EXPLANATIONS_ENV, "false").casefold() == "true"
            if explanations_enabled is None
            else explanations_enabled
        )

    @traceable(
        name="fit_agent",
        run_type="chain",
        tags=["fit-agent", "deterministic-fit", "return-prevention"],
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    async def execute(self, request: FitRequest | dict[str, Any]) -> FitResult:
        if not isinstance(request, FitRequest):
            request = FitRequest.model_validate(request)
        self._attach_request_metadata(request)
        await self._verify_tool_scope()

        try:
            product_payload = await self.tool_client.call_tool(
                "get_product_details", {"sku": request.sku}
            )
            product = self._product_context(Product.model_validate(product_payload))
        except (ClientError, ToolError, LookupError, ValueError) as exc:
            status = "PRODUCT_NOT_FOUND" if "not found" in str(exc).casefold() else "FAILED"
            return FitResult(
                status=status,
                sku=request.sku,
                requested_size=request.requested_size,
                reason_codes=[status],
                errors=[type(exc).__name__],
            )

        profile = await self._get_profile(request)
        try:
            evidence_payload = await self.tool_client.call_tool(
                "fit_build_evidence",
                {
                    "customer_id": request.customer_context.customer_id,
                    "sku": request.sku,
                    "requested_size": request.requested_size,
                },
            )
            evidence = FitEvidence.model_validate(evidence_payload)
        except (ClientError, ToolError, LookupError, ValueError) as exc:
            return FitResult(
                status="FAILED",
                sku=request.sku,
                requested_size=request.requested_size,
                reason_codes=["FIT_EVIDENCE_UNAVAILABLE"],
                errors=[type(exc).__name__],
            )

        evidence.similar_fit_cases = await self._retrieve_similar_cases(request)
        with trace(
            name="calculate_fit_risk",
            run_type="chain",
            inputs={
                "sku": request.sku,
                "requested_size": request.requested_size,
                "evidence_strength": evidence.evidence_strength,
            },
        ) as run:
            decision = self.calculator.evaluate(
                profile=profile,
                product=product,
                requested_size=request.requested_size,
                evidence=evidence,
            )
            run.end(outputs=decision.model_dump(mode="json"))

        with trace(
            name="apply_fit_policy",
            run_type="chain",
            inputs={"risk_band": decision.risk_band},
        ) as run:
            signals = self.policy.evaluate_signals(
                decision=decision,
                evidence=evidence,
            )
            run.end(outputs={"signal_count": len(signals)})

        explanation = await self._explain(product, decision, request)
        status = (
            "INSUFFICIENT_EVIDENCE"
            if decision.action == "INSUFFICIENT_EVIDENCE"
            else "SUCCESS"
        )
        result = FitResult(
            status=status,
            sku=request.sku,
            requested_size=decision.requested_size,
            recommended_size=decision.recommended_size,
            confidence=decision.confidence,
            risk_score=decision.risk_score,
            risk_band=decision.risk_band,
            action=decision.action,
            reason_codes=decision.reason_codes,
            explanation=explanation,
            downstream_signals=signals,
            evidence_summary=evidence.to_summary(),
        )
        self._attach_result_metadata(result)
        return result

    async def _get_profile(self, request: FitRequest) -> FitProfile:
        try:
            payload = await self.tool_client.call_tool(
                "fit_get_profile",
                {"customer_id": request.customer_context.customer_id},
            )
            return FitProfile.model_validate(payload)
        except (ClientError, ToolError, LookupError, ValueError):
            preferences = request.customer_context.preferences
            return FitProfile(
                customer_id=request.customer_context.customer_id,
                usual_size=preferences.usual_size,
                preferred_fit=preferences.fit_preference,
                size_confidence=0.0,
                return_risk_score=request.customer_context.fit_risk_score,
            )

    async def _retrieve_similar_cases(
        self, request: FitRequest
    ) -> list[SimilarFitCase]:
        try:
            payload = await self.tool_client.call_tool(
                "fit_retrieve_similar_cases",
                {
                    "request": SimilarFitCaseRequest(
                        customer_id=request.customer_context.customer_id,
                        sku=request.sku,
                        requested_size=request.requested_size,
                        limit=5,
                    ).model_dump(mode="json")
                },
            )
            return [SimilarFitCase.model_validate(item) for item in (payload or [])]
        except (ClientError, ToolError, LookupError, ValueError, RuntimeError):
            return []

    async def _explain(self, product, decision, request: FitRequest) -> Optional[str]:
        if not self.explanations_enabled:
            return None
        with trace(
            name="fit_explanation",
            run_type="chain",
            inputs={"sku": product.sku, "risk_band": decision.risk_band},
        ) as run:
            try:
                explanation = await self.llm_gateway.invoke(
                    use_case="fit_explanation",
                    prompt_version="v1",
                    context={
                        "product": product.model_dump(mode="json"),
                        "fit_result": decision.model_dump(mode="json"),
                    },
                    agent_name="FitAgent",
                    trace_id=request.trace_id or request.session_context.session_id,
                    session_id=request.session_context.session_id,
                )
            except (LLMGatewayError, LookupError, TimeoutError) as exc:
                run.end(outputs={"status": "UNAVAILABLE"}, error=type(exc).__name__)
                return None
            value = explanation.strip() if isinstance(explanation, str) else ""
            run.end(outputs={"status": "SUCCESS" if value else "EMPTY"})
            return value or None

    async def _verify_tool_scope(self) -> None:
        discovered = set(await self.tool_client.discover_tools())
        if discovered != FIT_AGENT_TOOLS:
            raise FitToolDiscoveryError(
                "Fit Agent capability mismatch: "
                f"expected={sorted(FIT_AGENT_TOOLS)}, actual={sorted(discovered)}"
            )

    @staticmethod
    def _product_context(product: Product) -> ProductFitContext:
        return ProductFitContext(
            sku=product.sku,
            product_name=product.product_name or product.sku,
            brand=product.brand or "UNKNOWN",
            category=product.category or "UNKNOWN",
            fit_type=product.fit_type,
            available_sizes=product.sizes,
            material=product.material,
        )

    @staticmethod
    def _attach_request_metadata(request: FitRequest) -> None:
        current = get_current_run_tree()
        if current is not None:
            current.metadata.update(
                {
                    "customer_id": request.customer_context.customer_id,
                    "session_id": request.session_context.session_id,
                    "sku": request.sku,
                    "requested_size": request.requested_size,
                }
            )

    @staticmethod
    def _attach_result_metadata(result: FitResult) -> None:
        current = get_current_run_tree()
        if current is not None:
            current.metadata.update(
                {
                    "recommended_size": result.recommended_size,
                    "risk_band": result.risk_band,
                    "confidence": result.confidence,
                    "evidence_count": (
                        result.evidence_summary.exact_product_cases
                        + result.evidence_summary.same_brand_cases
                        + result.evidence_summary.same_category_cases
                        if result.evidence_summary
                        else 0
                    ),
                    "vector_matches": (
                        result.evidence_summary.vector_matches
                        if result.evidence_summary
                        else 0
                    ),
                }
            )


__all__ = [
    "FastMCPFitToolClient",
    "FitAgent",
    "FitAgentError",
    "FitToolDiscoveryError",
    "FitToolInvocationError",
]
