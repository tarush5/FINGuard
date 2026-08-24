# FINGuard architecture diagrams

Eight views of the platform. Each renders on GitHub without a plugin.

1. [System architecture](#1-system-architecture)
2. [Transaction lifecycle](#2-transaction-lifecycle)
3. [Fraud detection pipeline](#3-fraud-detection-pipeline)
4. [Event bus topology](#4-event-bus-topology)
5. [ML lifecycle](#5-ml-lifecycle)
6. [AI investigation workflow](#6-ai-investigation-workflow)
7. [Database relationships](#7-database-relationships)
8. [Deployment architecture](#8-deployment-architecture)

---

## 1. System architecture

```mermaid
flowchart TB
    subgraph Edge
        BROWSER[Browser · React SPA]
        PRODUCER[Payment switch / stream producer]
    end

    subgraph Application["FastAPI application"]
        direction TB
        MW["Middleware<br/>request id · rate limit · security headers · timing"]
        subgraph Routers
            R1[auth]
            R2[transactions]
            R3[entities]
            R4[risk & rules]
            R5[fraud · cases · graph]
            R6[analytics · forecasting]
            R7[mlops]
            R8[platform]
            R9[ai]
            R10[governance]
        end
        subgraph Services
            S1[Feature service]
            S2[Rule engine]
            S3[Model service]
            S4[Graph service]
            S5[Risk ensemble]
            S6[Decision engine]
            S7[Case service]
            S8[Analytics]
            S9[AI orchestrator]
        end
    end

    subgraph Bus["Event bus"]
        K[(Kafka · or in-process)]
    end

    subgraph Workers["Consumers"]
        C1[Risk consumer → alerts + cases]
        C2[Notification consumers]
        C3[Feedback consumer]
        C4[Metrics consumers]
    end

    subgraph Data
        PG[(PostgreSQL · 41 tables)]
        RD[(Redis · cache · idempotency · rate limits)]
        S3B[(Object store · model artifacts)]
        MLF[(MLflow · optional)]
    end

    subgraph Batch["Scheduled (Airflow)"]
        B1[Quality suite]
        B2[Risk aggregates]
        B3[Ring detection]
        B4[Drift monitor]
        B5[Retraining gate]
    end

    BROWSER --> MW
    PRODUCER --> MW
    MW --> Routers --> Services
    S1 & S2 & S3 & S4 --> S5 --> S6
    S6 --> PG
    S6 -.after commit.-> K
    K --> Workers --> PG
    S1 <--> RD
    S3 <--> S3B
    S3 -.optional.-> MLF
    Batch --> PG
    Services --> PG
```

---

## 2. Transaction lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant P as Producer
    participant API as FastAPI
    participant CACHE as Redis
    participant DB as PostgreSQL
    participant FS as Feature service
    participant RE as Rule engine
    participant ML as Model service
    participant GR as Graph service
    participant BUS as Event bus
    participant CON as Risk consumer

    P->>API: POST /transactions (event_id)
    API->>API: Validate schema, amount, currency, coordinates
    API->>DB: SELECT ingested_events WHERE event_id
    API->>CACHE: claim idem:txn:{event_id}
    alt already processed
        API-->>P: 200 duplicate=true, original decision
    else new event
        API->>DB: Load customer, merchant, account, device
        API->>FS: compute_features (history strictly < occurred_at)
        FS->>DB: SELECT prior transactions
        FS-->>API: 35 features
        API->>RE: evaluate(namespace, active rules)
        RE-->>API: rule score, triggered hits, forced action
        API->>ML: predict_fraud + anomaly + customer risk
        ML-->>API: probability, SHAP attributions, model version
        API->>GR: graph_risk(device, ip, merchant, rings)
        GR-->>API: score + signals
        API->>API: Ensemble → 0-100 → decision policy
        API->>DB: INSERT transaction, features, prediction, risk_score, decision, ingested_event
        API->>DB: COMMIT
        API->>BUS: publish validated / prediction / risk events
        API-->>P: 200 decision + full trace
        BUS->>CON: risk.decided
        CON->>DB: INSERT alert (+ case when review or decline)
        CON->>BUS: alerts.created / cases.created
    end
```

> Events are published **after** the commit. Consumers run concurrently and must
> never observe an event for a row that is not yet visible.

---

## 3. Fraud detection pipeline

```mermaid
flowchart LR
    IN[Transaction] --> V{Valid?}
    V -- no --> REJ[422 with field errors]
    V -- yes --> D{Seen event_id?}
    D -- yes --> DUP[Return original decision]
    D -- no --> E[Enrich]

    E --> F["Features (35)<br/>amount · velocity · geo · device · merchant · behaviour"]

    F --> R[Rule engine]
    F --> M[XGBoost classifier]
    F --> A[Isolation forest]
    F --> G[Graph neighbourhood]
    C[Customer profile] --> ENS
    MR[Merchant profile] --> ENS

    R -->|0-100 points| ENS[Weighted ensemble]
    M -->|probability| ENS
    A -->|anomaly| ENS
    G -->|graph risk| ENS

    ENS --> SCORE[Final score 0-100]
    SCORE --> POL{Threshold policy}
    POL -->|< 30| AP[APPROVE]
    POL -->|30-70| SU[STEP-UP]
    POL -->|70-85| MRV[MANUAL REVIEW]
    POL -->|>= 85| DE[DECLINE]
    R -.DECLINE / REVIEW action.-> POL

    MRV --> CASE[Case opened]
    DE --> CASE
    SU --> ALERT[Alert only]
    CASE --> INV[Analyst investigation]
    INV --> FB[Verdict → feedback label]
    FB --> TRAIN[Retraining dataset]
```

Ensemble weights (normalised, configurable per simulation):

| Component | Weight |
|---|---|
| Model probability | 0.40 |
| Rule score | 0.22 |
| Graph risk | 0.14 |
| Anomaly score | 0.10 |
| Customer risk | 0.08 |
| Merchant risk | 0.06 |

---

## 4. Event bus topology

```mermaid
flowchart TB
    subgraph Producers
        API[API decision path]
        CASE[Case service]
        TRAIN[Training pipeline]
        SYS[System monitors]
    end

    subgraph Topics
        T1[transactions.raw]
        T2[transactions.validated]
        T3[transactions.enriched]
        T4[fraud.predictions]
        T5[risk.events]
        T6[alerts.created]
        T7[cases.created]
        T8[analyst.feedback]
        T9[model.events]
        T10[system.events]
    end

    subgraph Consumers
        C1[Metrics collector]
        C2[Risk consumer]
        C3[Alert notifier]
        C4[Case notifier]
        C5[Feedback recorder]
        C6[Model notifier]
    end

    DLQ[(topic.dlq — replayable from the UI)]

    API --> T2 & T4 & T5
    CASE --> T6 & T7 & T8
    TRAIN --> T9
    SYS --> T10

    T2 --> C1
    T4 --> C1
    T5 --> C2 --> T6 & T7
    T6 --> C3
    T7 --> C4
    T8 --> C5
    T9 --> C6

    Consumers -.3 retries with exponential backoff.-> DLQ
```

Delivery semantics: at-least-once. Handlers are idempotent (an existing alert
for a transaction short-circuits), retries use exponential backoff, and an
exhausted event is written to `dead_letter_events` with its payload, error and
attempt count — replayable from the System Health screen.

---

## 5. ML lifecycle

```mermaid
flowchart TB
    FS[(Feature store<br/>transaction_features)] --> DS[Build dataset]
    LAB[(Labels: synthetic + analyst verdicts)] --> DS

    DS --> VAL{Enough rows<br/>and positives?}
    VAL -- no --> STOP[Refuse with INSUFFICIENT_TRAINING_DATA]
    VAL -- yes --> SPLIT["Chronological split<br/>70 train / 15 validation / 15 test"]

    SPLIT --> FIT["Fit XGBoost<br/>scale_pos_weight from class ratio"]
    FIT --> THR["Sweep thresholds on validation<br/>minimise expected cost"]
    THR --> EVAL["Evaluate on test window<br/>ROC-AUC · PR-AUC · precision · recall · confusion"]
    EVAL --> BASE[Store SHAP baseline statistics]
    BASE --> ART[(Artifact: estimator + features + threshold)]
    ART --> REG[(model_versions — STAGING)]
    REG -.optional.-> MLF[(MLflow run + model uri)]

    REG --> PROMO{Promote?}
    PROMO -- yes --> PROD[PRODUCTION · incumbent archived]
    PROD --> SERVE[Serving: cached bundle + SHAP explainer]
    PROD --> ROLL[Rollback restores the archived version]

    SERVE --> PRED[(fraud_predictions)]
    PRED --> DRIFT[PSI + KS vs baseline]
    DRIFT --> ALERTM{Above threshold?}
    ALERTM -- yes --> EV[model.events → data science notified]

    CASES[Analyst verdicts] --> FBL[(feedback_labels)]
    FBL --> GATE{Enough new labels<br/>or drift?}
    GATE -- yes --> DS
    GATE -- no --> WAIT[Wait]
```

---

## 6. AI investigation workflow

```mermaid
flowchart TB
    Q[Analyst question] --> SAN[Sanitise input · strip injection scaffolding]
    SAN --> CTX{Context}
    CTX -->|transaction or case| EV[Assemble evidence from the database]

    EV --> E1[Amount vs customer profile]
    EV --> E2[Device and IP fan-out]
    EV --> E3[Velocity]
    EV --> E4[Impossible travel]
    EV --> E5[Merchant fraud rate]
    EV --> E6[Triggered rules]
    EV --> E7[Graph signals]
    EV --> E8[Model attributions]

    E1 & E2 & E3 & E4 & E5 & E6 & E7 & E8 --> BUNDLE[Evidence bundle]

    BUNDLE --> PROV{LLM configured?}
    PROV -- yes --> LLM["Narrate evidence<br/>grounding rules in the system prompt"]
    PROV -- no --> TPL[Deterministic template]
    LLM --> ANS[Answer + generated_by=llm]
    TPL --> ANS2[Answer + generated_by=deterministic]

    ANS & ANS2 --> LOG[(ai_queries: question, provider, evidence, latency)]
    LOG --> UI[Answer shown beside the evidence it came from]

    SQLQ[Analytics question] --> GEN{LLM configured?}
    GEN -- yes --> GSQL[Model generates SQL]
    GEN -- no --> INT[Match curated intent library]
    GSQL & INT --> GUARD["Validator<br/>single SELECT · no comments · allow-listed tables<br/>RBAC filter · PII check · LIMIT injected"]
    GUARD -- rejected --> BLOCK[400 UNSAFE_QUERY · logged]
    GUARD -- accepted --> EXEC[Execute read-only]
    EXEC --> VIS[Results + SQL shown + chart hint]
```

---

## 7. Database relationships

```mermaid
erDiagram
    CUSTOMERS ||--o{ ACCOUNTS : owns
    CUSTOMERS ||--o{ TRANSACTIONS : initiates
    CUSTOMERS ||--o{ DEVICE_LINKS : uses
    DEVICES   ||--o{ DEVICE_LINKS : "shared by"
    DEVICES   ||--o{ TRANSACTIONS : "originates from"
    MERCHANTS ||--o{ TRANSACTIONS : receives
    ACCOUNTS  ||--o{ TRANSACTIONS : "funds"

    TRANSACTIONS ||--|| TRANSACTION_FEATURES : "feature vector"
    TRANSACTIONS ||--o{ FRAUD_PREDICTIONS : "scored by"
    TRANSACTIONS ||--o{ RISK_SCORES : "ensemble"
    TRANSACTIONS ||--o{ DECISIONS : "decided"
    TRANSACTIONS ||--o{ RULE_EXECUTIONS : "evaluated"
    TRANSACTIONS ||--o{ ALERTS : raises
    TRANSACTIONS ||--o{ FEEDBACK_LABELS : "labelled by"

    RULES ||--o{ RULE_EXECUTIONS : produces
    ALERTS }o--|| CASES : "escalates into"
    CASES ||--o{ CASE_EVENTS : timeline
    CASES ||--o{ CASE_NOTES : notes
    CASES ||--o{ FEEDBACK_LABELS : "verdict"
    FRAUD_RINGS ||--o{ FRAUD_RING_MEMBERS : contains

    MODEL_VERSIONS ||--o{ MODEL_METRICS : records
    MODEL_VERSIONS ||--o{ DRIFT_METRICS : monitors
    MODEL_VERSIONS ||--o{ TRAINING_RUNS : "produced by"

    USERS }o--o{ ROLES : "assigned"
    ROLES }o--o{ PERMISSIONS : grants
    USERS ||--o{ REFRESH_TOKENS : holds
    USERS ||--o{ AUDIT_LOGS : performs
    USERS ||--o{ AI_QUERIES : asks

    DATASETS ||--o{ QUALITY_CHECKS : "checked by"
    DATASETS ||--o{ LINEAGE_EDGES : "flows into"

    CUSTOMERS {
        string id PK
        string email "masked by role"
        float avg_transaction_amount
        int confirmed_fraud_count
        float risk_score
        bool watchlisted
    }
    TRANSACTIONS {
        string id PK
        string event_id UK "idempotency key"
        float amount
        datetime occurred_at
        float risk_score
        string decision
        bool is_fraud "nullable = unreviewed"
    }
    CASES {
        string id PK
        string case_number UK
        string status
        float risk_score
        string resolution
    }
```

---

## 8. Deployment architecture

```mermaid
flowchart TB
    subgraph Internet
        U[Analysts]
    end

    subgraph AWS
        subgraph Edge
            CF[CloudFront + WAF]
            ALB[Application Load Balancer]
        end

        subgraph Static
            S3W[(S3 · SPA bundle)]
        end

        subgraph Compute["ECS Fargate"]
            API1[api task 1]
            API2[api task 2]
            APIN[api task N · autoscaled on p95 latency]
            WORK[consumer tasks]
            TRAIN[training task · scheduled]
        end

        subgraph Managed
            RDS[(RDS PostgreSQL Multi-AZ<br/>+ read replica)]
            EC[(ElastiCache Redis)]
            MSK[(MSK Kafka)]
            S3A[(S3 · model artifacts + warehouse)]
            SM[Secrets Manager]
        end

        subgraph Observability
            CW[CloudWatch logs and metrics]
            PROM[Prometheus scrape /metrics]
        end

        MWAA[MWAA · Airflow DAGs]
        MLFS[MLflow on ECS]
    end

    U --> CF
    CF --> S3W
    CF --> ALB --> API1 & API2 & APIN
    API1 & API2 & APIN --> RDS & EC & MSK & S3A
    MSK --> WORK --> RDS
    TRAIN --> S3A & RDS
    MWAA --> ALB
    API1 -.metrics.-> PROM
    Compute --> CW
    SM -.injected at task start.-> Compute
    MLFS --> RDS & S3A
```

Scaling levers, in the order they are reached:

| Constraint | Lever |
|---|---|
| API CPU | More Fargate tasks; the API is stateless |
| Feature query latency | Redis velocity aggregates; read replica for history |
| Model inference | Larger task, ONNX export, or a dedicated inference service |
| Event throughput | More Kafka partitions and consumer replicas (keyed by customer) |
| Write throughput | Partition `transactions` by month; batch feature-store writes |
| Analytics | Read replica, then a warehouse fed by the dbt marts |
