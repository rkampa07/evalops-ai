# Isolated Local Assessment Demo Guide

This guide walks through a repeatable local demonstration of EvalOps AI's opt-in frozen citation
assessments (`citation-evaluator-v4`) and semantic claim assessments (`semantic-aggregate-v12`),
alongside deterministic lexical scoring, regression comparison, and release gating.

The demo launcher uses a dedicated SQLite database (`backend/data/assessment_demo.db`) and never
reads, modifies, or deletes `backend/data/evalops.db`.

---

## 1. Evaluation Layers and Scope

EvalOps AI separates release-gating metrics from opt-in per-result diagnostic assessments:

| Layer | Version / Identifier | Execution Model | Role in Comparisons & Release Gates |
|---|---|---|---|
| **Deterministic lexical evaluation** | `methodology 2.0.0` | Synchronous on run creation (`POST /evaluations/runs`) or via `EvaluationJobWorker` | **Primary.** Drives run summaries, baseline-vs-candidate comparisons (`GET /evaluations/compare`), and release gates (`POST /evaluations/gates`). |
| **Model-judge grades (optional)** | `judge-v2` (`Llama-3.2-3B-Instruct-4bit`) | Offline script (`rag_judge.py`), replayed via `PUT /evaluations/runs/{id}/model-grades` | **Estimated only.** Displayed in a separate panel (`estimated_groundedness`); excluded from default release gates. |
| **Frozen citation assessment** | `citation-evaluator-v4` | Opt-in per saved result (`POST /evaluations/runs/{run_id}/results/{result_id}/assessments` with `{"evaluator": "citation", "citation_expected": ...}`), processed by `CitationAssessmentWorker` | **Diagnostic only.** Stored in `citation_assessment_results`; never modifies lexical run metrics, summaries, comparisons, or release gates. |
| **Frozen semantic claim assessment** | `semantic-aggregate-v12` | Opt-in per saved result (`{"evaluator": "claim"}`), processed by `ClaimAssessmentWorker` | **Diagnostic only.** Stored in `claim_assessment_results`; never modifies lexical run metrics, summaries, comparisons, or release gates. |

### Simulated Demo Verifier Disclosure

- **Citation assessments (`citation-evaluator-v4`)** run the real frozen deterministic citation
  evaluator (`backend/evalops/citation_evaluator_v4.py`, SHA-256 verified before every prediction).
- **Claim assessments (`semantic-aggregate-v12`)** in production worker mode (`make_local_verifier`)
  require optional `torch` and `transformers` dependencies plus a locally cached Hugging Face
  snapshot of `cross-encoder/nli-deberta-v3-small` at commit
  `fa2804872c3b4bd748f38c0185cc85775361e735`.
- **In this local demo (`make assessment-demo-worker`)**, `ClaimAssessmentWorker` is configured with
  `SimulatedDemoClaimVerifier` (`backend/scripts/assessment_demo_worker.py`). This stub computes
  deterministic token overlap over retrieved evidence units so the full API, queue, lease-fenced
  worker, provenance check, and UI lifecycle can be exercised **without downloading or loading any
  DeBERTa NLI model**.
- Results produced by `SimulatedDemoClaimVerifier` are explicitly marked in both the API payload
  (`guards: ["simulated_demo_verifier"]`, `model.backend: "simulated_demo_stub"`) and the UI with a
  prominent neutral badge: **`Simulated verifier — no NLI model loaded`**, plus separate
  **Configured model** and **Executed verifier** fields.

---

## Real DeBERTa mode (Phase 5H)

The existing API, SQLite schema (`0014`), and lease-fenced `ClaimAssessmentWorker` also support real
NLI. The launcher uses `make_local_verifier()` via a CPU runtime factory; it does not implement a
second evaluator or change frozen `semantic-aggregate-v12`, its configuration digest
(`92aacfdf47908332`), citation v4, lexical scores, comparisons, or release gates.

### Optional packages and exact model snapshot

On macOS arm64 with Python 3.13 and an existing `backend/.venv`:

```bash
make setup-nli
make assessment-nli-download
make assessment-nli-check
make assessment-nli-smoke
```

