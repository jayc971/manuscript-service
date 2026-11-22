# Manuscript Submission Service

## Quick Start

```bash
docker-compose up --build
```

That starts all 6 services: PostgreSQL, Redis, the mock similarity/notify service,
the API, the background worker, and the Next.js frontend.

- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs

### Seed Credentials

| Role | Email | Password |
|------|-------|----------|
| Author | author@example.com | password123 |
| Editor | editor@example.com | password123 |

The seed script runs automatically on API startup. Both users are ready
immediately after `docker-compose up`.

### Bulk Submit (sample data)

```bash
python submit_all.py
# or with options:
python submit_all.py --api http://localhost:8000 --concurrency 5
```

Requires `httpx`: `pip install httpx`. Reads `manifest.csv` from the repo root,
logs in as the author, and submits all manuscripts concurrently.

---

## Schema

Four tables, managed with Alembic migrations (`alembic upgrade head` runs automatically on API startup).

### `users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | string (UUID) | Primary key |
| `email` | string | Unique, indexed |
| `password_hash` | string | bcrypt |
| `role` | enum | `author` \| `editor` |
| `created_at` | timestamptz | |

### `manuscripts`
| Column | Type | Notes |
|--------|------|-------|
| `id` | string | Primary key — format `m_<8-char fragment>` |
| `owner_id` | string | FK → `users.id`, indexed |
| `title` | string | |
| `author_email` | string | Validated as RFC-5322 email at submit time |
| `file_path` | string | Local path (S3 key in production — see DEPLOYMENT.md) |
| `file_name` | string | Sanitised original filename |
| `status` | enum | `received` → `processing` → `done` / `error` |
| `editor_status` | enum | `pending` / `accepted` / `rejected` / `revision_requested` |
| `priority` | enum | `normal` \| `high` |
| `word_count` | integer | Null until `text_extracted` stage completes |
| `preview` | text | First 200 words, null until `preview_built` stage |
| `similarity_score` | float | 0–1 from similarity service, null until checked |
| `needs_attention` | boolean | True if similarity score > 0.8 |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | Auto-updated on change |

### `manuscript_stages`
One row per stage per manuscript. Five stages are created when a manuscript is submitted.

| Column | Type | Notes |
|--------|------|-------|
| `id` | string (UUID) | Primary key |
| `manuscript_id` | string | FK → `manuscripts.id`, indexed |
| `name` | string | `validated` / `text_extracted` / `similarity_checked` / `preview_built` / `author_notified` |
| `status` | enum | `pending` / `running` / `done` / `retrying` / `error` |
| `started_at` | timestamptz | Set when stage begins |
| `finished_at` | timestamptz | Set when stage completes |
| `attempts` | integer | Incremented on each retry |
| `last_error` | text | Last exception message, visible in the UI |

### `notification_keys`
Deduplication table — prevents the same email being sent twice even if a job retries.

| Column | Type | Notes |
|--------|------|-------|
| `id` | string (UUID) | Primary key |
| `key` | string | Unique — format `{manuscript_id}:{event_type}` |
| `created_at` | timestamptz | |

Before calling `/notify`, the worker inserts this key. If the insert fails (key already exists), the email was already sent and the call is skipped.

---

## Sample Data Problems

The provided sample data contains intentional quality issues. These are handled
gracefully by the service:

| File | Problem | Result |
|------|---------|--------|
| `ms_013.txt` | Empty file (0 bytes) | HTTP 422 — rejected at validation stage |
| `ms_014.txt` | Not a plain text file (binary/wrong encoding) | HTTP 422 — rejected at validation stage |
| `ms_019.txt` | Invalid `author_email`: `"not-an-email"` | HTTP 422 — rejected by API validation |
| `ms_020.txt` | Listed in manifest but file does not exist | Skipped by `submit_all.py` with reason "file not found" |
| `ms_015.txt` | Large file (7.4 MB) | Accepted — within the 50 MB limit; goes through the full pipeline |
| `ms_018 final (v2).txt` | Spaces and special characters in filename | Accepted — `sanitise_filename()` strips unsafe characters before saving |

---

## Why Polling Instead of WebSockets

The frontend polls `GET /manuscripts` every 5 seconds rather than using
WebSockets or Server-Sent Events. The assignment (§3.6) explicitly allows
polling with justification:

- **Simplicity:** No persistent connection infrastructure needed. Works behind
  any proxy, CDN, or load balancer with no special configuration.
- **Appropriate interval:** Pipeline stages take 10–30 seconds each. A 5-second
  poll lag is imperceptible to the user.
- **Stateless API:** Each poll is an independent request. No session affinity
  required, which is important when scaling API replicas horizontally.
- **Reliability:** If the connection drops, the next poll recovers automatically.
  A dropped WebSocket requires reconnect logic and state reconciliation.

For a real-time dashboard (sub-second updates) WebSockets or SSE would be the
right choice. For a pipeline that takes minutes, polling is simpler and more robust.

---

## Queue Decisions

### Direct vs Queued

