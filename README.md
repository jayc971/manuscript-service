# Manuscript Submission Service

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
