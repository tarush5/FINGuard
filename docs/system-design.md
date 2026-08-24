# FINGuard system design

The reasoning behind the platform: what it must do, what it deliberately does
not do, where it would break, and what would have to change to make it survive
production traffic.

---

## 1. Requirements

### Functional

| # | Requirement | Where it is met |
|---|---|---|
| F1 | Score every transaction and return a decision synchronously | `app/services/pipeline.py` |
| F2 | Never double-process a replayed event | Cache claim + `ingested_events` ledger |
| F3 | Explain every decision in terms an auditor accepts | Decision trace + SHAP + rule matches |
| F4 | Let analysts author and change rules without a deploy | JSON condition trees, validated server-side |
| F5 | Detect coordinated fraud, not only individual transactions | Graph projection + ring detection |
| F6 | Give investigators a complete workspace | Case timeline, evidence, notes, verdict |
| F7 | Turn verdicts into training signal | `feedback_labels` + retraining gate |
| F8 | Monitor model health in production | PSI/KS drift, metric thresholds, latency |
| F9 | Let risk owners test a policy before adopting it | Simulator replays real history |
| F10 | Answer natural-language questions safely | Evidence-grounded AI + SQL guardrails |
| F11 | Keep a defensible record of who did what | Append-only audit trail |

### Non-functional

| # | Requirement | Target | Measured (dev laptop, SQLite) |
|---|---|---|---|
| N1 | Decision latency | p95 < 500 ms | **16 ms** |
| N2 | API latency | p95 < 300 ms | **5–12 ms** |
| N3 | Model inference | < 100 ms | **6.2 ms** (incl. SHAP) |
| N4 | Event lag | < 5 s | sub-second in-process |
| N5 | Availability | 99.9% | stateless API, Multi-AZ data tier |
| N6 | Auditability | every state change | enforced in the service layer |
| N7 | Privacy | PII masked by role | enforced at serialisation |

Cold start is 3.7 s — model artifact load plus SHAP explainer construction, paid
once per process. In production that is absorbed by rolling deploys and a
readiness probe that only passes after the first inference.

### Explicitly out of scope

Real payment rails, KYC/AML case filing (SAR generation), chargeback
reconciliation, and multi-tenant isolation. Each would change the data model
enough that pretending to support them would be worse than saying so.

---

## 2. Key design decisions

### 2.1 Synchronous scoring, asynchronous everything else

The caller needs a decision now; nobody needs the alert email now. Scoring runs
inline (14 ms p50) and the fan-out — alerts, cases, notifications, metrics —
goes over the event bus.

**Consequence:** publishing must happen *after* the database commit, otherwise a
consumer racing on another thread queries a row that does not exist yet. The
pipeline therefore returns held-back events when the caller owns the transaction
boundary (`commit=False`), and `publish_pending()` releases them after the
caller commits. This was a real bug found in browser testing, not a theoretical
concern: the consumer logged "transaction not visible" on every demo scenario
until the ordering was fixed.

### 2.2 Rules as data

A rule is a JSON expression tree with an allow-list of fields and operators,
evaluated by a small interpreter. No `eval`, no code deploy, no injection
surface. Rules carry a version that increments on edit, and every trigger is
recorded with the values that matched — which is what makes rule precision
measurable against analyst verdicts.

**Trade-off:** the grammar is deliberately limited (comparisons, boolean
composition, field-to-field comparison with a multiplier). Anything more
expressive would need a real expression language and a sandbox.

### 2.3 One feature function for training and serving

Train/serve skew is the most common way a fraud model quietly degrades. There is
exactly one `compute_features`. The online path lets it query; the batch backfill
injects the same state from memory. A test asserts both paths produce identical
vectors.

Point-in-time correctness is structural: history is selected with
`occurred_at < transaction.occurred_at`, so replaying the past cannot see the
future.

### 2.4 Cost-optimised thresholds

The model threshold is chosen by sweeping operating points and minimising

```
expected cost = fraud_loss(FN) + cost_fp × FP + cost_review × reviews
```

With a missed fraud at ₹50,000 and a false positive at ₹500, the optimiser picks
a low threshold (0.024 on the seeded data), trading precision for recall. That is
the correct answer to the cost model, and the platform shows the whole cost curve
so the operating point is a choice rather than a default.

