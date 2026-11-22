# Deployment Guide

## Target: 10,000 Manuscripts per Day

**Average load:** ~7 submissions/minute, ~0.12/second.  
**Peak assumption:** 10x burst = 70 submissions/minute for short periods.  
**File size:** up to 50 MB per upload.

---

## Production Architecture

```
                        ┌─────────────────────────────────┐
Internet ──► ALB/Nginx  │  API (3 replicas)               │
                        │  Worker (10 replicas)            │
                        │  Frontend (2 replicas, CDN edge) │
                        └──────────┬──────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼               ▼
              PostgreSQL       Redis           S3 / Object
              (RDS/managed)  (ElastiCache/     Storage
              + PgBouncer     managed)         (uploads)
```

The docker-compose setup maps directly to this. Each service becomes its own
container group; the shared `uploads` volume becomes an S3 bucket.

---

## Scaling

### Workers

Each manuscript takes 7–10 seconds end-to-end (mostly similarity service latency).
One worker handles ~6–8 jobs/minute. For the 10x peak of 70/minute, **10 workers**
keep the queue draining in real time. Workers are stateless — scale by increasing
the replica count with no code changes.

```bash
# Kubernetes / ECS example
replicas: 10
```

**Similarity semaphore with multiple workers:** The current `asyncio.Semaphore(4)`
is per-process. With 10 workers that is 40 concurrent similarity calls. If the
external service has a global limit of 4, replace the in-process semaphore with a
Redis-backed distributed lock (e.g. `aioredlock`). The swap is local to `tasks.py`.

### API

3 replicas behind a load balancer handles the ingest comfortably. The API does
only: validate input, write one DB row, enqueue one Redis job. Each request
completes in under 100 ms.

### Database

- Use **PgBouncer** in transaction-pooling mode in front of PostgreSQL. The async
  SQLAlchemy pool (`pool_size=10, max_overflow=5`) per API replica can produce
  30+ connections under burst; PgBouncer keeps actual server connections low.
- For 10k manuscripts/day the data volume is small. A single `db.t3.medium` RDS
  instance (or equivalent) is sufficient. Add a read replica if the editor list
  query becomes slow (it scans all manuscripts).

### File uploads (50 MB files)

Local volume storage breaks the moment there are multiple API replicas. Switch to
S3 (or any object store):

1. `save_upload` in `manuscript_service.py` streams directly to S3 using
   `aioboto3` — never buffer the whole file in memory.
2. Workers read from S3 by key. The `file_path` column stores the S3 key instead
   of a local path.
3. Set a 60-second timeout on the API and load balancer for large uploads.
4. Enable S3 multipart upload for files over 10 MB.

---

## Migrations

Run Alembic **before** starting new API/worker containers — never during.

```bash
# In CI/CD pipeline, after image build, before deploy:
docker run --rm \
  --env-file .env.production \
  manuscript-api:$VERSION \
  python -m alembic upgrade head
```

Use an **init container** pattern (Kubernetes) or an ECS task dependency so the
migration job must succeed before any API replica starts. This prevents new code
from hitting an old schema.

For additive changes (new nullable column, new table) this is zero-downtime.
For breaking changes (column rename, type change), use a two-phase deploy:
phase 1 adds the new shape, phase 2 removes the old one after rollout.

---

## Environments and Releases

### Three environments

| | dev | staging | production |
|-|-----|---------|------------|
| Infrastructure | docker-compose, laptop or shared VM | Same as prod at smaller scale (1 API, 2 workers) | Full scale — see architecture diagram |
| Database | Local postgres container | Anonymised copy of prod data | RDS Multi-AZ |
| File storage | Local volume | S3 staging bucket | S3 production bucket |
| Notify / Similarity | Mock service (port 9000) | Mock or sandboxed vendor endpoint | Live external services |
| Secrets | `.env` file | Injected by CI/CD from secrets manager | AWS Secrets Manager / IAM roles |

### Promoting a change

1. Merge to `main` → CI builds and tags the Docker image (semver or git SHA).
2. Deploy the tagged image to **staging**. Run smoke tests: submit one manuscript, confirm all 5 stages complete, check `/notify/log` for no duplicates.
3. If staging is green, deploy the **same image tag** to production. Never rebuild for production — the image that passed staging is the image that ships.
4. If a migration is included: the Alembic init container runs first. If it fails, the deploy stops and no new API replicas start.

### In-flight jobs during a deploy

ARQ workers respect SIGTERM — they finish the job they are currently running before exiting. The rolling deploy strategy:

1. Start new worker replicas (new code, consuming new jobs).
2. Send SIGTERM to old replicas. Each finishes its current job, then exits cleanly.
3. The queue drains continuously throughout. No jobs are lost.

