# Neu.Tail Mission 4 — Governed Retail Agent System

See [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md) for the as-built component design,
runtime flows, tool permissions, persistence model, observability, and known
demo limitations.

See
[MISSION4_SEQUENCE_IMPLEMENTATION_DESIGN.md](MISSION4_SEQUENCE_IMPLEMENTATION_DESIGN.md)
for the proposed high-level and low-level changes needed to support the three
customer profiling, discovery/Upsell, and post-delivery Fit sequences with
NeutailUI.

This slice implements Profiling, Discovery, Size & Fit, and governed Service
Upsell Agents. Profile builds and caches `CustomerContext`; Discovery consumes that
context, uses an exact four-tool FastMCP scope, supports structured, semantic,
similar-item, and hybrid retrieval, filters live inventory and hard constraints,
then applies explainable segment-aware ranking. Fit combines exact product,
brand, category, return, exchange, inventory, and vector-retrieved outcome
evidence to produce deterministic size guidance and fit-risk signals. Upsell
consumes orchestrator-owned Discovery/Fit signals, applies deterministic
eligibility and suppression policy, scores only eligible opportunities, and
uses the LLM Gateway only to word an already-selected optional offer.

The LangGraph orchestrator adds identity validation, multi-turn session state,
hybrid intent detection, runtime capability discovery, deterministic agent
planning, structured invocation, response synthesis, and trace propagation.
All four specialists execute end to end. Discovery and Fit never invoke Upsell
directly: they publish typed signals and the orchestrator owns the handoff.

The profiling demo also includes a durable purchase-event workflow. A completed
purchase is idempotently recorded, evaluated against a versioned 90-day
loyalty policy, written to segment history and a transactional outbox, and then
used to invalidate and refresh the Profiling Agent context. LangGraph owns the
workflow and LangSmith can trace each stage.

The Shared Context Bus is simulated with the same SQLite database rather than
an external broker. Domain services commit an `outbox_events` row with their
business change; the in-process dispatcher records one idempotent
`outbox_deliveries` row per subscriber and marks the event published only after
all registered subscribers complete. It drains immediately for API responses
and continues polling pending rows in the FastAPI lifespan for restart
recovery. Current subscribers invalidate customer/profile context and route
durable high-product-engagement signals to the Upsell Agent.

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
export NEUTAIL_DEMO_PASSWORD="demo"
export NEUTAIL_INTERNAL_EVENT_TOKEN="replace-with-a-long-random-internal-token"
```

Login using any seeded customer email and the configured demo password:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/auth/login" \
  -H "content-type: application/json" \
  -d '{"email":"olivia.hart1@demo.neutail.local","password":"demo"}'
```

### Customer profiling and segmentation demo

Reset the two dedicated demo customers before presenting the sequence:

```bash
./bin/python -m database.seed_profile_demo
```

| Customer | Email | Starting facts | Third-purchase result |
| --- | --- | --- | --- |
| Alice Morgan | `alice.demo@demo.neutail.local` | Affluent, New, 2 purchases | Loyal, Prestige Champion |
| Bob Reed | `bob.demo@demo.neutail.local` | Less Affluent, New, 2 purchases | Loyal, Value Defender |

Sign in with the shared `demo` password, add any active product to the cart,
and select **Complete demo purchase**. The browser calls
`POST /api/v1/demo/checkout`; the returned committed transition is displayed in
the cart, while Home and Profile reload the new segment. Retrying an identical
checkout is safe because the UI reuses an idempotency key.

The flow intentionally does not award or multiply points. The response carries
`points_transaction_id=null` and `points_delta=null`, and the UI says points are
unchanged. This prevents the demo from claiming a loyalty write that did not
occur.

For trusted server-to-server ingestion, submit the versioned
`PURCHASE_COMPLETED` envelope to `POST /internal/v1/events` with
`X-Internal-Token`; query its durable status at
`GET /internal/v1/events/{event_id}`. Browser code must not use this credential.

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

The Home screen loads authenticated, in-stock recommendations from the
customer's strongest purchase/category affinities and profile preferences:

```bash
curl "http://127.0.0.1:8000/api/v1/recommendations/home?limit=8" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}"
```

This typed route invokes Profiling and Discovery directly through the
orchestrator without chat intent classification. Discovery retains ownership
of deterministic personalization, hard constraints, and inventory filtering.

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

Ask for service support through the same route:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/chat" \
  -H "content-type: application/json" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -d '{"session_id":"demo-session","message":"Can I get styling support?"}'
