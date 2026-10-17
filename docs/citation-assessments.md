# Frozen citation assessments

Citation assessments are an opt-in, separate workflow over one saved evaluation result. They do
not alter lexical result rows, run summaries, comparisons, model grades, or release gates. This
phase supports only `citation-evaluator-v4`; it does not load or invoke the semantic claim/NLI
evaluator.

## Local operation

Apply schema changes explicitly (or use the configured local auto-migration):

```sh
cd backend
.venv/bin/alembic upgrade head
.venv/bin/python -m evalops.citation_worker
```

The citation worker is independent of the evaluation worker. It does not migrate the database,
load an ML model, or download dependencies. Stop it with Ctrl-C / SIGTERM. The frozen source is
kept at `experiments/semantic-eval/citation_evaluator.py` and vendored unchanged as
`backend/evalops/citation_evaluator_v4.py`; focused tests verify the SHA-256
`33ee2d9f39258b5c7f8f6c53fa82b95730a880e500de819cb7aa262d0c51d84b`.

## API

An editor submits one citation assessment for a saved result:

```http
POST /evaluations/runs/12/results/34/assessments
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

```json
{"evaluator":"citation","citation_expected":true}
```

The request returns `202` with an assessment ID and status/result URLs. Repeating the same key
and canonical request in the same workspace and submitter scope returns that assessment; a
different request with the same key returns `409`. A new key creates a separate append-only
assessment. A queued assessment can be cancelled with
`POST /evaluations/assessments/{id}/cancel`. Workspace viewers may read status and results;
editors may submit and cancel.

## Evidence and provenance

Inputs are derived from the saved response, its saved question snapshot when available, its
recorded retrieval IDs, the KB passage text and ID registry, citation IDs parsed from the
response, and the explicit `citation_expected` flag. Reference answers, reviewer labels/notes,
lexical metrics, failures, and model grades are excluded. When citation IDs need classification,
the saved run KB hash is checked against the current dataset KB before passage text is
reconstructed, both at submission and immediately before finalization. When there are no citation
IDs, the frozen predictor's `not_applicable` or `missing_citation` outcome is independent of
retrieval evidence, so no KB reconstruction is needed.

For responses with citations, null retrieval metadata, stale KB, and retrieval IDs absent from
the verified KB are explicit errors. The question snapshot is optional context and is never
substituted with reference data. An empty retrieval list is preserved as an empty set and can
result in `not_retrieved`; it is never confused with missing metadata. The predictor keeps
`not_applicable`, `missing_citation`, `invalid_citation`, and `unresolved` separate from its five
scored classes. Aggregate and per-citation decisions are stored with reason codes.

Assessment outputs are immutable and prevent deletion of the referenced run/result. Citation and
claim jobs share fenced queue accounting, a global active-assessment lease, a 50-job global queue
cap, a 10-job per-workspace cap, and up to two retries for transient infrastructure failures. The
per-workspace cap can be configured with
`EVALOPS_MAX_QUEUED_CITATION_ASSESSMENTS_PER_WORKSPACE`.

## Limitations

The predictor is deterministic and lexical; it is not semantic entailment. Its abstentions and
special statuses are retained rather than mapped to scored classes. Workers are local processes;
the shared lease permits one active assessment at a time. SQLite is suitable for local use but does
not provide robust multi-host worker coordination. Assessment outputs contain citation explanations and should be treated as
workspace-private data. Claim assessments use a separate evaluator kind and output schema; see
`claim-assessments.md`.