`requirements/nli.lock` includes only the transitive CPU NLI dependencies (PyTorch, Transformers,
Hugging Face Hub, SentencePiece and their dependencies), reusing the existing reviewed lock pins
and hashes. It does not install MLX, sentence-transformers, CUDA or ONNX. This lock's PyTorch wheel
hash is specific to macOS arm64 / CPython 3.13; do not use it as a Linux CI dependency lock.
No installation or new download is necessary when these packages and the pinned cache already exist.

Model downloads occur only through `assessment-nli-download`; package installation may also use
the network. Inference never fetches weights. The download helper uses official `snapshot_download` with
`revision=fa2804872c3b4bd748f38c0185cc85775361e735`, one download thread, and an explicit allowlist:
`config.json`, `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`,
`special_tokens_map.json`, `added_tokens.json`, `spm.model`. It downloads no alternate checkpoint,
pickle weights, ONNX files or unpinned revision, and leaves other caches intact. The safetensors
checkpoint is about 568 MB (decimal); tokenizer files add about 11 MB. Its content SHA-256 was
verified against the pinned Hub metadata as
`ebc79588dd73ccfb6a3f6078519cfbf512c5305384c5ea1845bc71cd32216e86`. The cache
uses the distinct Xet blob identity
`3a9c57043e195d65bc3a07e5280c6aec6a448892a603fd02ecdbfd9ce3cdb3e4`; this is not
the complete file's SHA-256.
See the [pinned model files](https://huggingface.co/cross-encoder/nli-deberta-v3-small/tree/fa2804872c3b4bd748f38c0185cc85775361e735)
and [official download API](https://huggingface.co/docs/huggingface_hub/guides/download).

Regular inference uses the frozen local-cache verifier and `local_files_only=True`. Required files
must resolve under the exact commit's snapshot; output records include the resolved commit, file
sizes, blob identities and small-file SHA-256 values. Missing dependencies, missing files or a load
failure produce an error, never a simulated result. Preflight does not load weights or run inference.

### Start real inference

Use the same isolated demo backend and seeding commands shown below. Stop any simulated worker
before switching modes, then start:

```bash
make assessment-demo-worker-real
```

This defaults to `backend/data/assessment_demo.db`, with the existing guard rejecting the live
`backend/data/evalops.db`. For a separate temporary database, pass an explicit URL:

```bash
EVALOPS_DATABASE_URL=sqlite:////tmp/my-isolated-assessment.db \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  backend/.venv/bin/python backend/scripts/assessment_demo_worker.py --verifier real
```

The API must use that same isolated database. The direct script defaults to `real`; the original
`make assessment-demo-worker` command keeps explicitly simulated behavior. Both modes can also be
selected with `--verifier real|simulated`. `--once` processes at most one citation and one claim job;
a failed claim returns a nonzero exit status. The looping demo worker stops on a failed claim.
Real mode performs dependency/cache preflight before database creation or migration. Actual model
loading remains lazy, after a durable job is acquired. Authentication, CSRF, workspace ownership,
idempotency, cancellation, retries and result immutability continue through the existing API/worker.

Run one real worker on an 8 GB machine. The runtime fixes tensors to CPU and uses two intra-op
threads; it retains the production evaluator's batch size (16), token limit (512), and thresholds.
MPS is not enabled. Other applications and long evidence inputs affect latency and available RAM.
Cancellation waits for an in-flight NLI call to finish; it cannot interrupt a tensor operation.

### Identify real execution and preserve history

Submit a **new** claim assessment after starting the real worker. Old simulated results keep their
original provenance and are never relabelled. The claim card shows Configured model, full Model
revision, Executed model and Actual inference backend. It shows **Real NLI inference** only when
all claims record Transformers, the matching model/revision, the resolved pinned snapshot and
finite three-way probabilities for scored evidence. Configuration alone, empty evidence, or
missing/mismatched provenance does not earn that badge. Simulated results retain their explicit
**Simulated verifier — no NLI model loaded** disclosure and show no executed NLI model.

The three-way model output (entailment, contradiction, neutral) feeds the unchanged v12 aggregation
and guards, which may return supported, contradicted, insufficient evidence or needs-human-review.
These probabilities are uncalibrated, and explanations/guards do not prove semantic correctness.
Small smoke examples do not establish accuracy on policy questions. Review model errors and
abstentions; frozen assessments remain diagnostic and do not change release decisions.

### Measured local verification (2026-10-17)

An M2 MacBook with 8 GB memory loaded the already-cached exact revision using CPU, PyTorch 2.14.1,
Transformers 5.19.0 and two threads. One process loaded weights in 2.88 s; its first scored pair
took 1.04 s, and the next two took 22–24 ms. Peak process RSS was 669 MiB (not total system memory
or a guarantee for larger batches). These are one-run smoke measurements, not a benchmark.

Evidence was "A woman is reading a book." in each case:

| Claim | Expected / predicted NLI label | Probability of predicted label |
|---|---|---|
| A person is reading. | entailment / entailment | 0.994845 |
| No one is reading. | contradiction / contradiction | 0.999765 |
| The woman has two children. | neutral / neutral | 0.998641 |

Model label IDs were 0=contradiction, 1=entailment, 2=neutral. A separate Chromium check submitted
a legitimate claim through React and the API to a separate real worker and temporary SQLite DB.
It persisted a supported verdict with Transformers/snapshot/scored-pair provenance, returned it
through the API, and rendered the real-execution fields without simulated markers. Idempotent
replay reused the job, and lexical run metrics were unchanged. No live database was accessed.

## 2. Startup Instructions (3 Terminals)

Prerequisites: Python 3.13 (`backend/.venv` initialized via `make setup`) and Node 22 (`frontend/node_modules` installed). Ports `127.0.0.1:8000` and `127.0.0.1:5173` must be free.

### Terminal 1 — Start the Isolated Demo API (`127.0.0.1:8000`)

```bash
make assessment-demo-backend
```

This starts `uvicorn evalops.main:app` with:
- `EVALOPS_DATABASE_URL=sqlite:///<repo>/backend/data/assessment_demo.db` (auto-migrates to Alembic head `0014`),
- `EVALOPS_ENV=development` and `EVALOPS_AUTH_MODE=development` (loopback workspace `1`, owner role),
- `EVALOPS_API_JOB_SUBMISSIONS_RPM=60` (so interactive assessment submissions across multiple cases do not hit the default 5/minute job submission rate limit).

### Terminal 2 — Seed Demo Data & Start Assessment Workers

With Terminal 1 running, seed the Kestrel RAG dataset (`kestrel-policy-rag-v1`) and recorded runs
(`rag-v1`, `rag-v2`, `rag-v3`), then start the assessment worker loop:

```bash
make assessment-demo-seed
make assessment-demo-worker
```

- `make assessment-demo-seed` replays the committed Kestrel policy passages, 20 test cases, recorded
  model outputs, and saved judge verdicts over HTTP into `assessment_demo.db`.
- `make assessment-demo-worker` starts `backend/scripts/assessment_demo_worker.py`, polling
  `assessment_demo.db` for queued citation and claim assessment jobs.

### Terminal 3 — Start the Frontend UI (`127.0.0.1:5173`)

```bash
make frontend
```

Open `http://127.0.0.1:5173` in your browser.

---

## 3. Demonstration Walkthrough

### Step A — Inspect Deterministic Run Metrics (`Run detail`)
1. Open **Runs** in the left navigation and select the baseline `rag-v1` run (or candidate `rag-v3`).
2. Review the top-level summary cards (**Pass rate**, **Mean token F1**, **Forbidden-term violations**,
   **Latency p50 / p95**) and the **RAG evidence** card (**Retrieval recall@k**, **MRR**, **Citation validity**,
   **Groundedness (lexical)**, **Abstention accuracy**).
3. Note that all run-level metrics are computed deterministically under scoring methodology **2.0.0**.

### Step B — Run a Frozen Citation Assessment (`citation-evaluator-v4`)
1. In the **Results** table on the Run Detail page, click a case key (for example, `return-window` or
   another answered RAG case with retrieved passages) to expand its detail row.
2. Locate the **Assessments** section (`Separate, opt-in frozen assessments (citation-evaluator-v4 and semantic-aggregate-v12)`).
3. Verify the **Citation expected** checkbox state and click **Run citation assessment**.
4. Observe the status badge transition (`queued` → `running` → `succeeded`) while duplicate
   submissions are disabled.
5. Inspect the rendered **Citation assessment** card:
   - Evaluator disclaimer (`citation-evaluator-v4`),
   - Overall status badge (`correct`, `partial`, `missing_citation`, `invalid_citation`, `not_retrieved`, or `not_applicable`),
   - Outcome reason and reason code,
   - Provenance hashes (`config`, `input`, `kb`),
   - Per-citation decision table with component coverage scores.

### Step C — Run a Semantic Claim Assessment (`semantic-aggregate-v12` with Simulated Verifier)

For the real worker variant above, the same controls run real CPU NLI. Look for **Real NLI inference**,
**Executed model** and **Actual inference backend: transformers** instead of simulated disclosure.
The following steps describe the original lightweight simulated workflow.
1. On the same expanded result row (or another case that has recorded `retrieved_passage_ids`), click
   **Run claim assessment**.
2. Observe the job lifecycle (`queued` → `running` → `succeeded`) and progress counter
   (`completed of total`).
3. Inspect the rendered **Claim assessment** card:
   - Header badges: `frozen evaluator` and **`Simulated verifier — no NLI model loaded`**,
   - **Configured model**: `cross-encoder/nli-deberta-v3-small (fa2804872c3b)`,
   - **Executed verifier**: `demo-stub/simulated-lexical-verifier · simulated_demo_stub (simulated — no NLI weights loaded)`,
   - **Claim verdicts** table showing each split claim, its diagnostic branch and guards
     (`simulated_demo_verifier`), verdict badge, explicit `[SIMULATED DEMO VERIFIER ...]` reason, and
     deciding evidence units.
4. If a result has `retrieved_passage_ids: null`, the **Run claim assessment** button is disabled
   with an inline explanation that recorded retrieval metadata is required.

### Step D — Verify Regression Comparison & Release Gates Remain Unchanged
1. Click **Compare** (or navigate to `/compare?baseline=1&candidate=3`) to compare `rag-v1` against
   `rag-v3`.
   - Confirm per-case regression classification (`regressed`, `fixed`, `unchanged`) and metric deltas.
   - Runs with mismatched dataset versions, knowledge-base hashes, scoring configs, or major
     methodology versions are reported as **not comparable** rather than false regressions.
2. Click **Release report** (`/reports?run=3&baseline=1`) to evaluate the predefined release gates.
   - Confirm that `rag-v3` remains **NOT READY** (`min_pass_rate`, `min_abstention_accuracy`, and
     `max_regressed_cases` fail) and that running citation or claim assessments has not altered any
     lexical metric, run summary, or gate outcome.

---

## 4. Known Limitations

1. **Real DeBERTa NLI inference is not executed in the demo launcher:** `make assessment-demo-worker`
   uses `SimulatedDemoClaimVerifier` so the demo runs without installing `torch`/`transformers` or
   downloading `cross-encoder/nli-deberta-v3-small`. It does not measure or demonstrate real NLI
   model accuracy.
2. **Historical assessment retrieval is not implemented in the UI:** The backend exposes
   `GET /evaluations/assessments/{assessment_id}` and `GET /evaluations/assessments/{assessment_id}/result`,
   but does not expose a list endpoint for prior assessments on a `(run_id, result_id)` pair.
   Collapsing a result row or refreshing the browser page clears the in-memory React state for that
   row (while the completed assessment record remains durably stored in SQLite).
3. **Single-active-lease worker concurrency:** `CitationAssessmentWorker` and `ClaimAssessmentWorker`
   coordinate through a shared queue (`CitationAssessmentQueue`) and process one active assessment
   lease at a time.
4. **Local-only scope:** The demo backend binds to `127.0.0.1` using loopback development
   authentication (`EVALOPS_AUTH_MODE=development`) and is designed for local single-user inspection.

---

## 5. Safe Shutdown and Cleanup

1. **Stop the running processes:** Press `Ctrl+C` in Terminal 3 (frontend), Terminal 2 (demo worker),
   and Terminal 1 (demo API).
2. **Remove the isolated demo database:**
   ```bash
   make clean-assessment-demo-db
   ```
   This runs `backend/scripts/assessment_demo_worker.py --clean-db`, which verifies that no active
   OS processes are using `assessment_demo.db` and removes only `backend/data/assessment_demo.db`
   and its `-wal`, `-shm`, and `-journal` sidecar files. `backend/data/evalops.db` is never touched.