```

The response includes `upsell_result`. With no qualifying evidence it is a
deterministic `NO_OFFER` and makes no model call. A qualifying
`HIGH_PRODUCT_ENGAGEMENT` or `CHRONIC_FIT_RISK` specialist signal is routed by
the orchestrator to the Upsell Agent. The agent calls `evaluate_upsell` through
its FastMCP scope, then—only for an eligible result—selects an offer, invokes
the `upsell_message/v1` gateway prompt, and records `OFFER_SHOWN`. It never
records acceptance without an explicit customer action.

### UI-driven engagement and offer response

The UI can submit product views to `POST /api/v1/engagement/events`. Customer
identity comes from the bearer token; the backend resolves the SKU and decides
whether it is a premium product. For the seeded demo, `SKU00006` is premium.
Submit the same request three times with a new idempotency key each time:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/engagement/events" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -H "content-type: application/json" \
  -d "{\"session_id\":\"demo-session\",\"event_type\":\"PRODUCT_VIEWED\",\"sku\":\"SKU00006\",\"idempotency_key\":\"demo-session-SKU00006-view-1\",\"metadata\":{\"source\":\"PRODUCT_DETAIL\"}}"
```

Change the final `view-1` suffix to `view-2` and `view-3` for the next two
calls. The third view returns a `HIGH_PRODUCT_ENGAGEMENT` trigger and a governed
`upsell_result` when the customer is eligible. Product availability is checked
before a view is counted. The view receipt, context-bus delivery, and actionable
decision are durable and idempotent; the signal source is
`EngagementService`, not the Discovery Agent.

Actionable offers can be restored after a page or API restart:

```bash
curl "http://127.0.0.1:8000/api/v1/upsell/decisions/pending" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}"
```

NeutailUI polls this endpoint and reconciles results by `decision_id`.

Only an explicit UI action may resolve that decision:

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/upsell/decisions/UPSELL_ID/events" \
  -H "authorization: Bearer ${NEUTAIL_DEMO_TOKEN}" \
  -H "content-type: application/json" \
  -d '{"session_id":"demo-session","event_type":"OFFER_ACCEPTED","idempotency_key":"UPSELL_ID-OFFER_ACCEPTED"}'
```

Replace `UPSELL_ID` with the returned decision identifier. Acceptance records
interest only; it never starts a trial, creates a subscription, or charges the
customer. `OFFER_DECLINED` and `OFFER_DISMISSED` are also supported. Replaying
the same request with the same idempotency key returns the original response;
reusing a key for a different payload returns `409`.

## LangSmith

Export the values shown in `.env.example` before starting Uvicorn. LangGraph
runs, individual MCP tool calls, segmentation, latency, session ID, customer ID,
and trace ID will then appear under the configured LangSmith project. Profiling
and all Fit decisions remain deterministic and make no required LLM call.

For local debugging, set `NEUTAIL_LLM_TRACE_BODIES=true` to include the rendered
messages and final model content on each `litellm.acompletion` trace. Leave it
disabled outside controlled development because prompts can contain customer
context and conversation data. Restart the API after changing the flag.

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
`llm_gateway/prompts/`. Development routes use NVIDIA NIM through LiteLLM;
primary and fallback models are configured independently per use case. Set
`NVIDIA_NIM_API_KEY` and leave
`NVIDIA_NIM_API_BASE=https://integrate.api.nvidia.com/v1`. The Profiling Agent
does not use this gateway because
its segment classification is intentionally deterministic. The orchestrator
uses deterministic rules for clear intent and the gateway's `intent/v3` prompt
for ambiguity. Set `NEUTAIL_RESPONSE_SYNTHESIS_LLM=true` to verbalize structured
results through the governed `response_synthesis` route; otherwise it uses
deterministic response templates.

Token limits, timeouts, and optional reasoning controls are governed per route.
Separate `reasoning_content` is never used as customer-facing output.

Discovery ranking never uses an LLM. Set
`NEUTAIL_DISCOVERY_EXPLANATIONS_LLM=true` only to add governed explanations to
the already-ranked products; an explanation failure leaves the recommendations
intact.

Fit sizing and risk calculation also never use an LLM. Set
`NEUTAIL_FIT_EXPLANATIONS_LLM=true` only to verbalize the completed decision;
provider failure leaves the structured Fit result intact.

Upsell eligibility, suppression, opportunity scoring, and offer selection are
always deterministic. The `upsell_message` route can only word the immutable
selected offer. A provider failure preserves `OFFER_AVAILABLE` with
`message=null`; `NO_OFFER` never invokes the gateway.

## Verify

```bash
LANGSMITH_TRACING=false ./bin/python -m pytest -q
```

Start the development API with `.env` loaded:

```bash
./bin/uvicorn api.main:app --reload --env-file .env --log-level debug
```
