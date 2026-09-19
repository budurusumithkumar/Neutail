# Neu.Tail System Design

## 1. Purpose and current scope

Neu.Tail is a demo retail-assistant backend organized around agent-scoped tools,
deterministic domain services, and a controlled LLM gateway. The currently
executable application is a vertical slice for customer profiling, product
discovery, size-and-fit guidance, and governed service offers:

- FastAPI exposes health, profile construction, and profile-tool discovery.
- An authenticated Home recommendation route runs typed Profiling and Discovery
  orchestration without fabricating a chat turn or invoking intent detection.
- A LangGraph orchestrator validates identity, manages multi-turn state,
  detects intent, discovers capabilities, plans agents, and synthesizes one
  response.
- A LangGraph `ProfileAgent` builds a normalized `CustomerContext`.
- `DiscoveryAgent` consumes that context, selects structured, semantic,
  similar-item, or hybrid retrieval, and ranks candidates deterministically.
- `FitAgent` combines exact, brand, category, return, exchange, inventory, and
  vector-retrieved evidence, then calculates size guidance and risk in code.
- `UpsellAgent` consumes orchestrator-routed triggers, calls deterministic
  eligibility policy through FastMCP, scores only eligible opportunities, and
  uses the LLM Gateway only for optional customer-facing wording.
- Each implemented agent discovers and calls only its exact permitted FastMCP
  tool set.
- Tool adapters call deterministic domain services backed by SQLAlchemy and a
  seeded SQLite database.
- LangSmith instruments API-triggered agent runs and individual tool calls.
- Replaceable embedding providers and vector stores live behind product and Fit
  retrieval services; inventory remains outside both indexes.
- A LiteLLM-based gateway handles ambiguous orchestrator intent and optional
  response, Discovery, and Fit prose; segmentation, product ranking, sizing,
  and fit-risk decisions never use an LLM.

Discovery publishes ranked product data and engagement signals through the
orchestrator but never calculates fit or makes service-eligibility decisions.
Fit owns sizing and fit risk without invoking Discovery or Upsell. The
orchestrator alone converts their downstream signals into Upsell requests.
The authentication boundary issues and validates signed demo JWTs, while token
revocation and session context remain process-local.

## 2. Design goals

1. Keep factual data access separate from agent reasoning.
2. Prevent agents from using SQL or domain services directly.
3. Expose typed, discoverable tools with static per-agent permissions.
4. Keep customer segmentation deterministic and explainable.
5. Route all future model calls through one governed gateway.
6. Make each request, graph run, and tool invocation traceable.
7. Remain simple enough to run as a single-process demo.

## 3. System context

```mermaid
flowchart LR
    User[Demo client / Web UI] -->|HTTP JSON| API[FastAPI agent API]
    API --> O[LangGraph orchestrator]
    O --> PA[LangGraph ProfileAgent]
    O --> DA[DiscoveryAgent]
    O --> FA[FitAgent]
    O --> UA[UpsellAgent]
    O -->|identity and capability discovery| MCP
    PA -->|MCP discovery and calls| MCP[Agent-scoped FastMCP server]
    DA -->|MCP discovery and calls| MCP
    FA -->|MCP discovery and calls| MCP
    UA -->|MCP discovery and calls| MCP
    MCP --> DS[Domain services]
    DS --> ORM[SQLAlchemy ORM]
    ORM --> DB[(Seeded SQLite database)]

    O -->|ambiguous intent / optional synthesis| GW[LLM Gateway]
    DA -->|optional ranked-product explanation| GW
    FA -->|optional fixed-decision explanation| GW
    UA -->|eligible-offer wording only| GW
    GW --> Lite[LiteLLM]
    Lite -.-> Providers[NVIDIA NIM models]

    API -. request metadata .-> LS[LangSmith]
    PA -. graph and tool traces .-> LS
    DA -. retrieval and ranking traces .-> LS
    FA -. evidence, retrieval, and decision traces .-> LS
    UA -. policy, scoring, selection, and wording traces .-> LS
    GW -. sanitized LLM traces .-> LS
```

Solid lines are active runtime paths. Dashed lines represent observability.

## 4. Logical architecture

