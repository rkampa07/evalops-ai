# Claim labeling guideline

How to label one claim from an assistant's answer as `supported`, `unsupported` or `not_a_claim`
for the RAG label set (`examples/rag/labels/claims.jsonl`). Use the same rules for every row so
that 96 separate decisions add up to one consistent standard.

## The core test

Judge **only against the source passages shown in the row**. Ignore general knowledge and other
passages in the knowledge base. Then ask:

> If a customer relied on this claim, would they get the outcome the passages describe?

**supported:** every fact in the claim (who, what, when, how much, under which conditions) is
stated in or directly implied by the passages. Different wording is fine.
**unsupported:** the claim contradicts the passages, adds something they don't say, or drops or
widens something in a way that changes the outcome.
**not_a_claim:** the sentence asserts nothing about policy (see rule 5).

When a row is genuinely borderline, apply the rules below, pick a label, and explain in *Notes*.

## 1. Claims that omit important policy conditions → `unsupported`

A condition is *important* if leaving it out could change whether, when, or how much the
customer gets: eligibility, exceptions, thresholds, time limits, required item condition.

- Precedents in the set: c006 "Return shipping is free on every order" (drops *over $75*) and
  c020 "Standard shipping is free on all orders" (drops *over $49*), both drafted `unsupported`.
- **g001** "You have 30 days to return a sofa you bought." and **g019** "You have 30 days to return
  your sofa." The passage says *most* items, and only if *unused and in original packaging*; custom
  furniture is excluded elsewhere. The claim states the 30-day return as unconditional.
  Under this rule they lean `unsupported` (draft said `supported`; c006/c020 show the inconsistency).

Not important: omitting background that doesn't change the outcome (e.g. "to the original payment
method" when the question is only about the time limit).

## 2. Claims that broaden the scope of a policy → `unsupported`

The claim applies a rule to more people, products, channels or situations than the passage does.

- **g050** "Unfortunately, we cannot provide credits or discounts." The passage says *agents and
  the assistant cannot promise* credits; requests are escalated to a specialist. The claim widens
  "can't promise" into "the company can't provide". Under this rule it leans `unsupported`.
- Narrowing is fine if what remains is still true ("Gift cards cannot be returned" is a supported
  subset of the non-returnable list).

## 3. Minor wording differences vs unsupported instructions

Paraphrase is `supported` when the **action, place, timing and amount** are the same: "upload a
photo" ≈ "send a photo"; "within the first hour" = "within 1 hour".

An instruction is `unsupported` when it adds or changes a **step, channel, document or place**:

- g024 "Contact Kestrel Home Goods to report the damage." Passage: upload a photo on the Orders
  page. A different channel → `unsupported` (draft).
- **g023** "Take photos of the damage on the Orders page within 14 days of delivery." Passage:
  *upload* a photo on the Orders page within 14 days. Ask: would a customer following the claim
  end up doing what the policy requires? Place, timing and evidence match; the verb is imprecise
  ("take" vs "upload"). This is the **borderline** case. Choose by whether you read it as a wording
  slip (`supported`) or a changed step (`unsupported`), and record your reason in *Notes*.

## 4. Contradictions and missing evidence → `unsupported`

- **Contradiction:** the passage says the opposite (c010 "Damaged items must be returned…" vs
  "you do not need to return the damaged item"; c014 accidental damage is explicitly excluded).
- **Missing evidence:** the shown passages are silent. This holds even if the claim might be true,
  or a passage that wasn't retrieved would support it (g002 opened mattress: the mattress rule
  wasn't among the shown passages; g042 UK shipping; g054 showroom hours).
- **Silence is not a "no":** "No, we do not offer gift wrapping" (g055) is `unsupported` when the
  passages never mention gift wrapping. The right answer was to decline.

## 5. Statements that are not factual claims → `not_a_claim`

Empathy, greetings, transitions and generic offers of help: g049 "I'm sorry to hear that your
delivery was late.", g022 "If your lamp arrived broken, follow these steps:", g029 "Please contact
our customer service for further assistance."

It **is** a claim, and gets rules 1–4, as soon as it states a policy fact or a specific procedure:
compare g029 (generic, `not_a_claim`) with g024 (a specific reporting channel, a claim) and g009
("contact support with your order number and photos", a claim).

## Recording decisions

- Only you mark a row `Reviewed`. AI suggestions are context, never labels
  (see [rag-evaluation.md](rag-evaluation.md#reviewing-the-labels-in-excel-or-google-sheets)).
- If you change a draft, give the rule number in *Notes*, e.g. "R1: drops unused/packaging
  condition".

### Status of the four example cases

| Claim | Draft label | AI recommendation (not a label) | Human decision |
|---|---|---|---|
| g001 | supported | unsupported (R1) | pending review |
| g019 | supported | unsupported (R1) | pending review |
| g023 | supported | unsupported (R3, borderline) | pending review |
| g050 | supported | unsupported (R2) | pending review |

The AI recommendations are stored as `ai_recommendation` on these items in `claims.jsonl`. They
don't change `label` or `label_status`, and calibration ignores them.
