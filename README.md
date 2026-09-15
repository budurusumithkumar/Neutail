# Neu.Tail Mission 4 — Orchestrator and Profiling Agent

See [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for the as-built component design,
runtime flows, tool permissions, persistence model, observability, and known
demo limitations.

This slice implements the deterministic Profiling Agent and its FastAPI
boundary. The agent discovers exactly five read-only capabilities from its
FastMCP-scoped registry, enriches profile facts in a LangGraph workflow, derives
the Neu.Tail segment without an LLM, and caches the resulting `CustomerContext`
per customer/session.

The LangGraph orchestrator adds identity validation, multi-turn session state,
hybrid intent detection, runtime capability discovery, deterministic agent
planning, structured invocation, response synthesis, and trace propagation.
Profile requests execute end to end. Discovery, Fit, and Upsell are reported as
unavailable instead of duplicating their domain decisions in the orchestrator.

## Run

```bash
./bin/pip install -r requirements.txt
./bin/uvicorn api.main:app --reload
```

OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

Build a profile with either endpoint:

```bash
curl "http://127.0.0.1:8000/api/v1/customers/CUST001/profile?session_id=demo"

curl -X POST "http://127.0.0.1:8000/api/v1/profile" \
  -H "content-type: application/json" \
  -d '{"customer_id":"CUST001","session_id":"demo","trace_id":"demo-1"}'
```

Set `refresh=true` to rebuild cached context. The permitted MCP contracts can
be inspected at `/api/v1/agents/profiling/tools`.

## Authentication and orchestrated chat

The UI contract exposes login, current-user, and logout endpoints. The seeded
demo database has customer emails but no password hashes, so local login uses a
shared password from the environment. Configure both demo secrets before
starting the API:

```bash
export NEUTAIL_JWT_SECRET="replace-with-a-long-random-demo-secret"
export NEUTAIL_DEMO_PASSWORD="replace-with-a-demo-password"
```

Login using any seeded customer email and the configured demo password:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"olivia.hart1@demo.neutail.local","password":"replace-with-a-demo-password"}'
```

Use the response's `access_token` as the Bearer token for authenticated routes:

```bash
export NEUTAIL_DEMO_TOKEN="paste-login-access-token-here"

curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -H "x-request-id: demo-chat-1" \
  -d '{"session_id":"demo-session","message":"Show me my profile"}'
```

`GET /api/v1/auth/me` returns the authenticated customer, and
`POST /api/v1/auth/logout` revokes the presented token until its expiry. Login
and logout state are intentionally process-local demo behavior.

Inspect the current agent registry at `GET /api/v1/agents`. Reuse the same
`session_id` and customer token to demonstrate multi-turn context preservation.

## LangSmith

Export the values shown in `.env.example` before starting Uvicorn. LangGraph
runs, individual MCP tool calls, segmentation, latency, session ID, customer ID,
and trace ID will then appear under the configured LangSmith project. No model
provider key is needed because profiling is deterministic and makes no LLM call.

## LLM Gateway

All conversational model calls go through `LLMGateway.invoke()`. Agents provide
a business use case such as `intent_detection` or `product_explanation`; the
gateway owns prompt versioning, provider routing, structured-output validation,
fallback, token/cost accounting, and sanitized LangSmith traces.

```python
from llm_gateway import IntentResult, LLMGateway

gateway = LLMGateway()
intent = await gateway.invoke(
    use_case="intent_detection",
    context={"message": "Find a navy wedding dress"},
    agent_name="Orchestrator",
    trace_id="demo-trace-1",
    session_id="demo-session",
    response_model=IntentResult,
)
```

Routes and policies are in `llm_gateway/config/models.yaml`; prompts are in
`llm_gateway/prompts/`. Configure only the provider keys needed by the routes
you plan to demonstrate. The Profiling Agent does not use this gateway because
its segment classification is intentionally deterministic. The orchestrator
uses deterministic rules for clear intent and the gateway's `intent/v3` prompt
for ambiguity. Set `NEUTAIL_RESPONSE_SYNTHESIS_LLM=true` to verbalize structured
results through the governed `response_synthesis` route; otherwise it uses
deterministic response templates.

## Verify

```bash
LANGSMITH_TRACING=false ./bin/python -m pytest -q
```