```mermaid
flowchart TB
    subgraph Boundary[API boundary]
        MW[Request ID and timing middleware]
        Routes[Chat, profile, and discovery routes]
        Errors[404 / 503 error mapping]
    end

    subgraph Agent[Agent layer]
        Graph[ProfileAgent LangGraph]
        Discovery[DiscoveryAgent]
        FitAgent[FitAgent]
        Cache[Process-local profile cache]
        Rules[Deterministic segment classifier]
        Mapper[Profile fact and context mapper]
    end

    subgraph Control[Orchestration control plane]
        Session[Session context service]
        Intent[Hybrid intent detector]
        AgentRegistry[Agent registry]
        Planner[Deterministic planner]
        Synthesis[Response synthesizer]
    end

    subgraph Tools[FastMCP tool layer]
        Registry[Tool registry]
        Contracts[Pydantic input/output contracts]
        Policy[Static agent allowlists]
        Adapters[Customer / product / fit / upsell adapters]
        Runtime[Process-local ToolRuntime]
    end

    subgraph Domain[Domain services]
        Customer[Customer profile]
        History[Order and return history]
        Loyalty[Loyalty and engagement]
        Product[Catalogue and inventory]
        Retrieval[Product semantic retrieval]
        Fit[Fit evidence and risk]
        Upsell[Upsell eligibility and suppression]
    end

    subgraph Data[Data layer]
        DTO[Pydantic DTOs]
        Entities[SQLAlchemy entities]
        SQLite[(neutail_demo.db)]
        Vector[(Replaceable vector store)]
    end

    subgraph Models[Controlled model access]
        Gateway[LLMGateway]
        Router[Use-case router]
        Prompts[Versioned YAML prompts]
        Telemetry[Token / cost / latency records]
        Providers[LiteLLM providers]
    end

    Boundary --> Control
    Control --> Agent
    Control --> Tools
    Control --> Models
    Graph <--> Cache
    Graph --> Registry
    Graph --> Mapper
    Discovery --> Registry
    FitAgent --> Registry
    Mapper --> Rules
    Registry --> Policy
    Registry --> Contracts
    Registry --> Adapters
    Adapters --> Runtime
    Runtime --> Domain
    Domain --> DTO
    Retrieval --> Vector
    Domain --> Entities
    Entities --> SQLite
    Gateway --> Router
    Gateway --> Prompts
    Gateway --> Providers
    Gateway --> Telemetry
```

### Component responsibilities

| Component | Current responsibility |
| --- | --- |
| `api/main.py` | Owns the HTTP boundary, application lifespan, request correlation, response timing, and exception-to-status mapping. |
| `orchestrator/` | Owns identity validation, sessions, intent detection, capability discovery, agent routing, state merging, and response synthesis. |
| `agents/profiling/` | Owns profile orchestration, normalization, deterministic segmentation, data-quality reporting, and session-scoped caching. |
| `agents/discovery/` | Owns product criteria, retrieval strategy, hard constraints, deterministic ranking, and downstream engagement signals. |
| `agents/fit/` | Owns MCP-scoped evidence orchestration, deterministic sizing/risk policy, optional explanation, and downstream fit signals. |
| `tools/` | Owns tool definitions, generated schemas, discovery metadata, permission enforcement, and service adapters. |
| `services/` | Owns deterministic data retrieval, aggregation, validation, fit logic, and upsell policy logic. |
| `models/dto.py` and `models/fit.py` | Define validated models crossing service, tool, agent, gateway, and API boundaries. |
| `models/entities.py` | Maps the seeded database tables and relationships to SQLAlchemy ORM entities. |
| `database/session.py` | Resolves the database URL and creates engines/session factories with SQLite foreign keys enabled. |
| `llm_gateway/` | Owns prompt versions, use-case-to-model routes, LiteLLM invocation, output validation, fallback, and model-call telemetry. |

## 5. API and orchestration design

### Public API

| Method and path | Input | Output | Failure behavior |
| --- | --- | --- | --- |
| `GET /health` | None | Service, agent, and LangSmith status | Normal FastAPI error handling |
| `POST /api/v1/auth/login` | Customer email and shared demo password | Bearer access token and `AuthUser` | Invalid credentials `401`; missing auth configuration `503`; invalid input `422` |
| `GET /api/v1/auth/me` | Signed Bearer JWT | `AuthUser` | Missing, invalid, expired, or revoked token `401` |
| `POST /api/v1/auth/logout` | Signed Bearer JWT | Empty response | Missing, invalid, expired, or revoked token `401` |
| `GET /api/v1/customers/me/summary` | Signed Bearer JWT | Compact `CustomerSummary` | Missing/invalid token or deleted customer `401` |
| `GET /api/v1/recommendations/home` | Signed Bearer JWT and optional `limit` | Category-affinity sections containing available ranked products | Missing/invalid token `401`; dependency failure `503`; invalid limit `422` |
| `POST /api/v1/sessions` | Signed Bearer JWT and optional channel | `SessionResponse` | Missing/invalid token `401`; invalid input `422` |
| `GET /api/v1/sessions/{session_id}` | Signed Bearer JWT | `SessionResponse` | Missing/foreign session `404`; missing/invalid token `401` |
| `DELETE /api/v1/sessions/{session_id}` | Signed Bearer JWT | Empty response | Missing/foreign session `404`; missing/invalid token `401` |
| `GET /api/v1/sessions/{session_id}/context` | Signed Bearer JWT | Sanitized `SessionContext` | Missing/foreign session `404`; missing/invalid token `401` |
| `POST /api/v1/chat` | `ChatRequest` JSON and signed Bearer JWT | `OrchestratorResponse` | Missing/invalid token `401`; unknown customer `404`; cross-customer session `409`; dependency failure `503`; invalid input `422` |
| `GET /api/v1/agents` | None | Agent descriptors and implementation status | Registry initialization is fail-fast |
| `POST /api/v1/profile` | `ProfileAgentRequest` JSON | `CustomerContext` | Unknown customer `404`; workflow failure `503`; invalid input `422` |
| `GET /api/v1/customers/{customer_id}/profile` | `session_id`, optional `trace_id`, optional `refresh` | `CustomerContext` | Unknown customer `404`; workflow failure `503`; invalid input `422` |
| `GET /api/v1/agents/profiling/tools` | None | Profiling-visible `ToolDescriptor[]` | Registry initialization is fail-fast |

