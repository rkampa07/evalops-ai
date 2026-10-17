# Frozen semantic claim assessments

Claim assessment is an opt-in, durable operation over one saved evaluation result. It is separate
from lexical run metrics, citation assessment, model grades, comparisons, and release gates. The
claim evaluator is the unchanged `semantic-aggregate-v12` implementation.

## Local setup

Apply migrations explicitly, then start the claim worker from `backend`:

```sh
.venv/bin/alembic upgrade head
.venv/bin/python -m evalops.claim_worker
```

The worker requires schema revision `0014`. It loads the pinned local NLI model only after it has
claimed a claim-assessment job. The API process does not construct the NLI backend or load model
weights. The configured model is `cross-encoder/nli-deberta-v3-small` at revision
`fa2804872c3b4bd748f38c0185cc85775361e735`. The existing `TransformersNLI` loader verifies its
local snapshot and uses `local_files_only=True`; it never downloads or substitutes weights. Missing
packages, tokenizer files, or model weights fail the job with sanitized `model_unavailable`; there
is no lexical fallback. On an 8 GB machine, run one claim worker so only one NLI model instance is
resident.

Start the citation worker separately if citation jobs are also needed:

```sh
.venv/bin/python -m evalops.citation_worker
```

Both workers share the durable queue cap, per-workspace queued cap, numeric assessment ID allocator,
workspace-scoped idempotency ledger, and global active lease. This keeps citation and claim job IDs
usable through the existing `/evaluations/assessments/{assessment_id}` endpoints.

## API

An editor submits a claim assessment without a citation flag:

```http
POST /evaluations/runs/12/results/34/assessments
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

```json
{"evaluator":"claim"}
```

The response returns `202`, an assessment ID, status URL, result URL, and progress. The existing
status, result, and cancel endpoints work for either evaluator kind. Editors can submit or request
cancellation; workspace viewers can read status and completed output. An idempotency key is unique
within workspace and submitter across evaluator kinds. Identical replays return the same assessment;
different requests using the key return `409`.

## Inputs, limits, and stored output

Only the saved response, saved question, recorded retrieval IDs, and verified text for those
retrieved passages reach `split_claims` and `SemanticVerifier.verify(question, claim, evidence)`.
Reference answers, gold passage IDs, reviewer notes, lexical results, model grades, and citation
predictions are never passed to the claim evaluator. Retrieval metadata must be present, the full
current KB fingerprint must match the saved run fingerprint, and every recorded passage ID must
exist in that KB. A recorded empty retrieval remains empty; missing metadata fails closed.

The adapter bounds responses and questions at 20,000 characters, retrieved evidence at 20,000
characters and 50 passages, and extracted claims at 10 claims and 4,000 characters per claim. A
response with no checkable claims completes with `no_checkable_claims` and does not load NLI.

Claim outputs are immutable and stored separately from citation outputs. Each claim retains its
verdict (`supported`, `contradicted`, `insufficient_evidence`, or `needs_human_review`), reason,
diagnostic branch, abstention flag, deciding evidence references, and evaluator provenance.
`needs_human_review` is preserved as an abstention, not mapped to another class. Source SHA-256,
the v12 `AggregationConfig` digest, model ID/revision, input fingerprint, and KB fingerprint are
stored with the result. Assessment history prevents deletion of its saved run.

## Operational limits

The existing global cap of 50 and per-workspace queued cap of 10 are shared by citation and claim
assessments. Queue and lease updates use the existing durable queue row and fenced worker pattern.
Cancellation is checked before scoring, between claims, and before atomic finalization; an in-flight
NLI call finishes before cancellation takes effect. Transient infrastructure errors retry at most
twice. SQLite supports local development but is intended for single-process worker operation.
