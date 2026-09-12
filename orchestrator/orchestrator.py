"""LangGraph control plane for Neu.Tail agent coordination."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Optional

from fastmcp import Client, FastMCP
from langgraph.graph import END, START, StateGraph
from langsmith import trace, traceable
from pydantic import BaseModel

from llm_gateway import LLMGateway, LLMGatewayError
from llm_gateway.models import IntentResult
from orchestrator.agent_registry import (
    AgentDependencyError,
    AgentRegistry,
    AgentUnavailableError,
)
from orchestrator.intent import IntentDetector
from orchestrator.models import (
    AgentRunStatus,
    ConversationTurn,
    ExecutionPlan,
    NeuTailState,
    OrchestratorRequest,
    OrchestratorResponse,
    SessionContext,
)
from services.session_context_service import SessionContextService
from tools.permissions import AgentName
from tools.registry import TOOL_REGISTRY, ToolRegistry


class OrchestratorError(RuntimeError):
    """Base exception for control-plane failures."""


class OrchestratorCustomerNotFoundError(OrchestratorError, LookupError):
    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Customer '{customer_id}' was not found")


class OrchestratorDependencyError(OrchestratorError):
    """Raised when a mandatory control-plane capability is unavailable."""


class FastMCPIdentityValidator:
    """Validate identity through the trusted MCP registry, never through SQL."""

    def __init__(
        self,
        server_factory: Callable[[], FastMCP] = lambda: TOOL_REGISTRY.build_server(),
    ) -> None:
        self._server_factory = server_factory

    async def exists(self, customer_id: str) -> bool:
        with trace(
            name="orchestrator.validate_customer",
            run_type="tool",
            inputs={"customer_id": customer_id},
            tags=["orchestrator", "fastmcp", "identity"],
        ) as run:
            try:
                async with Client(self._server_factory()) as client:
                    result = await client.call_tool(
                        "customer_exists", {"customer_id": customer_id}
                    )
                value = self._extract_bool(result.data, result.structured_content)
            except Exception as exc:
                run.end(error=type(exc).__name__)
                raise OrchestratorDependencyError(
                    "Customer identity validation is unavailable"
                ) from exc
            run.end(outputs={"exists": value})
            return value

    @staticmethod
    def _extract_bool(data: Any, structured: Any) -> bool:
        if isinstance(data, bool):
            return data
        if isinstance(structured, bool):
            return structured
        if isinstance(structured, dict):
            for key in ("result", "value"):
                if isinstance(structured.get(key), bool):
                    return structured[key]
        raise OrchestratorDependencyError(
            "Customer identity tool returned an invalid response"
        )


class ResponseSynthesizer:
    """Verbalize structured outputs, with a deterministic no-provider fallback."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        llm_enabled: Optional[bool] = None,
    ) -> None:
        self.gateway = gateway
        self.llm_enabled = (
            os.getenv("NEUTAIL_RESPONSE_SYNTHESIS_LLM", "false").casefold()
            == "true"
            if llm_enabled is None
            else llm_enabled
        )

    async def synthesize(self, state: NeuTailState) -> str:
        fallback = self._deterministic_response(state)
        if not self.llm_enabled:
            return fallback

        request = state["request"]
        intent = state["intent_result"]
        try:
            response = await self.gateway.invoke(
                use_case="response_synthesis",
                context={
                    "user_message": request.message,
                    "intent": intent.model_dump(mode="json"),
                    "customer_context": _jsonable(state.get("customer_context")),
                    "agent_outputs": _jsonable(state.get("agent_outputs", {})),
                    "errors": state.get("errors", []),
                },
                agent_name="Orchestrator",
                trace_id=request.trace_id,
                session_id=request.session_id,
            )
        except (LLMGatewayError, LookupError):
            return fallback
        return response if isinstance(response, str) and response.strip() else fallback

    @staticmethod
    def _deterministic_response(state: NeuTailState) -> str:
        intent = state["intent_result"]
        context = state.get("customer_context")
        errors = state.get("errors", [])

        if intent.confidence < NeuTailOrchestrator.MIN_INTENT_CONFIDENCE:
            return (
                "Are you looking for a product recommendation, fit advice, "
                "styling support, or your customer profile?"
            )
        unavailable = {
            error.split(":", 1)[1]
            for error in errors
            if error.startswith("AGENT_UNAVAILABLE:")
        }
        if intent.intent == "CUSTOMER_CONTEXT" and context is not None:
            return (
                f"I loaded your profile. Your current Neu.Tail segment is "
                f"{context.segment}, with loyalty tier "
                f"{context.loyalty_tier or 'not available'}."
            )
        required_agent = {
            "PRODUCT_DISCOVERY": AgentName.DISCOVERY.value,
            "FIT_QUERY": AgentName.FIT.value,
            "SERVICE_QUERY": AgentName.UPSELL.value,
        }.get(intent.intent)
        if required_agent in unavailable:
            labels = {
                "PRODUCT_DISCOVERY": "product recommendations",
                "FIT_QUERY": "fit advice",
                "SERVICE_QUERY": "styling-service suggestions",
            }
            profile_note = (
                f" I did load your {context.segment} customer context."
                if context is not None
                else ""
            )
            return (
                f"The {labels[intent.intent]} capability is not available in "
                f"this demo slice yet.{profile_note}"
            )
        if errors:
            return "I could not complete that request because a required capability is unavailable."
        if context is not None:
            return f"I completed the request using your {context.segment} customer context."
        return "How can I help with product discovery, fit, or styling support?"


