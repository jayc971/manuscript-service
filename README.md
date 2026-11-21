# Manuscript Submission Service

## Queue Decisions

### Direct vs Queued

| Action | Approach | Why |
|---|---|---|
| User logs in | Direct | Synchronous and fast. The user is waiting for a response. |
| Author uploads a manuscript | Direct (202) then queue the pipeline | The endpoint returns an ID immediately. All processing happens in the background. |
| Editor opens the manuscript list | Direct | A straightforward database read with no heavy work involved. |
| Extracting text from a 50 MB file | Queued | This can take several seconds and would block the request thread if done inline. |
| Calling the similarity service | Queued | The service is slow (1 to 3 seconds), fails occasionally and only allows 4 concurrent requests. It has to run in the background with concurrency control. |
| Sending the "received" email | Queued | A failure here must not roll back the submission. It also needs an idempotency key to prevent sending the same email twice on retry. |
| Editor accepts a manuscript and the author needs to see it immediately | Direct DB write then queued email | The status update is a fast write so it happens inline. The email goes on the queue since a small delay is acceptable. |
| Updating a search index after a status change | Queued | The index does not need to be updated in real time. A short lag is fine. |
| Generating a nightly summary report | Scheduled job | This is time-driven not event-driven so it belongs in a cron scheduler rather than a queue. |
| Deleting an author account and all their data | Queued | Cascading deletes across multiple tables are slow and risky to run inside a request. |

### Delivery Guarantee

The queue gives at least once delivery. A worker only acknowledges a job after it completes successfully. If the worker crashes mid-job the job becomes visible again after a timeout and gets picked up by another worker. Because jobs can run more than once the code is written to be safe on retry. The similarity check and email notification both use idempotency keys so running them twice produces the same result as running them once.

### If the similarity service is down for an hour

Jobs that call the similarity service will fail and go into retry with exponential backoff. If the service returns a 429 with a Retry-After header the worker waits that long before trying again. After a set number of attempts the job moves to a dead letter queue and the manuscript stage is updated with the last error message so the author and editor can see what happened. All other pipeline stages that do not depend on similarity continue processing normally. When the service comes back up an operator can replay the dead lettered jobs.

### If 1000 manuscripts arrive in one minute

The submit endpoint returns 202 immediately for each one and puts a job on the queue. The workers process jobs at their own pace. The similarity service concurrency is capped at 4 at a time using a semaphore so the rate limit is respected regardless of how many jobs are queued. The system slows down gracefully under load and nothing is dropped.

### If a worker crashes halfway through a job

The job has a visibility timeout. When the timeout expires the queue makes the job visible again and another worker picks it up. Because all side effects (emails, status updates) use idempotency keys a job that runs a second time produces the same outcome as the first run.

### When I would pick a different queue technology

Redis with ARQ is the right call for this assignment because it is simple to run in docker-compose and async-native which fits FastAPI well. In production at scale I would look at a managed queue like Amazon SQS or Google Cloud Tasks. They handle durability and delivery guarantees at the infrastructure level so the application code does not have to. I would also consider RabbitMQ if the team already had it running and needed more routing flexibility.
