"""LangGraph workflow for trusted purchase-completed domain events."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith import traceable

from agents.profiling import ProfileAgent, ProfileAgentRequest
from models.dto import CustomerContext
from models.events import PurchaseCompletedEvent, PurchaseEventResult
from services.commerce_event_service import CommerceEventService
from services.context_bus import ContextBusDispatcher
from services.session_context_service import SessionContextService
from tools.runtime import get_runtime


class PurchaseEventState(TypedDict, total=False):
    event: PurchaseCompletedEvent
    trace_id: str
    result: PurchaseEventResult
    customer_context: CustomerContext
    invalidated_sessions: int
    published_outbox_events: int


def _trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    event = inputs.get("event")
    if isinstance(event, PurchaseCompletedEvent):
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "customer_id": event.customer_id,
            "order_id": event.data.order_id,
        }
    return {"event_type": type(event).__name__}


def _trace_outputs(output: Any) -> dict[str, Any]:
    if isinstance(output, PurchaseEventResult):
        return {
            "event_id": output.event_id,
            "customer_id": output.customer_id,
            "purchase_count_90d": output.purchase_count_90d,
            "segment": output.transition.new_segment,
            "segment_changed": output.transition.changed,
            "replayed": output.replayed,
        }
    return {"output_type": type(output).__name__}


class PurchaseEventGraph:
    """Route typed purchase events without conversational intent detection."""

    def __init__(
        self,
        *,
        profile_agent: ProfileAgent,
        session_service: SessionContextService,
        context_bus: ContextBusDispatcher,
    ) -> None:
        self.profile_agent = profile_agent
        self.session_service = session_service
        self.context_bus = context_bus
        self.graph = self._build_graph()

    @traceable(
        name="purchase_event_graph",
        run_type="chain",
        tags=["purchase-event", "profiling-agent", "deterministic"],
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    async def execute(
        self,
        event: PurchaseCompletedEvent | dict[str, Any],
        *,
        trace_id: str,
    ) -> PurchaseEventResult:
        if not isinstance(event, PurchaseCompletedEvent):
            event = PurchaseCompletedEvent.model_validate(event)
        state = await self.graph.ainvoke(
            {"event": event, "trace_id": trace_id},
            {
                "run_name": "purchase_event_workflow",
                "tags": ["domain-event", "purchase-completed"],
                "metadata": {
                    "event_id": event.event_id,
                    "customer_id": event.customer_id,
                    "trace_id": trace_id,
                },
            },
        )
        return state["result"]

    def _build_graph(self):
        builder = StateGraph(PurchaseEventState)
        builder.add_node("process_purchase", self._process_purchase)
        builder.add_node("dispatch_context_bus", self._dispatch_context_bus)
        builder.add_node("refresh_profile", self._refresh_profile)
        builder.add_edge(START, "process_purchase")
        builder.add_edge("process_purchase", "dispatch_context_bus")
        builder.add_edge("dispatch_context_bus", "refresh_profile")
        builder.add_edge("refresh_profile", END)
        return builder.compile()

    async def _process_purchase(
        self, state: PurchaseEventState
    ) -> dict[str, Any]:
        with get_runtime().session(write=True) as session:
            result = CommerceEventService(session).process_purchase(
                state["event"],
                trace_id=state["trace_id"],
            )
        return {"result": result}

    async def _dispatch_context_bus(
        self, state: PurchaseEventState
    ) -> dict[str, Any]:
        outbox_ids = state["result"].outbox_ids
        deliveries = await self.context_bus.dispatch(outbox_ids)
        invalidated = sum(
            int(
                subscribers.get("profile_context_projection", {}).get(
                    "invalidated_sessions", 0
                )
            )
            for subscribers in deliveries.values()
        )
        return {
            "invalidated_sessions": invalidated,
            "published_outbox_events": len(deliveries),
        }

    async def _refresh_profile(
        self, state: PurchaseEventState
    ) -> dict[str, Any]:
        event = state["event"]
        context = await self.profile_agent.execute(
            ProfileAgentRequest(
                customer_id=event.customer_id,
                session_id=f"purchase-event-{event.event_id}",
                trace_id=state["trace_id"],
                refresh=True,
            )
        )
        result = state["result"].model_copy(
            update={"customer_context": context}
        )
        return {"customer_context": context, "result": result}

__all__ = ["PurchaseEventGraph", "PurchaseEventState"]
