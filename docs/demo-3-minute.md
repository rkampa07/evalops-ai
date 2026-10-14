# Three-minute demo: RAG v3 is still blocked

Show a measured improvement that still fails release policy. This walkthrough uses the audited
working tree: v3 recordings and methodology 1.3.0 are pending implementation/data changes, not
part of the documentation commit. If they are absent, use the v1/v2 comparison from the
[five-minute demo](demo-script.md); do not claim a v3 result from a v1/v2-only checkout.

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
prompt version, not a hard-coded ID. Existing runs keep their stored methodology versions; check
Run detail before presenting. The like-for-like results below were verified by rescoring all three
recordings under 1.3.0 in isolated tests, not by rewriting the developer database.

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
- If stored methodology versions differ, acknowledge the UI warning; cite the same-version
  rescoring rather than describing the stored runs as identical-version measurements.

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
1.3.0 correctly fails it with `content`; 1.2.0 incorrectly passed it, yielding 10/20 instead of 9/20.
Four other answers have no citations, despite 100% validity of citations that are present.

Point at the separate **estimated** judge panel:

> "The judge's v3 groundedness estimate is 53.3%, not ground truth and not a default gate. Its
> held-out claim precision was 0.42 and kappa 0.34 against provisional labels. Only 5 of 96 labels
> are reviewed. V3 was developed on these same 20 questions, so we have no held-out question-set
> validation. The honest result is: some improvements, still blocked."

Full metric definitions, calibration limits, and gate values are in
[RAG evaluation](rag-evaluation.md) and [Evaluation methodology](evaluation-methodology.md).
