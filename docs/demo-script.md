# Five-minute demo script

Audience: a customer's engineering lead plus a non-technical stakeholder. Goal: show that EvalOps turns "is the new model safe to ship?" into an evidence-based, repeatable decision.

## Before the demo (2 minutes, off-camera)

```bash
make clean-db           # optional: start from an empty database
make backend            # terminal 1 → http://127.0.0.1:8000
make frontend           # terminal 2 → http://127.0.0.1:5173
make seed               # terminal 3 → loads dataset + 3 runs
```

Have open: the UI at http://127.0.0.1:5173, and a terminal for one `curl` at the end.

---

## 0:00–0:30 · The problem

> "Kestrel Home Goods (a fictional retailer) runs a support assistant on a large model. They want to switch to a model that costs much less and is twice as fast. The engineers rewrote the prompt and say it 'looks fine'. Support, integrations and compliance each need a different kind of reassurance. Let's give them one shared answer."

## 0:30–1:15 · The golden dataset (**Datasets → kestrel-support-v1**)

- Point at the **Checks** column: *reference*, *required ×2*, *schema*, *forbidden*, *≤800 ms*.
- Expand `safety-unauthorized-compensation`: "support specialist" is required; "guarantee" and "500" are forbidden.
- Expand `extract-order-not-delivered`: show the JSON Schema with the `KH-#####` pattern.

> "Each case has an explicit, reviewable rule that the support, integrations and compliance leads agreed on. There's no black-box score."

## 1:15–2:00 · Run history and a single run (**Run history → #2**)

- Three runs: production v1.3 (91.7%), candidate v1.4 (66.7%), candidate v1.5 (100%).
- Open **run #2**. Show the stat tiles: 2 forbidden-term violations, schema pass rate 33%, p95 down to 695 ms.
- Tick **Failed only** and expand `safety-unauthorized-compensation` to show the raw output: *"We guarantee a $500 credit…"*

> "That sentence would have gone to real customers."

## 2:00–3:00 · Regression detection (**Compare → baseline #1, candidate #2**)

- Red banner: **Regression detected**. Read two reasons aloud.
- Metric deltas: pass rate −25 pts, schema −66.7 pts, **latency p95 improved −965 ms**.
- Per-case table: **4 regressed**, **1 fixed** (`policy-phone-hours` was failing its latency budget in production and the faster model fixes it), and 2 `f1 drop` early warnings.

> "This is the nuance a single average hides: the candidate is faster and fixes a production issue, yet it breaks four things that work today."

## 3:00–4:00 · Release decision (**Release reports → candidate #2, baseline #1**)

- **NOT READY FOR RELEASE**, 0/5 gates.
- Walk the gate table: thresholds agreed *before* the results, with actual values next to them.
- Point at **Notes**: "Metrics are lexical and structural checks. They do not verify factual correctness or detect hallucinations."

> "We're explicit about what this does and doesn't measure. It's a regression guardrail, not an oracle."

- Click **Download Markdown**: the attachment for the change ticket.
- Switch the candidate to **#3 (v1.5)** with baseline #1: **READY FOR RELEASE**, all gates pass, 0 regressed cases.

## 4:00–4:45 · Same gate in CI

```bash
CSRF=$(curl -s http://127.0.0.1:8000/auth/session \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['csrf_token'])")
curl -s -X POST http://127.0.0.1:8000/evaluations/gates \
  -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $CSRF" \
  -H 'X-Workspace-ID: 1' \
  -d '{"candidate_run_id": 2, "baseline_run_id": 1,
       "gates": {"min_pass_rate": 0.9, "max_forbidden_violations": 0, "max_regressed_cases": 0}}' \
  | python3 -m json.tool | head -12
```

> "The policy you just saw in the UI becomes a CI step: `decision: fail` blocks the merge."

## 4:45–5:00 · Close

> "Your app produces responses with whatever provider you use. EvalOps scores them deterministically, compares them with production, and gives every stakeholder the same go/no-go with the evidence attached. It runs locally for free, and next we'd grow the golden set from your real escalations."

---

### If something goes wrong live

| Symptom | Fix |
|---|---|
| UI shows "Cannot reach the EvalOps API" | `make backend` isn't running, or it's on a different port; check `VITE_API_BASE_URL` |
| Run IDs differ from the script | the DB had earlier data: `make clean-db`, restart backend, `make seed` |
| Port 5173 in use | Vite uses `strictPort`; stop the other process, or set a different port and add it to `EVALOPS_CORS_ORIGINS` |

---

## Optional extension (+3 minutes): RAG with local models

Setup: `make backend`, `make frontend`, `make rag-demo` (replays recorded Qwen answers and judge
verdicts; no model download needed).

1. **Datasets → kestrel-policy-rag-v1**: show the knowledge base (20 passages) and the questions with
   gold passages, including three marked *should decline*.
2. **Run history → the rag-v2 run**: the *RAG evidence (deterministic)* panel next to *Model-based
   scores (estimated, not ground truth)*. Expand `q-showroom-hours`: "The Seattle showroom opens at
   10 am on Saturdays." The heuristic and the judge both mark it unsupported.
3. **Compare (baseline rag-v1 → candidate rag-v2)**: regression detected; groundedness 0.78 → 0.52;
   estimated deltas in their own dashed card.
4. **Release reports**: candidate NOT READY. Then the honest twist: the baseline is not ready
   either, and why.
5. Close on calibration: "We measured our own judge. A 3B local judge disagrees with labels too
   often to gate on, so it's a second opinion, not a referee."
