"""Business-facing Neu.Tail gateway backed only by LiteLLM."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from typing import Any, Optional, TypeVar

# LiteLLM is the concrete provider abstraction for every default gateway call.
# Use its bundled model map so importing the demo does not fetch configuration.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm

from langsmith import get_current_run_tree, trace, traceable
from pydantic import BaseModel, ValidationError

from llm_gateway.models import (
    CompletionOptions,
    LLMCallRecord,
    LLMCallStatus,
    ModelRoute,
    PromptDefinition,
    TokenUsage,
)
from llm_gateway.prompt_registry import PromptRegistry
from llm_gateway.router import ModelRouter, provider_from_model
from llm_gateway.telemetry import GatewayTelemetry


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
CompletionCallable = Callable[..., Awaitable[Any]]
TRACE_BODIES_ENV = "NEUTAIL_LLM_TRACE_BODIES"


class LLMGatewayError(RuntimeError):
    """Base gateway exception."""


class StructuredOutputError(LLMGatewayError):
    """Raised when model text does not satisfy the requested Pydantic model."""


class LLMInvocationError(LLMGatewayError):
    """Raised after all governed primary/fallback attempts fail."""

    def __init__(self, use_case: str, records: list[LLMCallRecord]) -> None:
        self.use_case = use_case
        self.records = records
        statuses = ", ".join(record.status.value for record in records)
        super().__init__(
            f"LLM invocation failed for use case '{use_case}' ({statuses})"
        )


def _trace_gateway_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    context = inputs.get("context")
    response_model = inputs.get("response_model")
    return {
        "use_case": inputs.get("use_case"),
        "agent_name": inputs.get("agent_name"),
        "trace_id": inputs.get("trace_id"),
        "session_id": inputs.get("session_id"),
        "prompt_version": inputs.get("prompt_version"),
        "context_keys": sorted(context) if isinstance(context, dict) else [],
        "response_model": getattr(response_model, "__name__", None),
    }


def _trace_gateway_output(output: Any) -> dict[str, Any]:
    return {
        "output_type": type(output).__name__,
        "structured": isinstance(output, BaseModel),
    }


class LLMGateway:
    """Resolve, render, invoke, validate, account, and trace every model call."""

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        prompt_registry: Optional[PromptRegistry] = None,
        telemetry: Optional[GatewayTelemetry] = None,
        completion: Optional[CompletionCallable] = None,
        trace_bodies_enabled: Optional[bool] = None,
    ) -> None:
        self.router = router or ModelRouter.from_yaml()
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.telemetry = telemetry or GatewayTelemetry()
        self._completion = completion or litellm.acompletion
        self.trace_bodies_enabled = (
            os.getenv(TRACE_BODIES_ENV, "false").casefold() == "true"
            if trace_bodies_enabled is None
            else trace_bodies_enabled
        )
        self.completion_backend = (
            "litellm.acompletion"
            if completion is None
            else f"injected:{getattr(completion, '__name__', type(completion).__name__)}"
        )

    @traceable(
        name="llm_gateway.invoke",
        run_type="chain",
        tags=["llm-gateway", "controlled-model-entry-point"],
        process_inputs=_trace_gateway_inputs,
        process_outputs=_trace_gateway_output,
    )
    async def invoke(
        self,
        use_case: str,
        context: dict[str, Any],
        agent_name: str,
        trace_id: str,
        prompt_version: Optional[str] = None,
        response_model: Optional[type[StructuredModel]] = None,
        session_id: Optional[str] = None,
    ) -> str | StructuredModel:
        """Invoke a logical use case without exposing provider details to agents."""

        route = self.router.resolve(_required_text(use_case, "use_case"))
        if response_model is not None and not (
            isinstance(response_model, type) and issubclass(response_model, BaseModel)
        ):
            raise TypeError("response_model must be a Pydantic BaseModel class")
        prompt = self.prompt_registry.load(
            route.prompt_group,
            prompt_version or route.default_prompt_version,
        )
        messages = prompt.render(context)
        if response_model is not None:
            messages = _with_structured_output_instruction(messages, response_model)

        normalized_agent = _required_text(agent_name, "agent_name")
        normalized_trace = _required_text(trace_id, "trace_id")
        normalized_session = session_id.strip() if session_id else None
        self._set_parent_metadata(
            route, prompt, normalized_agent, normalized_trace, normalized_session
        )

        call_records: list[LLMCallRecord] = []
        candidates = [route.primary_model]
        if route.policy.allow_fallback and route.fallback_model:
            candidates.append(route.fallback_model)

        attempt_number = 0
        for candidate_index, model in enumerate(candidates):
            structured_attempts = 1 + (
                route.policy.structured_output_retries
                if response_model is not None
                else 0
            )
            for structured_attempt in range(structured_attempts):
                attempt_number += 1
                result, record, error = await self._attempt(
                    route=route,
                    prompt=prompt,
                    messages=messages,
                    model=model,
                    model_options=(
                        route.fallback_options
                        if candidate_index > 0
                        else route.primary_options
                    ),
                    attempt=attempt_number,
                    is_fallback=candidate_index > 0,
                    response_model=response_model,
                    agent_name=normalized_agent,
                    trace_id=normalized_trace,
                    session_id=normalized_session,
                )
                call_records.append(record)
                if error is None:
                    return result
                if not isinstance(error, StructuredOutputError):
                    break
                if structured_attempt + 1 >= structured_attempts:
                    break

        raise LLMInvocationError(route.use_case, call_records)

    async def _attempt(
        self,
        *,
        route: ModelRoute,
        prompt: PromptDefinition,
        messages: list[dict[str, str]],
        model: str,
        model_options: CompletionOptions,
        attempt: int,
        is_fallback: bool,
        response_model: Optional[type[StructuredModel]],
        agent_name: str,
        trace_id: str,
        session_id: Optional[str],
    ) -> tuple[Any, LLMCallRecord, Optional[Exception]]:
        provider = provider_from_model(model)
        started = perf_counter()
        response: Any = None
        content: Optional[str] = None
        usage = TokenUsage()
        status = LLMCallStatus.FAILED
        error: Optional[Exception] = None
        result: Any = None

        trace_inputs: dict[str, Any] = {
            "message_count": len(messages),
            "prompt_characters": sum(len(item["content"]) for item in messages),
        }
        log_request_body = (
            route.policy.log_request_body or self.trace_bodies_enabled
        )
        log_response_body = (
            route.policy.log_response_body or self.trace_bodies_enabled
        )
        if log_request_body:
            trace_inputs["messages"] = messages

        with trace(
            name="litellm.acompletion",
            run_type="llm",
            inputs=trace_inputs,
            tags=["litellm", "fallback" if is_fallback else "primary"],
            metadata={
                "trace_id": trace_id,
                "session_id": session_id,
                "agent_name": agent_name,
                "use_case": route.use_case,
                "prompt_name": prompt.name,
                "prompt_version": prompt.version,
                "logical_model": route.logical_model,
                "ls_provider": provider,
                "ls_model_name": model,
                "body_logging_enabled": self.trace_bodies_enabled,
            },
        ) as run:
            try:
                kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": route.policy.temperature,
                    "max_tokens": route.policy.max_tokens,
                    "timeout": route.policy.timeout_seconds,
                    "drop_params": True,
                }
                kwargs.update(_completion_option_kwargs(model_options))
                if response_model is not None:
                    kwargs["response_format"] = response_model
                response = await asyncio.wait_for(
                    self._completion(**kwargs),
                    timeout=route.policy.timeout_seconds,
                )
                usage = _response_usage(response)
                parsed = _response_parsed(response, response_model)
                if parsed is not None:
                    result = parsed
                    content = parsed.model_dump_json()
                else:
                    content = _response_content(response)
                    result = (
                        _parse_structured(content, response_model)
                        if response_model is not None
                        else content
                    )
                status = (
                    LLMCallStatus.FALLBACK_SUCCESS
                    if is_fallback
                    else LLMCallStatus.SUCCESS
                )
            except (ValidationError, json.JSONDecodeError, StructuredOutputError) as exc:
                error = StructuredOutputError(
                    f"Response from '{model}' did not match "
                    f"{getattr(response_model, '__name__', 'the output schema')}"
                )
                error.__cause__ = exc
                status = LLMCallStatus.INVALID_OUTPUT
                if response is not None:
                    usage = _response_usage(response)
            except Exception as exc:  # provider SDK exceptions are normalized here
                error = exc
                status = LLMCallStatus.FAILED

            latency_ms = max(int((perf_counter() - started) * 1000), 0)
            record = LLMCallRecord(
                trace_id=trace_id,
                session_id=session_id,
                agent_name=agent_name,
                use_case=route.use_case,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                logical_model=route.logical_model,
                provider=provider,
                provider_model=model,
                attempt=attempt,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                latency_ms=latency_ms,
                status=status,
                estimated_cost_usd=_response_cost(response),
                error_type=type(error).__name__ if error else None,
            )
            self.telemetry.record(record)

            trace_outputs = {
                "response_type": type(result).__name__ if error is None else None,
                "response_characters": len(content or ""),
                "usage_metadata": usage.model_dump(),
                "status": status.value,
            }
            if log_response_body and content is not None:
                trace_outputs["content"] = content
            if error is None:
                run.end(outputs=trace_outputs)
            else:
                run.end(outputs=trace_outputs, error=str(error))
        return result, record, error

    @staticmethod
    def _set_parent_metadata(
        route: ModelRoute,
        prompt: PromptDefinition,
        agent_name: str,
        trace_id: str,
        session_id: Optional[str],
    ) -> None:
        current = get_current_run_tree()
        if current is not None:
            current.metadata.update(
                {
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "agent_name": agent_name,
                    "use_case": route.use_case,
                    "prompt_name": prompt.name,
                    "prompt_version": prompt.version,
                    "logical_model": route.logical_model,
                }
            )


def _required_text(value: Any, field_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{field_name} must be a non-empty string")


def _with_structured_output_instruction(
    messages: list[dict[str, str]], response_model: type[BaseModel]
) -> list[dict[str, str]]:
    result = [dict(message) for message in messages]
    schema = json.dumps(response_model.model_json_schema(), sort_keys=True)
    result[0]["content"] += (
        "\nReturn only valid JSON matching this schema. Do not add markdown fences:\n"
        + schema
    )
    return result


def _response_content(response: Any) -> str:
    choices = _value(response, "choices")
    if not choices:
        raise LLMGatewayError("LiteLLM returned no choices")
    message = _value(choices[0], "message")
    content = _value(message, "content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = [
            _value(item, "text")
            for item in content
            if isinstance(_value(item, "text"), str)
        ]
        combined = "".join(parts).strip()
        if combined:
            return combined
    finish_reason = _value(choices[0], "finish_reason")
    reasoning = _value(
        message,
        "reasoning_content",
        _value(message, "reasoning"),
    )
    raise LLMGatewayError(
        "LiteLLM returned empty message content "
        f"(finish_reason={finish_reason!r}, "
        f"reasoning_content_present={bool(reasoning)})"
    )


def _completion_option_kwargs(options: CompletionOptions) -> dict[str, Any]:
    """Forward reasoning options that LiteLLM's NVIDIA mapper omits."""

    extra_body: dict[str, Any] = {}
    if options.reasoning_effort is not None:
        extra_body["reasoning_effort"] = options.reasoning_effort
    if options.clear_thinking is not None:
        extra_body["chat_template_kwargs"] = {
            "clear_thinking": options.clear_thinking
        }
    return {"extra_body": extra_body} if extra_body else {}


