# Workspace quotas and API rate limits

Quota values are configured with environment variables and have these defaults:

| Resource | Default |
|---|---:|
| Datasets per workspace | 20 |
| Test cases per dataset | 10,000 |
| Knowledge-base passages per workspace | 25,000 |
| Queued evaluation jobs per workspace | 20 |
| Active evaluation jobs per workspace | 1 |
| Queued evaluation jobs across the installation | 100 (existing limit) |
| Authenticated read requests | 300 per user per minute |
| Authenticated writes | 60 per user per minute |
| Job submissions | 5 per user per minute |

Override values with `EVALOPS_MAX_DATASETS_PER_WORKSPACE`,
`EVALOPS_MAX_TEST_CASES_PER_DATASET`, `EVALOPS_MAX_PASSAGES_PER_WORKSPACE`,
`EVALOPS_MAX_QUEUED_JOBS_PER_WORKSPACE`, `EVALOPS_MAX_ACTIVE_JOBS_PER_WORKSPACE`,
`EVALOPS_API_READ_RPM`, `EVALOPS_API_WRITE_RPM`, and
`EVALOPS_API_JOB_SUBMISSIONS_RPM`. Values must be positive integers.

Dataset, case, passage, and job submission quota checks run in the same serialized database
write transaction as the corresponding insert. SQLite uses `BEGIN IMMEDIATE`; PostgreSQL locks
the owning workspace row. The job service checks idempotency before reserving queue capacity, so
an identical replay is not charged twice. The worker claims the oldest currently eligible job
and skips workspaces already at their active-job limit. Retried jobs waiting for queue capacity
hold a queued allowance but no active lease. Cancellation, terminal completion, and retry
recovery release or retain capacity according to the job state.

Authenticated rate counters are stored in `api_rate_limit_buckets`, shared by API processes,
and incremented with a database upsert. Responses include `Retry-After`. Buckets older than two
minutes are pruned during authenticated requests.

Apply migrations through `0014` before starting API or worker processes. SQLite serializes
writes globally, so quota updates are correct across local processes but write-heavy concurrent
workspaces may contend. Limits are process configuration: API and worker processes must use the
same values. SQLite does not provide a distributed rate-limit service or high write concurrency;
PostgreSQL is recommended before scaling beyond a small local deployment. Rate limiting is per
authenticated user, not per source IP, and unauthenticated login/liveness routes are outside
these application request buckets.