def _trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    if isinstance(request, OrchestratorRequest):
        return {
            "customer_id": request.customer_id,
            "session_id": request.session_id,
            "trace_id": request.trace_id,
            "message_characters": len(request.message),
        }
    return {"request_type": type(request).__name__}


def _trace_outputs(output: Any) -> dict[str, Any]:
    if isinstance(output, OrchestratorResponse):
        return {
            "trace_id": output.trace_id,
            "session_id": output.session_id,
            "intent": output.intent,
            "planned_agents": [item.value for item in output.execution_plan],
            "completed_agents": [item.value for item in output.completed_agents],
            "error_count": len(output.errors),
        }
    return {"output_type": type(output).__name__}


class NeuTailOrchestrator:
    """Coordinate sessions, intent, capabilities, agents, and response synthesis."""

    MIN_INTENT_CONFIDENCE = 0.65

    def __init__(
        self,
        *,
        agent_registry: Optional[AgentRegistry] = None,
        tool_registry: Optional[ToolRegistry] = None,
        llm_gateway: Optional[LLMGateway] = None,
        session_service: Optional[SessionContextService] = None,
        identity_validator: Optional[FastMCPIdentityValidator] = None,
        intent_detector: Optional[IntentDetector] = None,
        response_synthesizer: Optional[ResponseSynthesizer] = None,
    ) -> None:
        self.llm_gateway = llm_gateway or LLMGateway()
        self.agent_registry = agent_registry or AgentRegistry()
        self.tool_registry = tool_registry or TOOL_REGISTRY
        self.session_service = session_service or SessionContextService()
        self.identity_validator = identity_validator or FastMCPIdentityValidator()
        self.intent_detector = intent_detector or IntentDetector(self.llm_gateway)
        self.response_synthesizer = response_synthesizer or ResponseSynthesizer(
            self.llm_gateway
        )
        self.graph = self._build_graph()

    @traceable(
        name="neutail_orchestrator",
        run_type="chain",
        tags=["orchestrator", "control-plane", "langgraph"],
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    async def handle(
        self, request: OrchestratorRequest | dict[str, Any]
    ) -> OrchestratorResponse:
        if not isinstance(request, OrchestratorRequest):
            request = OrchestratorRequest.model_validate(request)
        result = await self.graph.ainvoke(
            {
                "request": request,
                "agent_outputs": {},
                "completed_agents": [],
                "errors": [],
            },
            {
                "run_name": "neutail_orchestrator_graph",
                "tags": ["orchestrator", "chat-turn"],
                "metadata": {
                    "customer_id": request.customer_id,
                    "session_id": request.session_id,
                    "trace_id": request.trace_id,
                },
            },
        )
        session = result["session"]
        intent = result["intent_result"]
        plan = result["execution_plan"]
        return OrchestratorResponse(
            trace_id=request.trace_id,
            session_id=request.session_id,
            response=result["final_response"],
            intent=intent.intent,
            intent_confidence=intent.confidence,
            execution_plan=plan.steps,
            completed_agents=result.get("completed_agents", []),
            extracted_entities=result.get("extracted_entities", {}),
            customer_context=result.get("customer_context"),
            agent_outputs=result.get("agent_outputs", {}),
            errors=result.get("errors", []),
            turn_count=session.turn_count,
        )

    def _build_graph(self) -> Any:
        builder = StateGraph(NeuTailState)
        builder.add_node("validate_identity", self._validate_identity)
        builder.add_node("load_context", self._load_context)
        builder.add_node("detect_intent", self._detect_intent)
        builder.add_node("merge_context", self._merge_context)
        builder.add_node("discover_capabilities", self._discover_capabilities)
        builder.add_node("build_plan", self._build_plan)
        builder.add_node("execute_plan", self._execute_plan)
        builder.add_node("synthesize_response", self._synthesize_response)
        builder.add_node("persist_context", self._persist_context)

        builder.add_edge(START, "validate_identity")
        builder.add_edge("validate_identity", "load_context")
        builder.add_edge("load_context", "detect_intent")
        builder.add_edge("detect_intent", "merge_context")
        builder.add_edge("merge_context", "discover_capabilities")
        builder.add_edge("discover_capabilities", "build_plan")
        builder.add_edge("build_plan", "execute_plan")
        builder.add_edge("execute_plan", "synthesize_response")
        builder.add_edge("synthesize_response", "persist_context")
        builder.add_edge("persist_context", END)
        return builder.compile()

    async def _validate_identity(self, state: NeuTailState) -> dict[str, Any]:
        customer_id = state["request"].customer_id
        if not await self.identity_validator.exists(customer_id):
            raise OrchestratorCustomerNotFoundError(customer_id)
        return {}

    async def _load_context(self, state: NeuTailState) -> dict[str, Any]:
        request = state["request"]
        session = self.session_service.get_or_create(
            request.session_id, request.customer_id
        )
        update: dict[str, Any] = {"session": session}
        if session.customer_context is not None:
            update["customer_context"] = session.customer_context
        return update

    async def _detect_intent(self, state: NeuTailState) -> dict[str, Any]:
        return {"intent_result": await self.intent_detector.detect(state)}

    async def _merge_context(self, state: NeuTailState) -> dict[str, Any]:
        intent = state["intent_result"]
        session = state["session"].model_copy(deep=True)
        for field in ("category", "occasion", "selected_sku", "requested_size"):
            value = getattr(intent, field)
            if value is not None:
                setattr(session, field, value)
        return {
            "session": session,
            "extracted_entities": session.entity_context(),
        }

    async def _discover_capabilities(
        self, _state: NeuTailState
    ) -> dict[str, Any]:
        catalog = {
            descriptor.name: self.tool_registry.list_tools(descriptor.name)
            for descriptor in self.agent_registry.list_agents()
        }
        return {"tool_catalog": catalog}

    async def _build_plan(self, state: NeuTailState) -> dict[str, Any]:
        intent = state["intent_result"]
        if intent.confidence < self.MIN_INTENT_CONFIDENCE:
            return {
                "execution_plan": ExecutionPlan(
                    steps=[], reason="Intent confidence is below the routing threshold"
                )
            }

        requested_intents = [intent.intent, *intent.secondary_intents]
        needs_specialist = any(
            item != "GENERAL_QUERY" for item in requested_intents
        )
        steps: list[AgentName] = []
        if needs_specialist and state.get("customer_context") is None:
            steps.append(AgentName.PROFILING)

        route_map = {
            "PRODUCT_DISCOVERY": AgentName.DISCOVERY,
            "FIT_QUERY": AgentName.FIT,
            "SERVICE_QUERY": AgentName.UPSELL,
        }
        for item in requested_intents:
            routed_agent = route_map.get(item)
            if routed_agent is not None and routed_agent not in steps:
                steps.append(routed_agent)
        return {
            "execution_plan": ExecutionPlan(
                steps=steps,
                reason="Deterministic mapping from detected intent and cached context",
            )
        }

    async def _execute_plan(self, state: NeuTailState) -> dict[str, Any]:
        working = dict(state)
        outputs = dict(state.get("agent_outputs", {}))
        completed = list(state.get("completed_agents", []))
        errors = list(state.get("errors", []))

        for agent_name in state["execution_plan"].steps:
            if (
                agent_name is not AgentName.PROFILING
                and working.get("customer_context") is None
            ):
                errors.append(
                    f"DEPENDENCY_MISSING:{agent_name.value}:customer_context"
                )
                continue
            with trace(
                name=f"orchestrator.invoke.{agent_name.value}",
                run_type="chain",
                inputs={"agent": agent_name.value},
                tags=["orchestrator", "agent-invocation"],
                metadata={
                    "trace_id": state["request"].trace_id,
                    "session_id": state["request"].session_id,
                    "customer_id": state["request"].customer_id,
                    "agent_name": agent_name.value,
                },
            ) as run:
                try:
                    result = await self.agent_registry.invoke(
                        agent_name,
                        working,
                        state["tool_catalog"].get(agent_name, []),
                    )
                except AgentUnavailableError:
                    errors.append(f"AGENT_UNAVAILABLE:{agent_name.value}")
                    run.end(outputs={"status": "UNAVAILABLE"})
                    continue
                except AgentDependencyError as exc:
                    errors.append(f"AGENT_DEPENDENCY_ERROR:{agent_name.value}")
                    run.end(error=type(exc).__name__)
                    continue
                except Exception as exc:
                    errors.append(
                        f"AGENT_EXECUTION_ERROR:{agent_name.value}:{type(exc).__name__}"
                    )
                    run.end(error=type(exc).__name__)
                    continue

                if result.status is AgentRunStatus.SUCCESS:
                    completed.append(agent_name)
                elif result.error:
                    errors.append(f"AGENT_PARTIAL:{agent_name.value}:{result.error}")
                working.update(result.state_updates)
                outputs[agent_name.value] = result.state_updates
                run.end(outputs={"status": result.status.value})

        update = {
            "agent_outputs": outputs,
            "completed_agents": completed,
            "errors": errors,
        }
        for key in ("customer_context", "discovery_result", "fit_result", "upsell_result"):
            if key in working:
                update[key] = working[key]
        return update

    async def _synthesize_response(
        self, state: NeuTailState
    ) -> dict[str, Any]:
        return {
            "final_response": await self.response_synthesizer.synthesize(state)
        }

    async def _persist_context(self, state: NeuTailState) -> dict[str, Any]:
        request = state["request"]
        intent = state["intent_result"]
        session = state["session"].model_copy(deep=True)
        session.turn_count += 1
        session.last_intent = intent.intent
        session.customer_context = state.get("customer_context")
        session.conversation.extend(
            [
                ConversationTurn(
                    role="user",
                    content=request.message,
                    trace_id=request.trace_id,
                    intent=intent.intent,
                ),
                ConversationTurn(
                    role="assistant",
                    content=state["final_response"],
                    trace_id=request.trace_id,
                    intent=intent.intent,
                ),
            ]
        )
        return {"session": self.session_service.save_context(session)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "FastMCPIdentityValidator",
    "NeuTailOrchestrator",
    "OrchestratorCustomerNotFoundError",
    "OrchestratorDependencyError",
    "OrchestratorError",
    "ResponseSynthesizer",
]