def _response_usage(response: Any) -> TokenUsage:
    usage = _value(response, "usage")
    return TokenUsage(
        input_tokens=int(
            _value(usage, "prompt_tokens", _value(usage, "input_tokens", 0)) or 0
        ),
        output_tokens=int(
            _value(
                usage,
                "completion_tokens",
                _value(usage, "output_tokens", 0),
            )
            or 0
        ),
        total_tokens=int(_value(usage, "total_tokens", 0) or 0),
    )


def _response_cost(response: Any) -> Optional[float]:
    if response is None:
        return None
    hidden = _value(response, "_hidden_params", {}) or {}
    value = _value(hidden, "response_cost")
    try:
        return max(float(value), 0.0) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_structured(
    content: str,
    response_model: type[StructuredModel],
) -> StructuredModel:
    normalized = content.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        normalized = "\n".join(lines).strip()
    return response_model.model_validate_json(normalized)


def _response_parsed(
    response: Any,
    response_model: Optional[type[StructuredModel]],
) -> Optional[StructuredModel]:
    if response_model is None:
        return None
    choices = _value(response, "choices") or []
    message = _value(choices[0], "message") if choices else None
    parsed = _value(message, "parsed")
    return parsed if isinstance(parsed, response_model) else None


def _value(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


__all__ = [
    "CompletionCallable",
    "LLMGateway",
    "LLMGatewayError",
    "LLMInvocationError",
    "StructuredOutputError",
    "TRACE_BODIES_ENV",
]
