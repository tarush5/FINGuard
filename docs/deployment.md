# Deploying FINGuard

A runbook for taking the platform from a laptop to AWS. Nothing here depends on
AWS-specific APIs — the same shape works on GCP, Azure or Kubernetes.

---

## 1. Before anything else

The application refuses to start in production mode unless these are true. That
is deliberate: a misconfigured fraud platform is worse than one that will not
boot.

```bash
ENVIRONMENT=production
DEBUG=false
JWT_SECRET=<64+ random characters>       # python -c "import secrets;print(secrets.token_urlsafe(48))"
DATABASE_URL=postgresql+psycopg2://…     # SQLite is rejected in production
```

Checklist:

- [ ] `JWT_SECRET` generated per environment, stored in a secret manager, never in git
- [ ] `CORS_ORIGINS` narrowed to the real web origin
- [ ] `MASK_PII=true` unless a documented exception exists
- [ ] `PLATFORM_MODE=production` (demo endpoints refuse to run without seeded data anyway)
- [ ] TLS terminated at the load balancer; HSTS is emitted automatically in production
- [ ] Database backups and point-in-time recovery enabled
- [ ] `/health` and `/ready` wired to the load balancer and the orchestrator
- [ ] `/metrics` scraped, alerts configured (see §7)

---

## 2. Topology

| Component | Service | Notes |
|---|---|---|
| Web app | S3 + CloudFront | Static bundle; `VITE_API_URL` baked at build time |
| API | ECS Fargate behind an ALB | Stateless; scale on p95 latency and CPU |
| Consumers | ECS Fargate (separate service) | Same image, `--consumers-only` entrypoint |
| Database | RDS PostgreSQL Multi-AZ + read replica | Analytics reads go to the replica |
| Cache | ElastiCache Redis | Idempotency, rate limits, hot features |
| Streaming | MSK (Kafka) | 3 partitions per topic minimum, keyed by customer |
| Artifacts | S3 | Model artifacts, warehouse Parquet |
| Tracking | MLflow on ECS | Backed by the same PostgreSQL, artifacts in S3 |
| Orchestration | MWAA (Airflow) | Runs the DAGs in `infra/airflow/dags` |
| Secrets | Secrets Manager | Injected as task environment at start |

---

## 3. Build and publish

```bash
# API
docker build -t finguard-api:$GIT_SHA ./backend
docker tag finguard-api:$GIT_SHA $ECR/finguard-api:$GIT_SHA
docker push $ECR/finguard-api:$GIT_SHA

# Web (API path is inlined at build time)
docker build --build-arg VITE_API_URL=https://api.finguard.example/api/v1 \
  -t finguard-web:$GIT_SHA ./frontend
docker push $ECR/finguard-web:$GIT_SHA
```

Or publish the SPA as static files:

```bash
cd frontend && VITE_API_URL=https://api.finguard.example/api/v1 npm run build
aws s3 sync dist/ s3://finguard-web --delete
aws cloudfront create-invalidation --distribution-id $DIST --paths '/*'
```

The CI workflow already builds both images and health-checks the API container
on every push to `main`.

---

## 4. Database

Migrations run as a **one-off task before** the new version starts serving, not
in the application entrypoint — otherwise N tasks race the same migration.

```bash
aws ecs run-task --cluster finguard --task-definition finguard-migrate \
  --overrides '{"containerOverrides":[{"name":"api","command":["alembic","upgrade","head"]}]}'
```

Rules for safe migrations:

1. Additive first: add nullable columns, backfill, then enforce `NOT NULL` in a
   later release.
2. Never rename in one step: add, dual-write, migrate readers, drop.
3. Create indexes `CONCURRENTLY` on large tables (Alembic:
   `op.create_index(..., postgresql_concurrently=True)` in an autocommit block).
4. Every migration must be reversible; CI runs `upgrade → downgrade → upgrade`
   against real PostgreSQL.

Seeding is for demo environments only:

```bash
python -m app.datagen.seed --reset       # never against production data
```

---

## 5. Rollout

Rolling deployment with health gating:

