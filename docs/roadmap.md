# Roadmap

Ordered by customer value per unit of effort. Items marked **(semantic)** would add model-based judgments. These would be clearly labelled as such, kept separate from the deterministic metrics, and never presented as ground truth.

## Near term

- **CLI and CI action**: `evalops run --dataset kestrel-support-v1 --responses out.json --gate policy.yaml`, exit code = gate decision. The engine is already I/O-free, so this mostly needs packaging.
- **Saved gate policies**: name and version policies per dataset instead of passing thresholds per request; record which policy version approved each release.
- **Dataset version diffs** *(versioning itself is done: per-case hashes, result snapshots, comparison compatibility checks)*: a UI diff between dataset versions, and optional re-scoring of a baseline's stored outputs against the candidate's definitions so edited cases can still be compared.
- **Per-tag gates**: e.g. `safety` tag pass rate must be 100% while `policy` may be ≥ 90%.
- **Streaming body limit**: enforce the request size cap for chunked uploads as well (currently `Content-Length` only).
- **Annotations**: let reviewers mark a failing case as "golden set wrong" or "accepted risk", with an audit trail.

## Medium term

- **Statistical confidence**: bootstrap confidence intervals on pass rate and F1. Flag comparisons where the dataset is too small to separate a real regression from noise.
- **Additional deterministic checks**: regex checks, numeric tolerance ("refund amount within ±1%"), citation/URL allow-lists, length limits, PII pattern detection (emails, card-number shapes).
- **Pluggable runners (optional)**: adapters that call a model endpoint to *generate* responses, behind an explicit opt-in, with credentials from the environment only. The default stays "bring your own outputs".
- **Label review and growth:** independent review of the 96 draft claim labels (`review_labels.py`), then grow toward 300+ claims across more models before treating judge numbers as more than provisional.
- **(semantic)** Embedding retrieval and embedding similarity, alongside BM25 and the lexical groundedness heuristic, compared on the same labelled set.
- **(semantic)** Stronger local judges (e.g. 7B–8B on a 16 GB machine) and judge ensembles. Each is admitted to gating only if its measured agreement on reviewed labels clears a documented bar.
- Answer-relevance checks: "is the grounded sentence actually the answer?", the gap shown by the order-cancellation example.

## Production readiness

- Headless API tokens for CI (browser OIDC login, opaque sessions, CSRF protection, and per-workspace `owner`/`admin`/`editor`/`viewer` RBAC are implemented).
- PostgreSQL multi-host deployment validation (SQLAlchemy, Alembic, workspace quotas, and lease-fenced background workers for runs and frozen assessments are implemented against SQLite).
- Containerized deployment, structured logging, metrics, and an immutable audit log of gate approvals.
- Data-retention controls and external secret-manager integration for multi-tenant customer deployments.

## Explicitly out of scope

- Claiming hallucination detection or factual verification from lexical metrics.
- Shipping customer data or credentials in the repository or examples.
