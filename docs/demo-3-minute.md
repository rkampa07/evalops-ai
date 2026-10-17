# Three-Minute Engineering Portfolio Demo — EvalOps AI

This guide structures a concise, three-minute technical walkthrough of **EvalOps AI** for Forward Deployed Engineer, AI Infrastructure, and Platform Engineering interviews. Every metric, evaluator version, and failure mode referenced below comes directly from recorded local runs and deterministic test fixtures.

---

## Pre-Demo Setup (Before the Clock Starts)

Requires **Python 3.13** and **Node 22.12+** (`make setup` once for locked dependencies).

To demonstrate **both** the recorded Kestrel RAG regression workflow and the **live opt-in citation/claim assessments** in an isolated SQLite database (`backend/data/assessment_demo.db`) without touching `backend/data/evalops.db` or loading AI model weights:

```bash
# Terminal 1 — Isolated FastAPI backend (http://127.0.0.1:8000)
make assessment-demo-backend

# Terminal 2 — Seed recorded Kestrel RAG runs (rag-v1, rag-v2, rag-v3 under methodology 2.0.0)
make assessment-demo-seed

# Terminal 3 — Lease-fenced background workers (real citation-evaluator-v4 + SimulatedDemoClaimVerifier)
make assessment-demo-worker

# Terminal 4 — React 19 + Vite UI (http://127.0.0.1:5173)
make frontend
```

Open `http://127.0.0.1:5173` in your browser. Because `make assessment-demo-backend` sets `EVALOPS_ENV=development` and `EVALOPS_AUTH_MODE=development` on `127.0.0.1`, loopback browser requests are automatically authenticated in **Default Workspace** (`owner` role) via `/auth/session` with no login prompt required.

*(If you only need the read-only regression replay without background assessment workers, `make backend`, `make frontend`, and `make rag-demo` against the default database remain supported.)*

---

## Three-Minute Demo Sequence

### 1. Problem and Motivation (0:00–0:30)

**Browser action:**
- Open **Datasets → `kestrel-policy-rag-v1`**.
- Briefly show the **20 policy passages** (`KB-*`) and **20 evaluation questions** (17 answerable with gold passages and required citations; 3 unanswerable questions marked **should decline** / `expect_abstain: true`).

**Presenter script:**
> "When teams iterate on a RAG prompt or retrieval pipeline, reading a handful of answers hides silent regressions: a prompt change that improves tone or fixes one policy answer frequently invents facts on out-of-scope questions, breaks citations, or regresses latency. EvalOps AI separates evaluation into two layers: fast, deterministic release gates that run on every candidate run, and opt-in asynchronous secondary assessments for deeper citation and claim diagnostics."

---

### 2. System Architecture (0:30–1:00)

**Browser action:**
- Open **Runs** and highlight the recorded `rag-v1`, `rag-v2`, and `rag-v3` runs scored under **methodology `2.0.0`**.

**Presenter script:**
> "The architecture keeps the FastAPI server and primary scoring path completely model-free. Primary runs are scored synchronously by a pure deterministic Python engine—methodology `2.0.0`—which computes retrieval recall@3 and MRR, citation validity, lexical groundedness, abstention accuracy, and release gates in SQLite with workspace-scoped RBAC and CSRF protection. Heavy or secondary evaluations—like our frozen `citation-evaluator-v4` heuristic and `semantic-aggregate-v12` claim evaluator—are queued as asynchronous jobs with SHA-256 provenance verification and executed out-of-process by lease-fenced background workers so they can never block the API or mutate primary release-gate metrics."

---

### 3. Evaluation Results and Regression Detection (1:00–1:45)

**Browser action:**
1. Open **Compare**, select baseline **`rag-v1`** and candidate **`rag-v2`**.
2. Point out the regression summary (`3/20` passed vs. `9/20`, **7 regressed cases**, groundedness drop, and latency spike).
3. Switch candidate to **`rag-v3`**, then open **Release reports** for **`rag-v1` → `rag-v2`** and **`rag-v1` → `rag-v3`**.

**Verified recorded metrics (`mlx-community/Qwen2.5-1.5B-Instruct-4bit`, BM25 `k=3`, methodology `2.0.0`):**