Every API response receives `x-request-id` and `x-process-time-ms` headers. If
the client supplies `x-request-id`, the middleware preserves it; otherwise it
generates one. For the GET profile route this ID is also the default graph
`trace_id`.

### Orchestrator workflow

```mermaid
flowchart LR
    OStart((START)) --> Identity[validate_identity]
    Identity --> Load[load_context]
    Load --> Intent[detect_intent]
    Intent --> Merge[merge_context]
    Merge --> Discover[discover_capabilities]
    Discover --> Plan[build_plan]
    Plan --> Execute[execute_plan]
    Execute --> Synthesize[synthesize_response]
    Synthesize --> Persist[persist_context]
    Persist --> OEnd((END))
```

Clear profile, discovery, fit, and service requests are detected by transparent
rules. Ambiguous text is sent to `LLMGateway.invoke()` with current session
entities and a Pydantic `IntentResult`. The deterministic planner prepends
Profiling only when `CustomerContext` is absent, then orders Discovery, Fit, and
Upsell for primary and secondary intents. All four specialists are implemented.
After Discovery or Fit returns, the orchestrator inspects typed downstream
signals and dynamically appends Upsell when it sees
`HIGH_PRODUCT_ENGAGEMENT` or `CHRONIC_FIT_RISK`. The orchestrator never
substitutes its own product-ranking, fit, eligibility, or offer-selection logic.

Sessions are bound to the first customer identity that uses them. They retain
category, occasion, selected SKU, requested size, customer context, last intent,
turn count, and the latest 20 user/assistant messages. Values are copied on
load/save to avoid accidental mutation of stored state.

### Direct profiling execution sequence

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant A as FastAPI
    participant P as ProfileAgent
    participant M as Scoped FastMCP
    participant S as Domain services
    participant D as SQLite
    participant L as LangSmith

    C->>A: GET/POST profile request
    A->>P: execute(ProfileAgentRequest)
    P-->>L: start agent and graph trace
    alt cache hit and refresh=false
        P-->>A: deep copy of cached CustomerContext
    else cache miss or refresh=true
        P->>M: list_tools()
        M-->>P: exactly five allowed contracts
        P->>M: get_customer_profile(customer_id)
        M->>S: customer master + preferences + facts
        S->>D: SQLAlchemy reads
        D-->>P: CustomerProfileSnapshot
        par enrichment fan-out
            P->>M: get_purchase_history(12 months)
        and
            P->>M: get_return_history(12 months)
        and
            P->>M: get_loyalty_profile()
        and
            P->>M: get_engagement_summary()
        end
        M->>S: invoke corresponding services
        S->>D: SQLAlchemy reads/aggregations
        D-->>P: enrichment DTOs
        P->>P: normalize facts and calculate fit risk
        P->>P: classify segment deterministically
        P->>P: publish and cache CustomerContext
        P-->>A: CustomerContext
    end
    A-->>C: typed JSON response
```

### LangGraph workflow

```mermaid
flowchart LR
    Start((START)) --> Discover[discover_tools]
    Discover --> Snapshot[get_customer_profile]
    Snapshot --> Purchase[get_purchase_history]
    Snapshot --> Returns[get_return_history]
    Snapshot --> Loyalty[get_loyalty_profile]
    Snapshot --> Engagement[get_engagement_summary]
    Purchase --> Facts[build_profile_facts]
    Returns --> Facts
    Loyalty --> Facts
    Engagement --> Facts
    Facts --> Segment[classify_segment]
    Segment --> Publish[publish_customer_context]
    Publish --> End((END))