1. Register the new task definition.
2. ECS starts new tasks; the ALB waits for `/ready` (database, cache and bus
   reachable) before routing.
3. Old tasks drain over 30 s.
4. Roll back by re-deploying the previous task definition — the images are
   immutable and tagged by commit.

**Cold start matters here.** The first request after a deploy pays ~3.7 s to load
the model artifact and build the SHAP explainer. Either accept it (health check
grace period ≥ 30 s) or warm the process in the entrypoint before the readiness
probe passes.

---

## 6. Scaling

| Signal | Action |
|---|---|
| API p95 > 200 ms | Add API tasks (target tracking on ALB response time) |
| CPU > 70% sustained | Add API tasks |
| Kafka consumer lag > 5 s | Add consumer tasks, up to the partition count |
| Database CPU > 70% | Move analytics reads to the replica; then partition `transactions` |
| Redis evictions | Increase memory or shorten feature TTLs |

Autoscaling boundaries that matter: consumers cannot exceed the partition count,
and the write path is ultimately bound by a single PostgreSQL primary — the
first structural change at high volume is partitioning `transactions` by month
and batching feature-store writes.

---

## 7. Observability and alerts

Scrape `/metrics` (Prometheus text format). Recommended alerts:

| Alert | Condition | Severity |
|---|---|---|
| Decision latency | p95 > 400 ms for 5 min | warning |
| API errors | 5xx rate > 1% for 5 min | critical |
| Event lag | consumer lag > 30 s | warning |
| Dead letters | any unresolved dead-lettered event | warning |
| Model drift | PSI ≥ 0.25 on a monitored feature | warning |
| Model quality | production PR-AUC below its floor | critical |
| Data trust | trust score < 95% | warning |
| Case SLA | > 25 cases past SLA | warning |
| Database | connection pool exhausted | critical |

Logs are structured JSON with a `request_id` on every line, propagated through
services and event consumers — ship them to CloudWatch or an ELK stack and index
on `request_id`, `actor` and `action`.

---

## 8. Backup and recovery

| What | How | Frequency | Retention |
|---|---|---|---|
| Database | Automated snapshots + PITR | continuous | 30 days |
| Model artifacts | S3 versioning + cross-region replication | on write | indefinite |
| Kafka | Topic retention | 7 days | replayable |
| Configuration | Infrastructure as code in git | per change | full history |

Restore drill (run quarterly):

```bash
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier finguard-prod \
  --target-db-instance-identifier finguard-restore-test \
  --restore-time 2026-08-24T12:00:00Z
# Point a staging API at the restore, run the smoke suite, then delete it.
```

---

## 9. Operational runbooks

**A bad model reached production.** ML Studio → Models → *Roll back*, or
`POST /api/v1/models/{name}/rollback`. The previous version is restored in under
a second; the action is audited. If no earlier version exists, deactivate the
model by demoting it — the platform falls back to the documented scorecard and
labels every response accordingly.

**A rule is causing false positives.** Rules → toggle it off (or set
`is_shadow`, which keeps it evaluated and logged but out of scoring). The change
is audited with the before/after condition. Confirm the effect on the Simulator
before re-enabling.

**Events are dead-lettering.** System Health → Dead letter queue shows the topic,
error, attempt count and payload. Fix the cause, then *Replay*. Do not replay a
poison message repeatedly — it will re-fail and re-queue.

**Thresholds need adjusting.** Use the Simulator: it replays real history through
the same ensemble and decision functions and reports the change in expected
loss, false positives, reviews and customer friction. Adopt the numbers by
updating `DECISION_*` and redeploying.

---

## 10. Environment matrix

| Setting | Development | Staging | Production |
|---|---|---|---|
| `ENVIRONMENT` | development | staging | production |
| `DEBUG` | true | false | false |
| `PLATFORM_MODE` | demo | demo | production |
| Database | SQLite | RDS (small) | RDS Multi-AZ + replica |
| Cache | in-process | ElastiCache | ElastiCache |
| Bus | in-process | MSK | MSK |
| `MASK_PII` | true | true | true |
| Rate limit | 240/min | 240/min | tuned per client |
| Log format | human | JSON | JSON |
| MLflow | off | on | on |

