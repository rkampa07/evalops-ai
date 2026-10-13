# Roadmap

Ordered by customer value per unit of effort. Items marked **(semantic)** would add model-based judgments. These would be clearly labelled as such, kept separate from the deterministic metrics, and never presented as ground truth.

## Near term

- **CLI and CI action**: `evalops run --dataset kestrel-support-v1 --responses out.json --gate policy.yaml`, exit code = gate decision. The engine is already I/O-free, so this mostly needs packaging.
- **Saved gate policies**: name and version policies per dataset instead of passing thresholds per request; record which policy version approved each release.
- **Dataset versioning**: immutable dataset snapshots so a run always references the exact cases it was scored against, plus a diff view between versions.
- **Per-tag gates**: e.g. `safety` tag pass rate must be 100% while `policy` may be ≥ 90%.
- **Streaming body limit**: enforce the request size cap for chunked uploads as well (currently `Content-Length` only).
- **Annotations**: let reviewers mark a failing case as "golden set wrong" or "accepted risk", with an audit trail.

## Medium term

- **Statistical confidence**: bootstrap confidence intervals on pass rate and F1. Flag comparisons where the dataset is too small to separate a real regression from noise.
- **Additional deterministic checks**: regex checks, numeric tolerance ("refund amount within ±1%"), citation/URL allow-lists, length limits, PII pattern detection (emails, card-number shapes).
- **Pluggable runners (optional)**: adapters that call a model endpoint to *generate* responses, behind an explicit opt-in, with credentials from the environment only. The default stays "bring your own outputs".
- **(semantic)** Embedding similarity as an *additional* signal for paraphrase-heavy answers, reported next to token F1 and never replacing it.
- **(semantic)** LLM-as-judge rubrics with judge-agreement tracking against human labels, displayed with their measured error rate.

## Production readiness

- Authentication (OIDC) and per-project roles; API tokens for CI.
- Postgres support (SQLAlchemy and Alembic are already in place), background scoring for very large runs.
- Containerized deployment, structured logging, metrics, and an audit log of gate decisions.
- Multi-tenant isolation and data-retention controls for customer outputs.

## Explicitly out of scope

- Claiming hallucination detection or factual verification from lexical metrics.
- Shipping customer data or credentials in the repository or examples.