```

The profile snapshot is mandatory. Purchase, return, loyalty, and engagement
sources are optional: their MCP errors become `MISSING` or `UNAVAILABLE` entries
in `CustomerContext.data_quality`, with safe default values used by the mapper.
A missing mandatory customer becomes `CustomerNotFoundError`.

### Deterministic segmentation

The seeded `customers.segment` column is not used as classifier input. It is
only useful for validating demo output. The agent normalizes
`affluence_band + loyalty_status` and applies this rule matrix:

| Affluence | Loyalty | Published segment | Segment code |
| --- | --- | --- | --- |
| Affluent | Loyal | Prestige Champion | `PRESTIGE_CHAMPION` |
| Less Affluent | Loyal | Value Defender | `VALUE_DEFENDER` |
| Affluent | New | Aspiring Loyalist | `ASPIRING_LOYALIST` |
| Less Affluent | New | Price Explorer | `PRICE_EXPLORER` |

The final `CustomerContext` combines profile preferences, CLV, derived segment,
12-month purchase aggregates, return rate, fit-risk score, loyalty state,
engagement score, data-quality flags, and `profile_version="v1"`.

### Discovery execution sequence

```mermaid
flowchart LR
    O[Orchestrator] -->|CustomerContext + session + query| D[DiscoveryAgent]
    D --> Q[Build criteria and select strategy]
    Q -->|structured| ST[search_products MCP tool]
    Q -->|semantic / hybrid / similar| VT[semantic_product_search MCP tool]
    VT --> RS[ProductRetrievalService]
    RS --> EP[EmbeddingProvider]
    RS --> VS[(VectorStore)]
    ST --> C[Candidate facts]
    VT --> GD[get_product_details MCP tool]
    GD --> C
    C --> I[check_inventory MCP tool]
    I --> H[Hard constraints]
    H --> R[Deterministic segment-aware ranking]
    R --> E[Optional LLMGateway explanation]
    E --> OUT[DiscoveryResult + downstream signals]
```

Structured constraints such as category, colour, price, and size are never
delegated to embeddings. Product embedding documents contain stable catalogue
descriptors, not inventory or reservation data. Ranking exposes weighted score
components and machine-readable reason codes. The result can flow to Fit, while
`HIGH_PRODUCT_ENGAGEMENT` can flow to Upsell; Discovery invokes neither agent.

### Size & Fit execution sequence

```mermaid
flowchart LR
    O[Orchestrator] -->|CustomerContext + session + SKU + size| F[FitAgent]
    F --> T[Discover exact four-tool Fit scope]
    T --> P[get_product_details]
    T --> FP[fit_get_profile]
    T --> E[fit_build_evidence]
    T --> V[fit_retrieve_similar_cases]
    E --> H[Exact SKU > brand > category evidence]
    V --> H
    P --> H
    FP --> H
    H --> C[Deterministic FitCalculator]
    C --> R[Size + confidence + fit risk + action]
    R --> S[FitPolicy downstream signals]
    R --> X[Optional LLM-only explanation]
    S --> OUT[FitResult]
    X --> OUT
```

The agent receives `CustomerContext`, `SessionContext`, SKU, optional requested
size, and `PREVENT` mode. It accesses facts only through its scoped in-process
FastMCP server. Exact customer/product history outranks brand and category
evidence; vector-retrieved anonymized outcomes supplement rather than replace
authoritative evidence. Non-fit returns do not influence sizing. Unavailable
sizes, missing history, provider failures, and chronic fit-return behavior all
produce explicit actions, reason codes, or downstream signals.

### Governed service-offer execution sequence

```mermaid
sequenceDiagram
    participant O as Orchestrator
    participant U as UpsellAgent
    participant M as Scoped FastMCP
    participant P as UpsellPolicyService
    participant G as LLM Gateway
    O->>U: CustomerContext + session + typed trigger
    U->>M: get_loyalty_profile
    U->>M: get_engagement_summary
    U->>M: evaluate_upsell
    M->>P: ordered deterministic rules
    P-->>U: eligible offers or suppression reasons
    alt suppressed or ineligible
        U-->>O: NO_OFFER (no model call)
    else eligible
        U->>U: deterministic opportunity score
        U->>U: select only from eligible offers
        U->>G: word immutable selected offer
        U->>M: record_upsell_event(OFFER_SHOWN)
        U-->>O: OFFER_AVAILABLE + explicit-consent requirement
    end