| Action | Approach | Why |
|---|---|---|
| User logs in | Direct | Fast and synchronous. The user is waiting for a response. |
| Author uploads a manuscript | Direct (202) then queue the pipeline | The endpoint returns an ID straight away. All processing happens in the background. |
| Editor opens the manuscript list | Direct | A simple database read with no heavy work involved. |
| Extracting text from a 50 MB file | Queued | This takes time and would hold up the request if done inline. |
| Calling the similarity service | Queued | The service is slow, fails sometimes and only allows 4 calls at a time. It has to run in the background with a cap on how many run at once. |
| Sending the "received" email | Queued | A failure here must not undo the submission. Each email also carries a unique key so the same email is never sent twice even if the job runs again. |
| Editor accepts a manuscript and the author needs to see it immediately | Direct DB write then queued email | Updating the status is quick so it happens inline. The email can go on the queue since a small delay is fine. |
| Updating a search index after a status change | Queued | The index does not need to update in real time. A short delay is acceptable. |
| Generating a nightly summary report | Scheduled job | This runs on a fixed schedule, not in response to an event, so it belongs in a scheduler rather than a queue. |
| Deleting an author account and all their data | Queued | Removing data across multiple tables is slow and risky to do inside a request. |

### Delivery Guarantee

The queue delivers each job at least once. A worker only marks a job done after it finishes successfully. If the worker crashes in the middle of a job, the job becomes available again after a time limit and another worker picks it up. Because a job might run more than once, the code is written so running it twice causes no extra harm. Emails use a unique key per event so the notification service will not send the same email again even if asked to.

### If the similarity service is down for an hour

Jobs that need the similarity service will fail and be retried after a short wait, with each wait a little longer than the last. If the service says to wait a specific amount of time the worker respects that before trying again. After a set number of attempts the job is moved to a separate list of failed jobs and the manuscript stage is updated with the last error so the author and editor can see what went wrong. Everything else in the pipeline that does not depend on similarity carries on as normal. When the service comes back up an operator can replay those failed jobs.

### If 1000 manuscripts arrive in one minute

The submit endpoint returns straight away for each one and adds a job to the queue. Workers process jobs at their own pace. No more than 4 similarity checks run at the same time regardless of how many jobs are waiting. The system slows down gracefully under load and nothing is lost.

### If a worker crashes halfway through a job

The job has a time limit. When that limit runs out the queue makes the job available again and another worker picks it up. Because emails and status updates use unique keys a job that runs a second time produces the same result as the first run, with no duplicates.

### When I would pick a different queue technology

Redis with ARQ works well here because it is simple to set up in docker-compose and fits naturally with async Python. In production at scale I would look at a managed option like Amazon SQS or Google Cloud Tasks because they handle reliability at the infrastructure level and the application code does not have to worry about it. I would consider RabbitMQ if the team already had it running and needed more control over how jobs are routed.

---

## Extra Credit

Beyond the core requirements, the following was implemented:

- **Structured logging** — every log line from the API and worker is prefixed with `[manuscript_id]` where relevant, making it straightforward to trace a single manuscript through docker-compose logs.
- **`needs_attention` flag** — manuscripts with a similarity score above 0.8 are automatically flagged. The frontend shows these highlighted so editors can prioritise them.
- **Bulk submit script** (`submit_all.py`) — reads `manifest.csv`, logs in as the author, and submits all manuscripts concurrently with a configurable semaphore. Useful for load-testing and for reproducing the sample data problems.
- **Priority field** — manuscripts accept `priority: normal | high`. The field is stored and returned in all responses. A separate high-priority queue was not implemented (see Known Issues).
- **Idempotent pipeline resume** — if the worker restarts mid-pipeline, it checks each stage's current status before doing work. Completed stages are skipped rather than re-run.

What I would do differently with more time:
- Replace the per-process `asyncio.Semaphore(4)` for the similarity service with a Redis-backed distributed lock so the limit holds across multiple worker replicas.
- Add a `POST /manuscripts/{id}/retry` endpoint so operators can replay a manuscript stuck in `error` state without touching the queue directly.
- Add an admin endpoint to inspect and replay the dead-letter queue.
- Implement a WebSocket or SSE channel for the frontend — polling at 5 seconds is acceptable for this pipeline, but SSE would halve the perceived latency for free.

---

## Known Issues

| Issue | Detail |
|-------|--------|
| Priority queue not enforced | The `priority` field is stored but all jobs go into the same ARQ queue. High-priority manuscripts are not processed before normal ones. A real implementation would use two separate ARQ pools (one per priority) or a priority-aware queue library. |
| Duplicate text detection | The service checks plagiarism against an external index, but does not detect when the same author submits the same file twice. A content hash on upload could catch exact duplicates before they enter the pipeline. |
| `POST /manuscripts/{id}/retry` not implemented | A manuscript stuck in `error` status can only be replayed by re-submitting it. An operator replay endpoint is noted in Extra Credit above but was deferred. |
| `submit_all.py` skips `ms_020.txt` | The file is listed in `manifest.csv` but does not exist in the sample data directory. The script logs the skip with reason "file not found". |
| File storage is local | Uploads go to a Docker volume. With multiple API replicas this breaks. The DEPLOYMENT.md section covers the S3 migration path. |

---

## AI Assistance

This project was built with Claude Code (Anthropic) as a coding assistant throughout.

Claude helped with:
- Project scaffolding — FastAPI app structure, SQLAlchemy models, Alembic async configuration
- ARQ worker task implementation, retry logic, and dead-letter handling
- Bug fixes — bcrypt 4.x compatibility, Alembic `run_sync` pattern for async engines, import resolution
- Next.js frontend component and polling logic
- DEPLOYMENT.md architecture documentation
- README content, queue decisions write-up, and this section

The architectural decisions — queue design, idempotency approach, 5-stage pipeline design, per-stage DB session strategy — were driven by the assignment requirements and my own judgment about distributed systems tradeoffs.