| Metric / Gate | Required Gate | `rag-v1` (Baseline) | `rag-v2` ("Friendlier" Prompt) | `rag-v3` (Targeted Fixes) |
| --- | --- | --- | --- | --- |
| **Pass rate** | `>= 0.90` (90%) | `9/20` (`45.0%`) | `3/20` (`15.0%`) — **6 gates fail** | `9/20` (`45.0%`) — **3 gates fail** |
| **Regressed cases vs. `rag-v1`** | `<= 0` | — | **7 regressed** (`has_regression = true`) | **3 regressed**, 3 fixed |
| **Lexical groundedness** | `>= 0.80` (80%) | `0.7778` (`77.8%`, 4 unsupported claims) | `0.5217` (`52.2%`, **22 unsupported claims**) | `0.8000` (`80.0%`, 6 unsupported claims) |
| **Citation validity** | `>= 1.00` (100%) | `0.9000` (`90.0%`) | `0.9167` (`91.7%`) | `1.0000` (`100.0%`, 4 answers omit citations) |
| **Abstention accuracy** | `>= 1.00` (100%) | `0.9000` (`90.0%`) | `0.8500` (`85.0%`) | `0.8500` (`85.0%`, `0/3` unanswerable declined) |
| **p95 latency** | `<= +20%` tolerance | `964.8 ms` | `1,709.1 ms` (**+77.2%**) | `1,219.1 ms` (**+26.4%**) |
| **Release Verdict** | All active gates | `NOT READY` | **`NOT READY FOR RELEASE`** | **`NOT READY FOR RELEASE`** |

**Presenter script:**
> "Here is why deterministic regression checks matter. In `rag-v2`, a 'friendlier, more helpful' prompt causes pass rate to collapse from `9/20` (`45%`) to `3/20` (`15%`) with **7 regressed cases**. Lexical groundedness drops from `77.8%` to `52.2%`— jumping from 4 unsupported claims to 22—abstention accuracy drops from `0.90` to `0.85` because the model invents Saturday 10 am Seattle showroom hours and claims UK shipping to London when policy is Canada-only, and p95 latency climbs from `965 ms` to `1,709 ms`.
>
> In `rag-v3`, targeted prompt rules bring lexical groundedness up to `80.0%` and citation validity to `100%`, returning to `9/20` passed. An aggregate average looks flat, but **Compare** and **Release reports** still block `rag-v3`: three cases improve (`q-late-credit`, `q-ship-uk`, `q-white-glove`), while three regress (`q-showroom-hours`, `q-gift-wrap`, `q-financing`) because `rag-v3` answers all three unanswerable questions instead of abstaining."

---

### 4. Citation and Claim Assessment Demonstration (1:45–2:30)

**Browser action:**
1. Open **Runs**, select the **`rag-v1`** (or **`rag-v3`**) run, and expand **`q-return-window`** (*"How many days do I have to return a sofa I bought?"*).
2. Click **Run citation assessment** (`citation-evaluator-v4`) and wait ~1–2 seconds for the worker to transition `queued → running → completed`.
3. Point out the citation assessment status **`wrong_passage`** (`reason_code: "citation_set_wrong_passage"`, per-citation `KB-RET-01: wrong_passage` / `specific_retrieved_passage_matches`).
4. Click **Run claim assessment** (`semantic-aggregate-v12`) and wait ~1–2 seconds for completion.
5. Point out the **`Simulated verifier — no NLI model loaded`** disclosure badge and the **Configured model** vs. **Executed verifier** fields.

