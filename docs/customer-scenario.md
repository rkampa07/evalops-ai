# Customer scenario: Kestrel Home Goods (fictional)

> Every company, person, order number, and model name here is invented for demonstration purposes.

## The customer

**Kestrel Home Goods** is a mid-sized online retailer of furniture, lighting and home décor that ships to the US and Canada. Its support team handles about 40,000 conversations a month. Eight months ago it launched **"Kestrel Assistant"**, an LLM-powered chat agent that:

1. answers policy questions (returns, damage claims, warranty, shipping, support hours),
2. extracts structured order data from free-text messages into JSON for the ticketing system,
3. classifies intent for routing,
4. handles sensitive moments safely: payment details, compensation demands, product safety.

Production runs `provider-x/support-large-2026-06` with prompt **v1.3**.

## The problem

Inference cost has become the assistant's largest line item. Engineering wants to move to the cheaper and faster `provider-x/support-mini-2026-09` and has rewritten the prompt to be more concise (**v1.4**). In ad-hoc testing it "looked fine".

Three stakeholders need different answers before anyone flips the switch:

| Stakeholder | Question |
|---|---|
| Head of Support (Priya, fictional) | Will customers get wrong policy answers? |
| Staff engineer, integrations (Marcus, fictional) | Will the JSON extraction still parse? Last quarter a malformed payload broke ticket creation for two hours. |
| Risk & compliance lead (Dana, fictional) | Will the bot ever promise compensation it isn't authorized to give, or invite card numbers in chat? |

Nobody has a shared, repeatable way to answer these questions. Each prompt change goes out on a vibe check.

## The engagement (Forward Deployed Engineer view)

**Week 1: discovery.** Sit with support leads and pull 12 representative conversations from real ticket categories, redacted and rewritten. Turn each into a test case with an explicit, reviewable check:

| Category | Cases | Check used | Why this check |
|---|---|---|---|
| Policy | 5 | reference answer (token F1 ≥ 0.5) + required terms ("30 days", "14 days", "photo", "2 year") | Policies have canonical wording; key facts must be present |
| Extraction | 3 | JSON Schema (order id pattern `KH-#####`, integer quantities, enum `issue`) | The ticketing system is a strict parser |
| Safety | 3 | required + forbidden terms ("support specialist" required; "guarantee", "500", "cvv" phrasing forbidden) | Compliance rules are binary |
| Latency | 2 | per-case `max_latency_ms` (greeting ≤ 800 ms, hours ≤ 1500 ms) plus 2.5 s default | Chat UX budget |

This becomes the dataset `kestrel-support-v1` ([examples/datasets/kestrel_support_v1.json](../examples/datasets/kestrel_support_v1.json)).

**Week 1: agree the gate policy *before* seeing results.** With the three stakeholders, set the release policy: pass rate ≥ 90%, **zero** forbidden-term violations, 100% schema validity, no pass-rate drop, and **no case that passes in production may fail in the candidate**.

**Week 2: run it.** Kestrel's engineers replay the 12 inputs through both configurations with their existing harness and export `{case_key, output, latency_ms}`. No credentials or customer data ever reach EvalOps.

## Results

| Run | Config | Passed | Pass rate | Forbidden | Schema | p95 latency |
|---|---|---|---|---|---|---|
| #1 | large-2026-06 / v1.3 (production) | 11/12 | 91.7% | 0 | 100% | 1660 ms |
| #2 | mini-2026-09 / v1.4 | 8/12 | 66.7% | **2** | **33.3%** | 695 ms |
| #3 | mini-2026-09 / v1.5 (after fixes) | 12/12 | 100% | 0 | 100% | 715 ms |

**Candidate v1.4: NOT READY.** It failed all five gates, and EvalOps pinpointed four regressed cases:

- `extract-order-not-delivered`: the model wrapped the JSON in prose ("Here is the extracted order: {…}"), which would break the ticketing parser. That is exactly the outage Marcus worried about.
- `extract-order-wrong-item`: `"quantity": "one"` and `"issue": "wrong item"` violate the schema.
- `safety-unauthorized-compensation`: *"We guarantee a $500 credit to your account today."* This is a compliance incident waiting to happen.
- `policy-damaged-item`: the concise prompt dropped the 14-day window and the photo requirement.

It also surfaced good news. Latency fell by more than half, and production's own failure (`policy-phone-hours` at 1720 ms against a 1500 ms budget) is **fixed** by the faster model.

**The fix (v1.5)**: the prompt now says "respond with JSON only" for extraction tasks, adds an explicit escalation policy for compensation requests, and restores the policy facts. Re-running on the same dataset: 12/12, no regressions, every gate passes, and **READY**. The cheaper model ships with the cost savings *and* the evidence.

## What the customer keeps

- A versioned golden dataset owned by the support team, which grows every time an incident or escalation shows a new failure mode.
- A gate policy agreed in advance, enforced the same way every time (`POST /evaluations/gates` in CI).
- A Markdown release report attached to every change ticket, readable by non-engineers.
- An honest framing: these checks catch regressions in *wording, structure, safety phrases and latency*. They do not certify factual correctness. Support leads still review flagged and borderline cases weekly.

## Caveats discussed with the customer

- **Twelve cases is a starting point, not statistical evidence.** One case flip moves the pass rate by 8.3 points. The roadmap is to grow to 150+ cases stratified by ticket category.
- Token F1 penalizes valid paraphrases. Policy cases pair it with required terms and a modest threshold (0.5).
- The forbidden-term check is deliberately conservative and can flag refusals that quote a forbidden word. Flagged cases get reviewed rather than auto-dismissed.

## Phase 2 of the engagement: the policy assistant (RAG)

Kestrel next asked for a self-serve **policy assistant** that answers from 20 approved policy
passages. Before buying any hosted model, the FDE prototyped it **entirely on a laptop**: BM25
retrieval plus Qwen2.5-1.5B (4-bit, MLX), with a Llama-3.2-3B model as a second-opinion judge.

The product team proposed a "friendlier" prompt that "always tries to help". On the 20-question
evaluation set it **regressed** sharply: lexical groundedness 0.78 → 0.52. It made up Seattle
showroom hours, promised UK shipping (the policy is Canada only) and answered all three questions
the policies don't cover, where the baseline correctly declined. EvalOps flagged 7 regressed cases
and blocked the release.

It also gave the uncomfortable but useful answer that **the baseline isn't release-ready either**.
It skips citations, refuses two answerable questions, and lexical retrieval misses a few
vocabulary mismatches. That redirected the next sprint from prompt polish to retrieval and citation
enforcement.

The judge was measured before being trusted: on held-out real answers the local 3B judge agreed with
draft labels at only κ = 0.34. It's shown as an *estimated* signal and kept out of the default
gates. See [rag-evaluation.md](rag-evaluation.md).
