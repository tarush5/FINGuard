# FINGuard API

Interactive documentation is generated from the code and served at
**`/docs`** (Swagger UI) and **`/redoc`**; the raw schema is at
`/openapi.json`. This page covers the conventions those pages cannot express.

Base path: `/api/v1`

---

## Authentication

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@finguard.io","password":"FinGuard#2026"}'
```

```json
{
  "access_token": "eyJhbGciOi…",
  "refresh_token": "eyJhbGciOi…",
  "token_type": "bearer",
  "expires_in": 1800,
  "user": { "id": "USR-…", "roles": ["ADMIN"], "permissions": ["…"] }
}
```

Send the access token as `Authorization: Bearer <token>`. When it expires, call
`POST /auth/refresh` with the refresh token — it is **rotated**: the old one is
revoked, and presenting it again revokes every token in the family, because
reuse implies theft.

| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | Exchange credentials for tokens |
| `POST /auth/refresh` | Rotate a refresh token |
| `POST /auth/logout` | Revoke the presented refresh token |
| `GET /auth/me` | Profile with effective permissions |
| `POST /auth/change-password` | Rotate a password, revoking all sessions |
| `GET /auth/roles` | The role → permission matrix |

---

## Conventions

### Error envelope

Every failure has the same shape. Stack traces never reach the client.

```json
{
  "success": false,
  "error": {
    "code": "TRANSACTION_NOT_FOUND",
    "message": "Transaction TXN-123 was not found.",
    "request_id": "req_a0913a768e6b4cd5",
    "details": { "fields": [{ "field": "amount", "message": "must be > 0" }] }
  }
}
```

| Status | Codes |
|---|---|
| 400 | `UNSAFE_QUERY`, `SQL_EXECUTION_FAILED`, `BAD_REQUEST` |
| 401 | `NOT_AUTHENTICATED` |
| 403 | `PERMISSION_DENIED` |
| 404 | `NOT_FOUND`, `TRANSACTION_NOT_FOUND`, `CASE_NOT_FOUND`, `MODEL_NOT_FOUND` |
| 409 | `CONFLICT` (duplicate rule code, illegal case transition) |
| 422 | `VALIDATION_ERROR`, `CUSTOMER_NOT_FOUND`, `INSUFFICIENT_TRAINING_DATA` |
| 429 | `RATE_LIMITED` (with `Retry-After`) |
| 500 | `INTERNAL_ERROR` |

### Pagination

List endpoints accept `page`, `page_size` (≤ 200), `sort_by`, `sort_dir`, and
return:

```json
{
  "items": [ … ],
  "pagination": {
    "page": 1, "page_size": 25, "total": 24119, "pages": 965,
    "has_next": true, "has_previous": false
  }
}
```

### Headers

| Header | Direction | Meaning |
|---|---|---|
| `X-Request-ID` | in / out | Correlation id; echoed and attached to every log line and audit row |
| `X-Correlation-ID` | in | Ties an ingested transaction to an upstream trace |
| `X-Process-Time-Ms` | out | Server-measured handling time |

### Permissions

Routes declare a permission, never a role. `GET /auth/me` returns the caller's
effective set; `GET /governance/roles` returns the whole matrix.

| Permission | Grants |
|---|---|
| `transaction:read` / `transaction:ingest` | Query / submit transactions |
| `customer:read` / `customer:pii_read` | Customer records / unmasked PII |
| `rule:read` / `rule:write` | View / author rules |
| `case:read` / `case:write` / `case:assign` | Investigation workflow |
| `risk:read` / `risk:simulate` | Risk data / policy simulation |
| `model:read` / `model:train` / `model:promote` | MLOps |
| `ai:query` / `ai:sql` | AI investigator / natural-language SQL |
| `audit:read`, `governance:*`, `user:manage`, `system:admin` | Governance |

---

## Scoring a transaction

```bash
curl -X POST http://localhost:8000/api/v1/transactions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "event_id": "evt_2026_08_24_0001",
    "customer_id": "C-80112",
    "merchant_id": "M-10031",
    "amount": 184200,
    "currency": "INR",
    "channel": "WEB",
    "device_id": "D-9F2A11C4",
    "ip_address": "185.44.2.19",
    "country": "GB", "city": "London",
    "latitude": 51.5074, "longitude": -0.1278
  }'