### 2.5 Two graph workloads, two implementations

Online risk needs the immediate neighbourhood in ~1 ms; ring detection needs
connected components over 90 days. Forcing both through one mechanism would make
one of them wrong. Online uses indexed queries; offline builds a NetworkX
projection.

**Trade-off:** the relational projection is capped at 20,000 recent transactions.
Beyond that, Neo4j (already configurable) or a graph-processing job is required.

### 2.6 Ensemble as a weighted blend

Six signals, weights summing to 1, result directly on 0–100. It is explainable
("the model contributed 38 of the 86 points"), tunable, and the simulator can
re-run the identical function with different weights.

**Trade-off:** a stacked meta-learner would likely score better. It would also be
much harder to explain to an auditor, and the weights would stop being a lever
risk owners can reason about.

### 2.7 AI narrates, never asserts

Evidence is retrieved first, deterministically. The model is handed that evidence
and asked to phrase it. Every response records which path produced it. With no
provider configured the platform degrades to templates and keeps working.

Generated SQL is not trusted: it is validated after generation against an
allow-list filtered by the caller's own permissions.

### 2.8 Substitutable infrastructure

Kafka, Redis and Neo4j each have an in-process equivalent with the same
interface and semantics — retries, backoff, dead-letter, TTLs. This keeps the
laptop experience honest (nothing silently no-ops) while the production drivers
activate purely through environment variables.

---

## 3. Data model

41 tables in five groups:

| Group | Tables | Notes |
|---|---|---|
| Identity | users, roles, permissions, user_roles, role_permissions, refresh_tokens, audit_logs, notifications, policies | Refresh tokens stored server-side for revocation |
| Core | customers, accounts, merchants, devices, device_links, transactions, transaction_features, ingested_events, dead_letter_events | `device_links` materialises the customer↔device edge |
| Risk | rules, rule_executions, risk_scores, fraud_predictions, decisions, alerts, cases, case_events, case_notes, fraud_rings, fraud_ring_members | `decisions` is the immutable record |
| MLOps | model_versions, model_metrics, drift_metrics, feedback_labels, training_runs | Registry is the serving source of truth |
| Platform | datasets, pipeline_runs, quality_checks, lineage_edges, ai_queries, system_metrics, demo_scenario_runs | |

Conventions: business-readable string primary keys (`TXN-…`, `CASE-…`), money as
`NUMERIC(18,2)` returned as float, timezone-aware timestamps, composite indexes
on every access path the UI uses, soft deletion where an audit trail must
survive, and explicit constraint naming so Alembic autogenerate is deterministic.

---

## 4. Failure analysis

| Failure | Detection | Behaviour | Recovery |
|---|---|---|---|
| Redis down | `cache.healthy()` | Falls back to in-process cache; idempotency relies on the durable ledger | Automatic on reconnect |
| Kafka down at boot | Producer connect fails | Falls back to the in-process bus; logged loudly | Restart with brokers reachable |
| Consumer handler throws | Retry counter | 3 retries with exponential backoff, then dead-letter | Replay from System Health |
| Model artifact missing | Load raises | Falls back to the documented scorecard, tagged as such | Re-promote or retrain |
| SHAP unavailable | Explainer construction fails | Importance-weighted fallback, `method` says so | — |
| Database unreachable | `/ready` probe | 503; the load balancer stops routing | Failover to the Multi-AZ standby |
| Poison message | Deserialisation fails | Logged, offset committed, message dropped | Inspect the DLQ |
| Duplicate event | Ledger + cache claim | Original decision returned | None needed |
| Clock skew | Validation | Rejects timestamps >5 min in the future | — |
| Training with too few positives | Explicit guard | `INSUFFICIENT_TRAINING_DATA`, no model registered | Wait for labels |

**Degradation order.** The platform prefers a worse answer to no answer, and
always says which it gave: trained model → cold-start scorecard; SHAP → importance
fallback; Redis → in-process; Kafka → in-process; LLM → deterministic template.

---

## 5. Consistency and idempotency

Two guards, because they fail differently:

1. **Cache claim** — fast, but a flush loses it.
2. **`ingested_events` ledger** — durable, survives everything, checked first.

Downstream consumers are independently idempotent (an existing alert
short-circuits case creation), so at-least-once delivery cannot duplicate work.

