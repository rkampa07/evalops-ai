# Three-minute demo: RAG v3 is still blocked

Show a measured improvement that still fails release policy. Replay the existing v1/v2/v3
recordings under methodology **2.0.0**, without regenerating answers or changing historical
recordings. V3 remains a historical experiment; v3, v4, and v5 are not release-ready.

## Before the demo

Use Python 3.13 and Node 22.12+:

```sh
make setup             # locked Python dependencies + npm ci
make backend           # terminal 1: http://127.0.0.1:8000
make frontend          # terminal 2: http://127.0.0.1:5173
make rag-demo          # terminal 3: recorded answers and saved judge verdicts, no inference
```

Replay adds/reuses runs in the local database without changing example recordings. Do not run
`make clean-db`, regenerate answers, import labels, or lower gates for this demo. Select runs by
the run IDs printed by the demo script. Existing runs keep their stored methodology versions;
the script creates new 2.0.0 evaluations instead of reusing 1.x runs. Verify **2.0.0** in Run
detail for both selected runs. Cross-major comparisons are incompatible: rescore the same
recordings under the same version rather than relabeling historical results.

For automated playback against a fresh temporary database (no developer database changes):

```sh
cd frontend
npm run test:e2e:install  # Chromium only; no AI models
npm run test:e2e -- e2e/evalops.spec.ts
```

Browser setup runs the actual demo twice in replay mode and checks idempotent seeding,
methodology 2.0.0, and the blocked v3 report.

## 0:00–0:35: What is being checked?

Open **Datasets → kestrel-policy-rag-v1**. Show the 20 policy passages and 20 questions, including
three marked **should decline**.

> "This is a fictional policy assistant. We check retrieval, citations, lexical support, and whether
> it declines questions the policies don't cover. These are guardrails, not factual verification."

## 0:35–1:20: Compare v1 with v3

Open **Compare**, select baseline `rag-v1` and candidate `rag-v3`.

- Both pass **9/20 cases**. V3's lexical groundedness rises from 77.8% to 80%; citation validity
  rises from 90% to 100%.
- Three cases improve, but three regress: showroom hours, gift wrapping, and financing.
- Recorded p95 latency rises from 965 ms to 1,219 ms, beyond the 20% comparison tolerance.
- Both selected runs must use 2.0.0; the original model responses remain unchanged.

> "The unchanged average hides a tradeoff: three fixes and three regressions. V3 answers all
> three unanswerable questions, so this is still a regression against the baseline."

## 1:20–2:10: The release decision

Open **Release reports**, select candidate `rag-v3` and baseline `rag-v1`.

**NOT READY FOR RELEASE**: five gates pass, three fail, one is skipped. Read the failed gates:

| Gate | Required | Actual |
|---|---|---|
| Pass rate | ≥ 90% | 45% |
| Abstention accuracy | 100% | 85% across all 20 questions; 0/3 unanswerable questions declined |
| Regressed cases | 0 | 3 |

> "We kept the policy unchanged. Passing the citation and groundedness gates is not enough to
> release. The skipped gate is JSON schema, which doesn't apply to this dataset."

Download Markdown if a report artifact is useful; it does not imply release approval.

## 2:10–3:00: Show the limitations

Open the v3 run and inspect `q-opened-mattress`: the output is only citation markers. Methodology
2.0.0 retains the citation-only rejection introduced in 1.3.0 and fails it with `content`;
1.2.0 incorrectly passed it, yielding 10/20 instead of 9/20. Short numeric answers are accepted
as content, but numeric claims must be grounded in retrieved evidence.
Four other answers have no citations, despite 100% validity of citations that are present.

Open the documented [custom-sofa false positive](rag-evaluation.md#known-limitation-custom-made-sofa-contradiction-passes-lexical-scoring).
The v5 prospective answer says a custom-made sofa can be returned within 30 days, citing the
general return policy. The retrieved specific policy says custom-made furniture **cannot be
returned**. Lexical scoring nevertheless passes it; human review remains necessary. This example
is documented, not seeded into the development-set UI. Reproduce it without inference:

```sh
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/pytest -p no:cacheprovider tests/regression/test_rag_known_limitations.py::test_known_limitation_custom_sofa_contradiction_passes_lexical_scoring
```

> "A green lexical result is not proof of semantic correctness. The judge panel is also an estimate:
> v3's estimated groundedness is 53.3%, with calibration precision 0.42 and kappa 0.34 against
> provisional labels. Only 5 of 96 labels are reviewed. V3 has no held-out question-set validation.
> Some improvements, still blocked."

Full metric definitions, calibration limits, and gate values are in
[RAG evaluation](rag-evaluation.md) and [Evaluation methodology](evaluation-methodology.md).