The API replicas are stateless and can be replaced in parallel with no coordination. The database migration runs as a dependency before any new container starts, so there is never a moment when new code hits an old schema.

---

## Secrets

Never commit `.env` files. Inject at runtime:

| Secret | Source |
|--------|--------|
| `DATABASE_URL` | AWS Secrets Manager / GCP Secret Manager |
| `SECRET_KEY` (JWT signing) | Secrets Manager, rotate every 90 days |
| `REDIS_URL` | Injected by infrastructure (ECS task def / K8s secret) |
| `SIMILARITY_SERVICE_URL` | Environment variable per deployment stage |
| AWS credentials (S3) | IAM instance role — no keys in env |

Rotate `SECRET_KEY` by running both old and new keys in parallel for the JWT
TTL window (24 hours), then removing the old one.

---

## Observability

**Structured logs** are already in place — every route and worker task logs with
`[manuscript_id]` as the first field. Ship to CloudWatch Logs / Datadog with
JSON parsing.

**Metrics to add** (Prometheus + Grafana or CloudWatch):

| Metric | Alert threshold |
|--------|----------------|
| `queue_depth` (pending ARQ jobs) | > 500 for 5 min |
| `stage_error_rate` | > 2% over 10 min |
| `similarity_retry_count` | > 3 retries/job average |
| `manuscripts_stuck_in_received` | > 0 for 10 min (enqueue failure) |
| API p99 latency on `POST /manuscripts` | > 500 ms |

**Traces:** Add OpenTelemetry to FastAPI (one middleware line) and ARQ tasks.
Trace IDs propagate from the HTTP request through to the worker job, so a failed
pipeline stage links back to the original submission in the trace.

---

## Cost Estimate (AWS, 10k/day)

| Component | Spec | Monthly cost |
|-----------|------|-------------|
| ECS Fargate — API (3 × 0.5 vCPU / 1 GB) | ~$25/mo each | ~$75 |
| ECS Fargate — Worker (10 × 1 vCPU / 2 GB) | ~$70/mo each | ~$700 |
| RDS PostgreSQL db.t3.medium | Multi-AZ | ~$120 |
| ElastiCache Redis cache.t3.micro | | ~$25 |
| S3 (10k files × 50 MB avg = 500 GB) | + transfer | ~$12 |
| ALB | | ~$20 |
| **Total** | | **~$950/month** |

Worker count drives most of the cost. If latency SLA allows a longer queue
drain time (e.g. 30 minutes), 3 workers (~$210/month total) are enough for
average load with auto-scaling to 10 on burst.

---

## When Things Go Wrong

### Similarity service is down for an hour

Jobs that reach the `similarity_checked` stage fail and retry with exponential backoff (2 s → 4 s → 8 s). After 4 attempts the job is moved to the ARQ dead-letter queue and the stage is marked `error` with the last exception message. The manuscript's overall status becomes `error` so the author and editor can see what happened. All other manuscripts whose pipeline does not currently need the similarity service continue processing normally. When the service recovers, an operator can replay dead-letter jobs by re-enqueuing them with the manuscript ID.

### A job keeps crashing its worker (poison pill)

A malformed file — valid-looking text with embedded binary sequences, an encoding edge case — can cause a stage to crash on every attempt. After `max_retries` (4) the job is not re-queued and the stage is marked `error`. The ARQ dead-letter queue holds the job metadata for manual inspection. The worker itself is not affected; it moves to the next job immediately after the exception is caught and logged. Recovery: read the error from the manuscript's stage record, fix the file or the extraction logic, then replay the job.

There is no automatic poison-pill detection beyond the retry cap. A future improvement: alert on-call if the same job ID fails more than twice within a short window.

### Queue (Redis) becomes unavailable

The API still accepts submissions — it writes the manuscript row to the database — but the `enqueue_job` call raises and the endpoint returns 500. The manuscript row exists in `received` status with no stages started. It is not lost. When Redis recovers, an operator can replay any manuscript stuck in `received` by re-enqueuing its ID. Active workers that cannot reach Redis to fetch the next job wait with backoff and resume automatically when connectivity returns.

### Database goes down for a minute

API requests that need a database connection fail with 503 (the SQLAlchemy pool raises before any business logic runs). Workers that try to write stage updates also fail and retry with backoff. There is a small race window in the `author_notified` stage: the worker calls the notify service and then writes the `NotificationKey` row. If the database goes down between those two steps, the email was sent but the key was not recorded. On retry, the email is sent again — the notify service's idempotency key deduplicates it on their side, so the author receives only one email. No other stage has this window because the external call is the last step.
