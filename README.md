# Neu.Tail Mission 4 — Orchestrator, Profile, Discovery, and Fit Agents

See [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for the as-built component design,
runtime flows, tool permissions, persistence model, observability, and known
demo limitations.

This slice implements the deterministic Profiling, Discovery, and Size & Fit
Agents. Profile builds and caches `CustomerContext`; Discovery consumes that
context, uses an exact four-tool FastMCP scope, supports structured, semantic,
similar-item, and hybrid retrieval, filters live inventory and hard constraints,
then applies explainable segment-aware ranking. Fit combines exact product,
brand, category, return, exchange, inventory, and vector-retrieved outcome
evidence to produce deterministic size guidance and fit-risk signals.

The LangGraph orchestrator adds identity validation, multi-turn session state,
hybrid intent detection, runtime capability discovery, deterministic agent
planning, structured invocation, response synthesis, and trace propagation.
Profile, Discovery, and Fit requests execute end to end. Upsell remains
separate and is reported as unavailable instead of having its decisions
duplicated in Discovery, Fit, or the orchestrator.

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

The UI header/profile panel can load its compact customer record with:

```bash
curl "http://127.0.0.1:8000/api/v1/customers/me/summary" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}"
```

Create a UI session before sending chat messages. The request body is optional;
its `channel` can be `web`, `mobile`, or `demo` and defaults to `web`:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/sessions" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -H "content-type: application/json" \
  -d '{"channel":"web"}'
```

Use the returned `session_id` for `/api/v1/chat`. Session metadata and sanitized
context are available at `GET /api/v1/sessions/{session_id}` and
`GET /api/v1/sessions/{session_id}/context`; close it with
`DELETE /api/v1/sessions/{session_id}`.

Browser requests are allowed from common local UI origins on ports 3000, 4173,
5173, and 8080. For another frontend origin, configure it before starting the
API:

```bash
export NEUTAIL_CORS_ORIGINS="http://localhost:4200"
```

Use exact comma-separated origins without path components. Deployed origins
must be explicitly configured; the API does not use a wildcard origin.

Inspect the current agent registry at `GET /api/v1/agents`. Reuse the same
`session_id` and customer token to demonstrate multi-turn context preservation.

Try hybrid product discovery through the same chat route:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -d '{"session_id":"demo-session","message":"Find me an elegant navy dress for a wedding under £500"}'
```

The response includes a structured `discovery_result` with the retrieval
strategy, ranked recommendations, score components/reason codes, counts, and
downstream signals. Inventory and exact price constraints are enforced outside
the vector index.

Ask for size guidance by supplying a selected SKU either in the message or in
the explicit UI field:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -d '{"session_id":"demo-session","message":"Will size 12 fit me?","selected_sku":"SKU00001"}'
```

The response includes a structured `fit_result` containing the requested and
recommended sizes, confidence, risk score/band, action, reason codes, evidence
counts, and downstream risk signals. The recommendation is calculated in code;
an LLM can only verbalize that fixed result.

## LangSmith

Export the values shown in `.env.example` before starting Uvicorn. LangGraph
runs, individual MCP tool calls, segmentation, latency, session ID, customer ID,
and trace ID will then appear under the configured LangSmith project. Profiling
and all Fit decisions remain deterministic and make no required LLM call.

## LLM Gateway

All conversational model calls go through `LLMGateway.invoke()`. Agents provide
a business use case such as `intent_detection` or `discovery_explanation`; the
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
`llm_gateway/prompts/`. Development routes use NVIDIA NIM through LiteLLM:
Z.ai GLM-5.3 is the primary model for every use case, with NVIDIA-hosted
OpenAI GPT-OSS-20B as the fallback. Set `NVIDIA_NIM_API_KEY` and leave
`NVIDIA_NIM_API_BASE=https://integrate.api.nvidia.com/v1`. The Profiling Agent
does not use this gateway because
its segment classification is intentionally deterministic. The orchestrator
uses deterministic rules for clear intent and the gateway's `intent/v3` prompt
for ambiguity. Set `NEUTAIL_RESPONSE_SYNTHESIS_LLM=true` to verbalize structured
results through the governed `response_synthesis` route; otherwise it uses
deterministic response templates.

Discovery ranking never uses an LLM. Set
`NEUTAIL_DISCOVERY_EXPLANATIONS_LLM=true` only to add governed explanations to
the already-ranked products; an explanation failure leaves the recommendations
intact.

Fit sizing and risk calculation also never use an LLM. Set
`NEUTAIL_FIT_EXPLANATIONS_LLM=true` only to verbalize the completed decision;
provider failure leaves the structured Fit result intact.

## Verify

```bash
LANGSMITH_TRACING=false ./bin/python -m pytest -q
```

Start the development API with `.env` loaded:

```bash
./bin/uvicorn api.main:app --reload --env-file .env --log-level debug
```