**Presenter script:**
> "Next, let's drill into a single result—`q-return-window`—to see how opt-in assessments complement lexical scoring without altering release-gate metrics:
>
> First, in the primary run, the deterministic lexical scorer marks the claim **`unsupported`** in `rag-v1` and `rag-v3` because conversational words like *'sofa you bought'* dilute content-word overlap against `KB-RET-01` below the 60% lexical threshold. When we trigger the frozen **`citation-evaluator-v4`** assessment, it answers a different question—citation-passage appropriateness across the retrieved set (`KB-RET-01`, `KB-RET-03`, `KB-RET-04`)—and flags **`wrong_passage`** (`specific_retrieved_passage_matches`), because another retrieved passage in the candidate set matches a more specific phrase.
>
> Second, when we trigger the frozen **`semantic-aggregate-v12`** claim assessment, notice the prominent **`Simulated verifier — no NLI model loaded`** banner. The card explicitly separates **Configured model** (`cross-encoder/nli-deberta-v3-small`) from **Executed verifier** (`SimulatedDemoClaimVerifier (deterministic token-overlap stub — no DeBERTa inference)`). That lets us exercise the full asynchronous job lifecycle, lease recovery, and frozen aggregation pipeline in a portable demo without claiming real neural NLI inference."

---

### 5. Engineering Tradeoffs and Limitations (2:30–3:00)

**Browser action:**
- Stay on the expanded **`rag-v3`** run (optionally show `q-opened-mattress`, where the output is only citation markers `[KB-RET-01] [KB-RET-03]...` and fails `content` under methodology `2.0.0`).

**Presenter script:**
> "Finally, I want to be explicit about the engineering tradeoffs and limitations of this system:
> 1. **Lexical groundedness is a guardrail, not semantic entailment:** A green lexical score does not prove factual correctness. In our documented custom-made sofa regression test (`test_rag_known_limitations.py`), an answer claiming a custom sofa can be returned within 30 days passes 60% lexical overlap against the general return policy even though `KB-RET-03` states custom furniture *cannot* be returned.
> 2. **Simulated demo verifier vs. real DeBERTa inference:** The local assessment demo uses `SimulatedDemoClaimVerifier` so it runs offline with zero model downloads; it does not run `torch`/`transformers` or `cross-encoder/nli-deberta-v3-small`.
> 3. **Uncalibrated judge estimates:** Our offline 3B MLX judge panel (`Llama-3.2-3B-Instruct-4bit`) is stored as an auxiliary estimate only—never a release gate—because its calibration against provisional labels is precision `0.42` and Cohen's `κ = 0.34`, with only `5 of 96` labels human-reviewed.
> 4. **UI & deployment scope:** Historical assessment listing by `(run_id, result_id)` is not yet wired into row re-expansion, and the stack is scoped to local `127.0.0.1` evaluation rather than multi-tenant production deployment."

---

## Fallback Plan if the Live Demo Fails

If any live service has an issue during the interview, use these immediate fallbacks:

| Failure Mode | Immediate Recovery / Non-Interactive Fallback |
| --- | --- |
| **Assessment job stays in `queued`** | Terminal 3 (`make assessment-demo-worker`) is not running. Start it; the worker polls every `1.0s` and will immediately pick up the queued job. |
| **Claim assessment button is disabled** | The selected run was not seeded with `retrieved_passage_ids`. Run `make assessment-demo-seed` and select the newly printed `rag-v1` / `rag-v3` run IDs. |
| **Port `8000` or `5173` conflict / browser issue** | Run the self-contained vitest integration harness (`cd frontend && npx vitest run src/pages/RunDetail.test.tsx`), which exercises the real React UI, FastAPI routes, SQLite database, `CitationAssessmentWorker`, and `ClaimAssessmentWorker` in ~2 seconds. |
| **Show the custom-sofa lexical false positive without UI** | Run `PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/pytest -p no:cacheprovider tests/regression/test_rag_known_limitations.py::test_known_limitation_custom_sofa_contradiction_passes_lexical_scoring` |
| **Static artifacts & recorded outputs** | Walk through the recorded JSON artifacts directly in `examples/rag/responses/qwen2.5-1.5b_rag-v1.json`, `qwen2.5-1.5b_rag-v2.json`, and `qwen2.5-1.5b_rag-v3.json`, plus [docs/rag-evaluation.md](rag-evaluation.md) and [docs/assessment-demo.md](assessment-demo.md). |

### Post-Demo Cleanup

Stop terminals with `Ctrl+C`. To remove the isolated demo database while leaving `backend/data/evalops.db` untouched:

```bash
make clean-assessment-demo-db
```