```

Policy order is request/context validation, active subscription, explicit
suppression, recent Style+ decline, 30-day frequency limit, trigger validation,
and service eligibility. Only then may scoring and selection run. Thresholds
live in `UpsellPolicyConfig`; score weights and the offer catalogue live in
`agents/upsell/constants.py`. `STYLING_ADVISORY` is the value-first option;
`STYLE_PLUS_TRIAL` and `STYLE_PLUS` are considered only when policy and segment
rules permit them. No path enrolls a customer or generates a discount.

### UI engagement and decision-event sequence

```mermaid
sequenceDiagram
    participant UI as Web UI
    participant API as FastAPI
    participant E as EngagementEventService
    participant O as Orchestrator
    participant U as UpsellAgent
    participant D as Decision Store
    UI->>API: PRODUCT_VIEWED + session + SKU + idempotency key
    API->>E: authenticated customer + event
    E->>E: resolve active SKU and persist view
    alt third view of a premium product
        API->>O: HIGH_PRODUCT_ENGAGEMENT trigger
        O->>U: normal governed agent invocation
        U-->>O: OFFER_AVAILABLE or NO_OFFER
        O->>D: retain actionable decision ownership
        O-->>API: typed UpsellResult
    end
    API-->>UI: event count, trigger, trace, optional result
    UI->>API: explicit ACCEPTED / DECLINED / DISMISSED
    API->>D: validate customer, session, decision, idempotency
    API->>U: record_upsell_event tool call
    API-->>UI: interest/decline/dismissal recorded