```

```json
{
  "transaction_id": "TXN-4F1A589997A66ACD",
  "decision": "MANUAL_REVIEW",
  "risk_score": 79.5,
  "risk_band": "HIGH",
  "duplicate": false,
  "latency": {
    "validation_ms": 0.004, "dedup_ms": 0.19, "enrichment_ms": 0.52,
    "feature_ms": 1.83, "rule_ms": 0.54, "model_ms": 6.16,
    "graph_ms": 1.17, "persist_ms": 3.42, "total_ms": 14.03
  },
  "trace": { "stages": [ /* FEATURES, RULES, MODEL, GRAPH, RISK, DECISION */ ] }
}
```

**`event_id` is the idempotency key.** Replaying it returns the original
decision with `duplicate: true` — no second case, no double-counted metrics.
Omit it and the server generates one, which means the call is *not* idempotent;
always send your own for machine traffic.

Related:

| Endpoint | Purpose |
|---|---|
| `GET /transactions/{id}/trace` | The six-stage decision trace |
| `GET /transactions/{id}/explain` | SHAP attributions and ensemble factors |
| `GET /transactions/live` | Server-sent stream of newly scored transactions |

The live stream is SSE over `fetch` (not `EventSource`) so the access token
travels in a header rather than a query string.

---

## Endpoint map

| Group | Endpoints |
|---|---|
| **Transactions** | `GET|POST /transactions`, `/{id}`, `/{id}/trace`, `/{id}/explain`, `/live` |
| **Entities** | `/customers`, `/customers/{id}`, `/merchants`, `/merchants/{id}`, `/devices`, `/devices/{id}` |
| **Rules** | `GET|POST /rules`, `GET|PATCH|DELETE /rules/{id}`, `POST /rules/test` |
| **Risk** | `/risk/policy`, `POST /risk/simulate`, `/risk/threshold-optimisation` |
| **Fraud** | `/alerts`, `/cases`, `/cases/{id}`, `/cases/{id}/status`, `/assign`, `/notes`, `/timeline` |
| **Graph** | `/fraud-rings`, `/fraud-rings/{id}`, `POST /fraud-rings/detect`, `/graph/{type}/{id}`, `/graph/summary` |
| **Analytics** | `/analytics/overview`, `/timeseries`, `/breakdown/{dimension}`, `/losses`, `/performance`, `/merchants`, `/customers`, `/heatmap`, `/geography`, `/operations` |
| **Forecasting** | `/forecasting/{metric}`, `/forecasting-workload` |
| **MLOps** | `/models`, `/models/{id}`, `/promote`, `/rollback`, `/compare`, `POST /models/train`, `/experiments`, `/monitoring/models`, `/monitoring/drift`, `/feedback` |
| **Platform** | `/datasets`, `/pipelines`, `/quality`, `POST /quality/run`, `/lineage` |
| **AI** | `/ai/status`, `POST /ai/ask`, `POST /ai/sql`, `/ai/cases/{id}/summary`, `/report`, `/ai/queries` |
| **Governance** | `/users`, `/governance/roles`, `/governance/policies`, `/governance/ai-usage`, `/audit` |
| **System** | `/health`, `/ready`, `/metrics`, `/monitoring/system`, `/monitoring/latency`, `/notifications`, `/events/topics`, `/events/dead-letter` |
| **Demo** | `/demo/scenarios`, `POST /demo/run`, `/demo/runs` |

---

## Rule authoring

A rule condition is a JSON expression tree. The server validates it against an
allow-list of fields and operators before storing it — an unknown field or
operator is a 422, and there is no code path that evaluates a string.

```json
{
  "code": "R-VEL-003",
  "name": "Rapid spending after a device change",
  "category": "VELOCITY",
  "severity": "HIGH",
  "risk_points": 22,
  "action": "SCORE",
  "priority": 15,
  "condition": {
    "all": [
      { "field": "is_new_device", "op": "is_true" },
      { "field": "txn_count_1h", "op": "gte", "value": 5 },
      { "any": [
        { "field": "amount_ratio_to_avg", "op": "gt", "value": 3 },
        { "field": "is_cross_border", "op": "is_true" }
      ]}
    ]
  }
}
```

Operators: `gt gte lt lte eq ne in not_in between contains starts_with is_true
is_false`. A predicate may compare against another field with an optional
multiplier:

```json
{ "field": "amount", "op": "gt", "value_ref": "customer_avg_amount", "multiplier": 5 }
```

Actions: `SCORE` (adds points), `STEP_UP`, `REVIEW`, `DECLINE` (escalate the
decision, never de-escalate it).

Before activating, back-test against real history:

```bash
curl -X POST /api/v1/rules/test -H "Authorization: Bearer $TOKEN" -d '{
  "condition": { "field": "amount_ratio_to_avg", "op": "gt", "value": 4 },
  "sample_size": 2000, "days": 60
}'
```

returns hit rate, precision and recall against labelled outcomes.

---

## AI endpoints

`POST /ai/ask` — evidence-grounded answer about a transaction or case:

```json
{
  "answer": "Assessment: HIGH risk (79.5/100), decision MANUAL_REVIEW…",
  "generated_by": "deterministic",
  "evidence": [
    { "kind": "AMOUNT", "source": "customers.avg_transaction_amount",
      "statement": "Amount INR 43,989.83 is 4.8x the customer's average…" }
  ],
  "disclaimer": "Evidence is retrieved from the platform database…"
}
```

`POST /ai/sql` — natural-language analytics. The response always includes the
executed SQL. Rejected queries return `400 UNSAFE_QUERY` with the reason, and
both the attempt and the reason are logged to `ai_queries`.

Blocked by design: anything that is not a single `SELECT`; comments; multiple
statements; tables outside the caller's permissions; PII columns without
`customer:pii_read`.

---

## Rate limits

240 requests/minute per client for the API, 20/minute for `/auth/*`. Exceeding
either returns `429` with `Retry-After`. Limits are per-process unless
`REDIS_URL` is configured, in which case they are shared across replicas.

---

## Versioning

The path carries the major version (`/api/v1`). Additive changes — new fields,
new endpoints, new optional parameters — ship within `v1`. Removing or
retyping a field would require `/api/v2`.
