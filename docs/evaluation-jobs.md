# Durable evaluation jobs

Evaluation jobs are submitted through `POST /evaluations/jobs` and are executed by a separate
local worker. The existing synchronous `POST /evaluations/runs` path remains available and keeps
its existing behavior. Jobs use the same deterministic scoring engine and do not call an LLM.

## Local setup

Apply migrations explicitly to the database selected by `EVALOPS_DATABASE_URL` (or the default
backend database):

```sh
cd backend
EVALOPS_DATABASE_URL="sqlite:////absolute/path/to/evalops.db" .venv/bin/alembic upgrade head
```

Start the single local worker in another terminal:

```sh
cd backend
EVALOPS_DATABASE_URL="sqlite:////absolute/path/to/evalops.db" .venv/bin/python -m evalops.worker
```

Stop it with Ctrl-C or SIGTERM. `--once` claims at most one currently available job and exits;
`--poll-seconds` changes the idle polling interval. The worker does not run migrations or create
the database schema. The API and worker must use the same database URL.

## Execution and recovery

The worker atomically changes one queued job to running, assigns a unique lease token, and
decrements queue capacity in one short transaction. It checks the persisted dataset version,
canonical scoring-input fingerprint, and knowledge-base hash before scoring. A mismatch fails
with a sanitized `input_changed` error; it never scores a changed snapshot.

Scoring occurs outside write transactions. Progress and lease heartbeat updates use short,
separate sessions. Cancellation is checked between cases and again in the final transaction.
Results are held until finalization, which creates the run, all result rows, the job/run link, and
the succeeded state in one transaction. A process crash before that commit leaves no partial run.

Leases expire after two minutes without a heartbeat. A later worker poll requeues expired work
with persisted exponential backoff, up to two retries (three total attempts). Only recognized
transient database failures are retried. Other failures are terminal and expose only a stable
error code and generic message. Unique job/run linkage and owner-token checks prevent duplicate
finalization and fence stale workers. The queue counter includes only queued jobs; capacity is
released when a job is claimed, cancelled while queued, or requeued for a retry.

Migration `0006` adds `next_attempt_at` so retry delays survive process restarts. No migration is
automatically applied by the worker. This implementation is intended for one local worker and
SQLite development; multi-process production operation needs database-specific locking and
concurrency validation before deployment.