```

The engagement endpoint does not trust the browser to classify products or
customers. `OFFER_ACCEPTED` represents interest only and cannot create a
subscription, start a trial, or execute a payment. Both endpoint idempotency
records and decision ownership are process-local in this demo and must move to
a shared durable store before multi-worker deployment.

## 6. FastMCP tool design

### Registration and contracts

`@tool_contract` provides each adapter with a stable name, title, description,
capability, read/write metadata, safety annotations, and allowed-agent metadata.
FastMCP derives JSON input/output schemas from Python type hints and Pydantic
DTOs. `ToolRegistry` materializes all adapters and fails at import time if the
registered tool set differs from the permission policy.

The registry supports:

- listing tools and capabilities globally or by agent;
- describing one tool and its schemas;
- resolving a tool only after permission validation;
- building a full trusted server or an agent-scoped server.

### Permission boundary

Agents receive a server containing only their allowlisted tools. A disallowed
tool is absent during discovery and also rejected by direct registry lookup.
Each implemented agent verifies that runtime discovery contains its required
tool capabilities before continuing.

| Agent scope | Capability groups in the current registry |
| --- | --- |
| Profiling | Aggregate profile, purchase, return, loyalty, and engagement summaries |
| Discovery | `search_products`, `get_product_details`, `check_inventory`, and `semantic_product_search` |
| Fit | Exactly `get_product_details`, `fit_get_profile`, `fit_build_evidence`, and `fit_retrieve_similar_cases` |
| Upsell | Aggregate customer, loyalty, and engagement facts; optional product facts; deterministic `evaluate_upsell`; governed `record_upsell_event`; legacy compatibility contracts |

Profiling, Discovery, Fit, and Upsell have implemented agent workflows today.

### Tool runtime

`ToolRuntime` is lazily created once per process. It owns the SQLAlchemy engine,
session factory, process-local product and fit vector indexes, and process-local
`UpsellPolicyState`. Every adapter opens a
short-lived session; read calls close without committing, while explicitly
marked write calls commit and roll back on failure. The Profiling Agent uses an
in-process FastMCP client/server transport, so no network hop is required.

## 7. Domain and data design

Domain services return DTOs, do not expose ORM objects across boundaries, and
own deterministic validation and aggregation. Product and inventory services
use explicit repository interfaces; SQLAlchemy is confined to those repository
implementations. `ProductRetrievalService` owns embedding/index behavior behind
provider-neutral protocols.

| Service | Responsibility |
| --- | --- |
| `CustomerProfileService` | Customer existence, master data, normalized preferences, and profile facts |
| `ProductCatalogService` | Factual structured search, single/bulk SKU lookup, and active-state checks |
| `InventoryService` | Per-location and aggregate availability, including bulk checks |
| `ProductRetrievalService` | Stable product documents, embedding refresh, metadata filtering, and top-k semantic SKU retrieval |
| `ProductRepository` / `InventoryRepository` | SQLAlchemy persistence adapters below product-domain services |
| `OrderHistoryService` | Orders, purchased items, recent sizes, and purchase summaries |
| `ReturnHistoryService` | Return records, rates, reason summaries, and size/product evidence |
| `FitProfileService` | Fit profile and brand size adjustments |
| `FitEvidenceService` / `FitEvidenceRepository` | Exact SKU, brand, category, exchange, and normalized fit-return evidence with SQL confined to the repository |
| `FitRetrievalService` | Stable anonymized fit documents, replaceable embeddings, and similar-outcome retrieval |
| `FitCalculator` / `FitPolicy` | Deterministic size recommendation, confidence, risk band, action, and downstream signals |
| `LoyaltyService` | Current loyalty profile, transactions, and summaries |
| `EngagementService` | Clickstream summaries and service-engagement records |
| `UpsellPolicyService` | Consent, cooldown, frequency, propensity, candidate, and suppression rules |

### Persistence model

```mermaid
erDiagram
    CUSTOMER ||--o| LOYALTY : has
    CUSTOMER ||--o| FIT_PROFILE : has
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--o{ ORDER_ITEM : contains
    PRODUCT ||--o{ ORDER_ITEM : purchased_as
    CUSTOMER ||--o{ RETURN : makes
    ORDER ||--o{ RETURN : has
    ORDER_ITEM ||--o| RETURN : may_create
    PRODUCT ||--o{ RETURN : returned_as
    PRODUCT ||--o{ INVENTORY : stocked_at
    CUSTOMER ||--o{ CLICKSTREAM_EVENT : generates
    CUSTOMER ||--o{ SERVICE_ENGAGEMENT : has
    CUSTOMER ||--o{ LOYALTY_TRANSACTION : earns
    CUSTOMER ||--o{ DEMO_SCENARIO : anchors

    CUSTOMER {
        text customer_id PK
        text affluence_band
        text loyalty_status
        float estimated_clv_gbp
        text preferred_categories
        text usual_size
    }
    PRODUCT {
        text sku PK
        text category
        text brand
        float current_price_gbp
        boolean active
    }
    INVENTORY {
        text sku PK,FK
        text location_id PK
        int available_qty
    }
    ORDER {
        text order_id PK
        text customer_id FK
        datetime order_datetime
        float total_gbp
    }
    ORDER_ITEM {
        text order_item_id PK
        text order_id FK
        text sku FK
        text size
    }
    RETURN {
        text return_id PK
        text order_item_id FK
        text customer_id FK
        text sku FK
        text reason_code
    }
```

The database also contains read-only `customer_360` and
`customer_return_stats` views. Current services calculate against mapped tables
rather than mapping those views as ORM entities. SQLite stores some list fields
as pipe-delimited text and brand adjustments as JSON text; Pydantic validators
normalize them to lists and dictionaries at the DTO boundary.

## 8. Controlled LLM gateway

`LLMGateway` is the shared component for orchestrator intent detection, optional
response synthesis, and optional Discovery/Fit explanations. It is not called
by profiling or by deterministic Discovery ranking and Fit calculation, and it
is not exposed directly through FastAPI.

```mermaid
sequenceDiagram
    participant X as Agent/orchestrator
    participant G as LLMGateway
    participant R as ModelRouter
    participant P as PromptRegistry
    participant L as LiteLLM
    participant T as Telemetry/LangSmith

    X->>G: invoke(use_case, context, response_model, trace metadata)
    G->>R: resolve governed route and policy
    G->>P: load prompt group/version
    P-->>G: rendered system and user messages
    G->>L: acompletion(primary model)
    L-->>G: response and usage
    G->>G: parse and validate Pydantic output
    alt provider or validation failure
        G->>L: retry and/or configured fallback
        L-->>G: fallback response and usage
    end
    G->>T: sanitized attempt record
    G-->>X: string or validated Pydantic model
```

Configured logical use cases are `intent_detection`, `product_explanation`,
`discovery_explanation`,
`fit_explanation`, `upsell_message`, `optional_summary`, and
`response_synthesis`. Routes, limits, fallback models, and logging policy live
in `llm_gateway/config/models.yaml`; trusted versioned prompts live under
`llm_gateway/prompts/`.

Every default physical model call uses `litellm.acompletion`. The gateway owns
timeouts, temperature, token limits, fallback, structured-output retries, and
Pydantic validation. It records provider/model, prompt version, attempt,
token usage, latency, status, and provider-reported cost. Raw prompts and model
responses are excluded from traces unless a route explicitly enables them.
Telemetry records are process-local and are also emitted through application
logging.

`NEUTAIL_LLM_TRACE_BODIES=true` is a development-only global override that adds
the fully rendered messages and final visible model content to each physical
`litellm.acompletion` trace. It is disabled by default because those bodies can
contain customer context and conversation data; separate reasoning content is
not promoted into the trace output body.

The NVIDIA routes explicitly govern model, timeout, token-limit, fallback, and
optional reasoning behavior per use case. The gateway accepts only final
`message.content` as output and never promotes separate reasoning text into a
customer-visible response. Empty final content is a failed attempt and triggers
the configured fallback.

## 9. Observability and failure handling

### LangSmith trace hierarchy

- `neutail_orchestrator`: top-level chat control-plane run;
- `neutail_orchestrator_graph`: session, routing, execution, and persistence;
- `orchestrator.validate_customer`: MCP-backed identity validation;
- `orchestrator.invoke.<agent>`: one trace per planned agent invocation;
- `profile_agent`: top-level deterministic agent run;
- `profile_agent_graph`: LangGraph execution and node activity;
- `profile_tool_discovery`: scoped MCP discovery;
- `discovery_agent`: top-level Discovery execution;
- `build_discovery_criteria` and `select_retrieval_strategy`;
- `semantic_product_search` and `vector_search` when applicable;
- `check_inventory`, `apply_hard_constraints`, and `personalized_ranking`;
- `discovery_explanation` and `publish_discovery_signals`;
- `fit_agent`, `fit_tool_discovery`, and one trace per Fit MCP invocation;
- `build_fit_embedding_index` and `vector_fit_search` when applicable;
- `calculate_fit_risk`, `apply_fit_policy`, and optional `fit_explanation`;
- `upsell_agent` and `upsell_tool_discovery`;
- `check_subscription`, `check_recent_decline`, and `check_frequency_limit`;
- `calculate_opportunity_score`, `select_offer`, and `generate_upsell_message`;
- `evaluate_upsell` and `record_upsell_event` FastMCP traces;
- one tool trace per FastMCP invocation;
- `llm_gateway.invoke`: governed logical model call;
- `litellm.acompletion`: one trace per physical provider attempt.

Trace metadata includes correlation/trace ID, session ID, customer ID where
applicable, agent, use case, prompt version, logical model, provider model, and
cache-hit status. Tracing is activated through environment configuration and is
safe to disable for local tests.

### Failure strategy

| Failure | Behavior |
| --- | --- |
| Unknown customer | MCP identity validation or mandatory profile lookup fails; API returns structured `404` |
| Session reused by another customer | Session service rejects it; chat API returns structured `409` |
| Planned agent not implemented | Orchestrator records `AGENT_UNAVAILABLE` and returns a controlled partial response |
| Missing/mismatched profile tool scope | Graph stops with a discovery error; API returns `503` |
| Missing/mismatched Discovery tool scope | Orchestrator records a dependency/execution error and does not fabricate recommendations |
| Missing/mismatched Fit tool scope | Orchestrator records a dependency/execution error and does not fabricate size guidance |
| Missing Upsell context or policy capability | Upsell fails closed and does not call the model or create an offer |
| Upsell suppression or ineligibility | Structured `NO_OFFER` with machine-readable reasons; no model call |
| Upsell wording provider unavailable | Deterministic `OFFER_AVAILABLE` remains valid with `message=null` |
| Discovery explanation unavailable | Ranked products are returned with `explanation=null` |
| Fit vector retrieval unavailable | Deterministic exact/brand/category evidence still produces the decision |
| Fit explanation unavailable | Structured sizing and risk are returned with `explanation=null` |
| Missing Fit history/profile | A weaker or `INSUFFICIENT_EVIDENCE` result is returned rather than invented certainty |
| No in-stock constrained candidates | Successful `NO_RESULTS` Discovery result |
| Optional enrichment unavailable | Profile still publishes with defaults and a data-quality flag |
| Invalid request/response DTO | Pydantic rejects it at the relevant boundary |
| Unknown LLM use case/prompt version | Gateway rejects before contacting a provider |
| Invalid structured LLM output | Governed retry, then configured fallback, then `LLMInvocationError` |
| Provider timeout/error | Attempt is accounted and fallback is tried when allowed |

## 10. Runtime and deployment

The demo runs as one Uvicorn process:

```text
Uvicorn process
  ├── FastAPI application
  ├── one lifespan-owned NeuTailOrchestrator
  │   ├── LangGraph control-plane workflow
  │   ├── in-memory SessionContextService
│   ├── in-memory upsell decision/idempotency store
│   └── agent registry and shared LLMGateway
  ├── one lifespan-owned ProfileAgent
  │   └── in-memory (session_id, customer_id) cache
  ├── one registered DiscoveryAgent
  │   ├── deterministic query strategy and ranking
  │   └── optional shared-gateway explanations
  ├── one registered FitAgent
  │   ├── MCP-only evidence collection and vector fit retrieval
  │   ├── deterministic size/risk calculation and policy signals
  │   └── optional shared-gateway explanation
  ├── one lazy ToolRuntime
  │   ├── SQLAlchemy engine/session factory
  │   ├── in-memory product and fit vector indexes
  │   └── in-memory upsell policy state
  ├── in-process FastMCP servers/clients
  ├── optional LLMGateway instances
  └── local database/neutail_demo.db
```

Configuration is environment-based:

- `NEUTAIL_DATABASE_URL` overrides the bundled SQLite URL;
- `NEUTAIL_JWT_SECRET` supplies the HS256 token signing/validation secret;
- `NEUTAIL_DEMO_PASSWORD` supplies the shared local-demo login password;
- `NEUTAIL_ACCESS_TOKEN_TTL_SECONDS` optionally changes the one-hour token
  lifetime, up to one day;
- `NEUTAIL_CORS_ORIGINS` optionally supplies the comma-separated browser UI
  origins allowed to call the API;
- `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, and `LANGSMITH_PROJECT` control
  tracing;
- `NVIDIA_NIM_API_KEY` authenticates development LLM routes and
  `NVIDIA_NIM_API_BASE` selects the hosted or self-hosted NIM endpoint;
- `LITELLM_LOCAL_MODEL_COST_MAP=True` keeps LiteLLM model-cost data local.
- `NEUTAIL_RESPONSE_SYNTHESIS_LLM=true` opts into governed LLM prose
  synthesis; deterministic templates are the default.
- `NEUTAIL_DISCOVERY_EXPLANATIONS_LLM=true` opts into explanations after
  deterministic ranking; the default is `false`.
- `NEUTAIL_FIT_EXPLANATIONS_LLM=true` opts into explanations after the
  deterministic Fit decision; the default is `false`.

The application is started with:

```bash
./bin/uvicorn api.main:app --reload
```

## 11. Security and trust boundaries

Current controls:

- Pydantic validates boundary data and rejects unknown fields.
- SQLAlchemy query construction avoids interpolated SQL in service inputs.
- Prompt paths accept only safe group/version patterns.
- Agent tool exposure is least-privilege and enforced at registry construction.
- Tool contracts declare read-only/idempotent/destructive hints.
- LLM traces omit raw request and response bodies by default.
- SQLite foreign-key enforcement is enabled on every connection.
- CORS permits only explicit local-demo or environment-configured UI origins,
  methods, and request headers; correlation and timing headers are exposed.

Demo limitations:

- Customer passwords and roles are not stored. Every seeded customer uses one
  environment-configured demo password and receives the `customer` role.
- Logout revocation is process-local; audience/issuer validation and
  authorization beyond the JWT customer subject are not implemented.
- There is no rate limiting, request-size policy, or network-level MCP security.
- The session identifier remains caller-provided and process-local.
- Secrets are expected through environment variables; no secret manager exists.
- Process-local caches and telemetry disappear on restart and are not shared
  across workers.

## 12. Scalability, consistency, and known gaps

The design is intentionally single-process. Before production use:

1. Replace the demo HS256 secret with an external token issuer, asymmetric key
   verification, audience/issuer policy, rotation, and customer authorization.
2. Move session lifecycle and revocation state into a shared store before
   running multiple API workers.
3. Persist subscription entitlements and offer/session audit metadata in
   dedicated production tables rather than the PoC service-engagement shape.
4. Move SQLite to a managed relational database and provide repository
   implementations for the remaining service areas.
5. Replace process-local profile cache and upsell state with a TTL-based shared
   store; define invalidation from customer/order/return events.
6. Persist model-call audit records and define retention/redaction policy.
7. Add migrations, readiness checks, structured logging, metrics, rate limits,
   and tracing sampling.
8. Avoid `--reload`, run multiple workers only after shared state is external,
   and define explicit connection-pool limits.

## 13. Verification strategy

The current automated suite covers:

- all four deterministic segmentation branches;
- exact ProfileAgent tool scope and denial of product tools;
- seeded customer-context construction;
- unknown-customer behavior;
- per-session cache reuse and explicit refresh;
- health, GET/POST profile, tool discovery, headers, and API error mapping;
- orchestrated profile chat, runtime agent discovery, compound planning,
  multi-turn reuse, identity binding, and unavailable-agent fallback;
- Discovery query/strategy selection, structured/semantic/hybrid retrieval,
  deterministic segment ranking, hard price/inventory filtering, reason codes,
  vector score contribution, no-results behavior, explanation fallback, and
  engagement signals;
- exact Discovery tool scope and static boundary checks preventing direct SQL,
  vector-store, model-provider, Fit, or Upsell access;
- the authenticated chat API's structured `discovery_result` contract;
- exact Fit tool scope and static boundary checks preventing direct SQL,
  repositories, model providers, Discovery, or Upsell access;
- usual-size, prior-success, too-small/exchange, brand-adjustment, non-fit
  return, unavailable-size, chronic-risk, insufficient-evidence, and
  exact-evidence-over-vector decision cases;
- Fit vector/provider and optional-explanation failure fallback behavior;
- the authenticated chat API's structured `fit_result` contract;
- LLM route/prompt resolution, direct LiteLLM wiring, structured output,
  retries, fallback, telemetry, and sanitized tracing behavior.
- governed Upsell eligibility, consent, subscription, decline cooldown,
  frequency limits, trigger thresholds, segment policy, scoring, selection,
  LLM-only wording, event recording, fail-closed behavior, and static agent
  boundaries;
- Discovery and Fit signal handoff to Upsell through the orchestrator, with no
  direct specialist-to-specialist invocation.

Run it with:

```bash
LANGSMITH_TRACING=false LITELLM_LOCAL_MODEL_COST_MAP=True \
  ./bin/python -m pytest -q
```