The decision path is one transaction: the transaction row, feature vector,
prediction, risk score, decision and idempotency ledger entry commit together, or
none of them do. Events publish only after that commit.

---

## 6. Security model

- **Authentication** — short-lived JWT access tokens; refresh tokens are stored
  server-side, rotated on every use, and presenting a retired token revokes the
  whole family (reuse implies theft).
- **Authorisation** — 30 permissions, 7 roles. Routes declare permissions, never
  roles, so the matrix can change without touching route code.
- **PII** — masked in the serialisation layer that every response passes
  through. A new endpoint cannot leak by forgetting.
- **AI** — prompt sanitisation, evidence grounding, SQL allow-list filtered by
  caller permissions, PII column blocking, full query logging.
- **Transport and headers** — CORS allow-list, `nosniff`, `DENY`,
  `Referrer-Policy`, `Permissions-Policy`, HSTS in production.
- **Configuration** — the app refuses to boot in production mode with a
  development JWT secret, on SQLite, or with debug enabled.

Residual risks worth stating: HS256 shared-secret JWTs (asymmetric keys would be
better for multi-service deployments); no per-tenant isolation; rate limiting is
per-process unless Redis is configured.

---

## 7. Scaling to 5,000 TPS

Nothing here is measured — it is the plan the architecture was built for.

At 5,000 TPS with the current 14 ms median, a single process handles ~70/s, so
the shape is roughly:

| Layer | Change |
|---|---|
| Ingestion | Producers write to `transactions.raw`; the API stops being the entry point for machine traffic |
| Scoring | ~80 stateless scorer replicas consuming partitions keyed by `customer_id`, so one customer's velocity state stays on one consumer |
| Features | Velocity aggregates move to Redis sorted sets updated by the stream, removing the per-decision history query (the largest single cost after inference) |
| Inference | ONNX export and batched scoring; SHAP computed asynchronously for flagged transactions only, not for every approval |
| Writes | `transactions` partitioned by month; feature-store writes batched; predictions written by a separate consumer |
| Reads | Analytics served from a read replica, then from the dbt marts rather than the operational tables |
| Graph | Ring detection moves to a scheduled Spark/GraphFrames job; online risk stays query-based against Redis-cached fan-out counters |

The two things that would need re-engineering rather than scaling: SHAP on the
hot path (already the largest component of inference), and the per-decision
`transaction_features` insert.

---

## 8. Disaster recovery

| Scenario | Target | Mechanism |
|---|---|---|
| AZ failure | RTO < 5 min | Multi-AZ RDS failover; stateless tasks reschedule |
| Region failure | RTO < 4 h | Cross-region snapshot restore; artifacts replicated in S3 |
| Data corruption | RPO < 5 min | Point-in-time recovery; the event log allows replay |
| Bad model promoted | RTO < 1 min | One-click rollback to the archived version |
| Bad rule activated | RTO < 1 min | Deactivate from the UI; the change is audited |
| Accidental deletion | — | Soft deletion on customers, merchants, rules and users |

---

## 9. Observability

- **Logs** — structured JSON with a `request_id` propagated through services and
  event consumers via a context variable.
- **Metrics** — in-process histograms and counters exposed as JSON for the
  System Health screen and in Prometheus text format at `/metrics`.
- **Traces** — the decision trace is a domain-level trace: every stage with its
  measured latency and the evidence it contributed, persisted per transaction.
- **Health** — `/health` (liveness) and `/ready` (database, cache, bus).

Percentiles are computed over a bounded in-memory window (2,000 samples per
metric) and the API says so, rather than implying all-time coverage.

---

## 10. What a reviewer should check

1. `app/services/pipeline.py` — the decision path, its ordering guarantees and
   the deferred publish.
2. `app/services/features.py` — point-in-time correctness and the injection
   points the batch path uses.
3. `app/services/rules.py` — the grammar, its validation and why there is no
   `eval`.
4. `app/ml/train.py` — chronological splitting and cost-based threshold
   selection.
5. `app/services/ai/text_to_sql.py` — the guardrails, and that they run *after*
   generation.
6. `backend/tests/test_features_and_security.py::TestFeatureComputation` — the
   two correctness tests that keep the ML honest.
