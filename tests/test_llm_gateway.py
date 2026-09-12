from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm  # noqa: E402
import pytest  # noqa: E402

from llm_gateway import (  # noqa: E402
    GatewayTelemetry,
    IntentResult,
    LLMCallStatus,
    LLMGateway,
    LLMInvocationError,
    ModelRouter,
    PromptRegistry,
)
from llm_gateway.router import UnknownUseCaseError  # noqa: E402


def _response(
    content: str,
    *,
    model: str = "test/model",
    input_tokens: int = 10,
    output_tokens: int = 5,
):
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content, parsed=None))
        ],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        _hidden_params={"response_cost": 0.001},
    )


def test_every_configured_route_resolves_a_versioned_prompt():
    router = ModelRouter.from_yaml()
    prompts = PromptRegistry()

    assert router.list_use_cases() == [
        "fit_explanation",
        "intent_detection",
        "optional_summary",
        "product_explanation",
        "response_synthesis",
        "upsell_message",
    ]
    for use_case in router.list_use_cases():
        route = router.resolve(use_case)
        prompt = prompts.load(route.prompt_group, route.default_prompt_version)
        assert prompt.version == route.default_prompt_version


def test_default_gateway_is_wired_to_litellm_acompletion():
    gateway = LLMGateway()

    assert gateway.completion_backend == "litellm.acompletion"
    assert gateway._completion is litellm.acompletion


def test_prompt_registry_supports_explicit_versions_and_required_context():
    prompt = PromptRegistry().load("intent", "v2")
    messages = prompt.render({"message": "I need help choosing my size"})

    assert prompt.name == "intent_detection"
    assert messages[0]["role"] == "system"
    assert "choosing my size" in messages[1]["content"]
    with pytest.raises(ValueError, match="Missing prompt context"):
        prompt.render({})


def test_router_rejects_unknown_use_case():
    with pytest.raises(UnknownUseCaseError):
        ModelRouter.from_yaml().resolve("uncontrolled_model_call")


def test_gateway_returns_validated_structured_output_and_accounts_tokens():
    received: list[dict] = []

    async def fake_completion(**kwargs):
        received.append(kwargs)
        return _response(
            '{"intent":"PRODUCT_DISCOVERY","category":"Dresses","confidence":0.92}',
            model=kwargs["model"],
        )

    async def scenario():
        telemetry = GatewayTelemetry()
        gateway = LLMGateway(telemetry=telemetry, completion=fake_completion)
        result = await gateway.invoke(
            use_case="intent_detection",
            context={"message": "Show me a dress"},
            agent_name="Orchestrator",
            trace_id="trace-structured",
            session_id="session-1",
            response_model=IntentResult,
        )
        return result, telemetry.records

    result, records = asyncio.run(scenario())
    assert isinstance(result, IntentResult)
    assert result.intent == "PRODUCT_DISCOVERY"
    assert received[0]["model"] == "gemini/gemini-2.5-flash"
    assert received[0]["response_format"] is IntentResult
    assert records[0].status is LLMCallStatus.SUCCESS
    assert records[0].total_tokens == 15
    assert records[0].session_id == "session-1"


def test_invalid_structured_output_is_retried_centrally():
    calls = 0

    async def fake_completion(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response("not json", model=kwargs["model"])
        return _response(
            '{"intent":"FIT_QUERY","confidence":0.8}', model=kwargs["model"]
        )

    async def scenario():
        gateway = LLMGateway(completion=fake_completion)
        result = await gateway.invoke(
            "intent_detection",
            {"message": "Will this fit me?"},
            "Orchestrator",
            "trace-retry",
            response_model=IntentResult,
        )
        return result, gateway.telemetry.records

    result, records = asyncio.run(scenario())
    assert result.intent == "FIT_QUERY"
    assert [record.status for record in records] == [
        LLMCallStatus.INVALID_OUTPUT,
        LLMCallStatus.SUCCESS,
    ]


def test_primary_provider_failure_uses_configured_fallback():
    async def fake_completion(**kwargs):
        if kwargs["model"].startswith("gemini/"):
            raise RuntimeError("provider unavailable")
        return _response("A concise eligible offer.", model=kwargs["model"])

    async def scenario():
        gateway = LLMGateway(completion=fake_completion)
        result = await gateway.invoke(
            use_case="upsell_message",
            context={
                "customer_context": {"segment": "Prestige Champion"},
                "shopping_intent": "wedding outfit",
                "eligible_offer": {"name": "Personal Styling"},
                "reason_codes": ["PREMIUM_STYLE_AFFINITY"],
            },
            agent_name="UpsellAgent",
            trace_id="trace-fallback",
        )
        return result, gateway.telemetry.records

    result, records = asyncio.run(scenario())
    assert result == "A concise eligible offer."
    assert [record.status for record in records] == [
        LLMCallStatus.FAILED,
        LLMCallStatus.FALLBACK_SUCCESS,
    ]
    assert records[-1].provider_model == "openai/gpt-5-mini"


def test_all_provider_failures_are_normalized():
    async def unavailable(**_kwargs):
        raise TimeoutError("provider timeout")

    async def scenario():
        gateway = LLMGateway(completion=unavailable)
        with pytest.raises(LLMInvocationError) as captured:
            await gateway.invoke(
                "optional_summary",
                {"conversation": []},
                "Orchestrator",
                "trace-failed",
            )
        return captured.value

    error = asyncio.run(scenario())
    assert len(error.records) == 2
    assert {record.status for record in error.records} == {LLMCallStatus.FAILED}


def test_telemetry_log_does_not_contain_raw_context(caplog):
    secret_message = "private-customer-message"

    async def fake_completion(**kwargs):
        return _response("GENERAL_QUERY", model=kwargs["model"])

    async def scenario():
        gateway = LLMGateway(completion=fake_completion)
        return await gateway.invoke(
            "intent_detection",
            {"message": secret_message},
            "Orchestrator",
            "trace-private",
        )

    with caplog.at_level(logging.INFO, logger="neutail.llm_gateway"):
        asyncio.run(scenario())
    assert secret_message not in caplog.text
    assert "trace-private" in caplog.text