---

## 11. Free-tier deployment: Render + Vercel

The fastest path to a public URL. The API and its PostgreSQL/Redis services run
on Render; the SPA runs on Vercel. Both read configuration from files already in
this repository (`render.yaml`, `frontend/vercel.json`).

### Step 1 — push the repository to GitHub

Both platforms deploy from git.

```bash
git init && git add -A
git commit -m "FINGuard: fraud detection and risk decisioning platform"
git branch -M main
git remote add origin https://github.com/<you>/finguard.git
git push -u origin main
```

### Step 2 — deploy the API on Render

1. <https://dashboard.render.com> → **New** → **Blueprint** → select the repo.
2. Render reads `render.yaml` and provisions three resources: the web service,
   a PostgreSQL database and a key-value (Redis) instance.
3. Two variables are marked `sync: false` and must be entered by hand:
   - `CORS_ORIGINS` — leave blank for now; set it in step 4.
   - `LLM_API_KEY` — only if you want generated narration instead of the
     deterministic templates.
4. **Apply**. First build takes 5–10 minutes: dependency install, migrations,
   then the seeder builds a 6,000-transaction portfolio and trains the models.

Note what the start command does:

```
python -m scripts.bootstrap && uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

`bootstrap` applies migrations and seeds **only** when the database is empty, so
restarts and redeploys never rebuild or duplicate data.

Confirm: `https://finguard-api.onrender.com/health` returns
`{"status":"ok",...}`, and `/docs` serves the OpenAPI UI.

### Step 3 — deploy the SPA on Vercel

1. <https://vercel.com/new> → import the repo.
2. Set **Root Directory** to `frontend`. Vercel detects Vite; `vercel.json`
   supplies the SPA rewrite and cache headers.
3. Add one environment variable:

   | Name | Value |
   |---|---|
   | `VITE_API_URL` | `https://finguard-api.onrender.com/api/v1` |

   It is inlined at build time, so changing it later requires a redeploy.
4. **Deploy**.

### Step 4 — close the CORS loop

Back in Render, set `CORS_ORIGINS` to the Vercel origin and redeploy the API:

```
https://finguard.vercel.app,https://finguard-<you>.vercel.app
```

Include preview domains if you intend to use them. Until this is set the browser
blocks every API call and the login screen simply fails.

### Step 5 — verify

```bash
curl https://finguard-api.onrender.com/health
curl -X POST https://finguard-api.onrender.com/api/v1/auth/login   -H 'Content-Type: application/json'   -d '{"email":"admin@finguard.io","password":"FinGuard#2026"}'
```

Then open the Vercel URL and sign in.

### What the free tier costs you

| Limit | Effect | Mitigation |
|---|---|---|
| Web service sleeps after 15 min idle | First request takes 50–60 s to wake, plus ~4 s to load the model and build the SHAP explainer | Upgrade to Starter ($7/mo), or accept it for a portfolio link and say so |
| 512 MB RAM | Training is the peak; the seed sizes in `render.yaml` are set for it | Keep `SEED_TRANSACTIONS` at 6,000 or lower |
| PostgreSQL expires after 30 days | Database is deleted | Recreate and redeploy; the seeder rebuilds everything |
| No shell on free plan | Cannot run `python -m app.datagen.seed` manually | `SEED_ON_STARTUP=true` handles it |

`MLFLOW_TRACKING_URI` is blank by default and MLflow is *not* installed in
`requirements.txt` — it pulls in Flask, SciPy and more for a feature that is
optional at runtime. Install `requirements-mlflow.txt` alongside it only if you
run a tracking server.

### Change the demo password before sharing a public link

The seeded accounts use a published password. On a public deployment, sign in as
admin and rotate it:

```bash
curl -X POST https://finguard-api.onrender.com/api/v1/auth/change-password   -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'   -d '{"current_password":"FinGuard#2026","new_password":"<something else>"}'
```

The data is synthetic, so the exposure is limited to someone else driving your
demo — but a public write endpoint with a documented password is still a public
write endpoint.
