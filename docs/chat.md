# Live playground (chat + Execution Inspector)

The **Live playground** page lets you ask the policy assistant questions and inspect exactly what
happened on each turn. It is a debugging and test-authoring tool. It is **not** an evaluation run,
and it never affects comparisons or release gates.

## Enabling it

Chat is off by default (`EVALOPS_CHAT_BACKEND=off`); the API then answers `503 chat_disabled` and
holds no model in memory.

| `EVALOPS_CHAT_BACKEND` | What answers | Needs |
|---|---|---|
| `off` (default) | nothing | — |
| `fixture` | a deterministic stand-in that quotes the top passage and cites it | nothing; used by tests |
| `mlx` | `EVALOPS_CHAT_MODEL` (default `mlx-community/Qwen2.5-1.5B-Instruct-4bit`) via MLX | `make setup-local-ai` and an already-downloaded model |

```bash
make chat-fixture   # API with the fixture backend (no model)
make chat           # API with real local inference (MLX, Apple Silicon)
make frontend       # second terminal, then open http://127.0.0.1:5173/playground
```

The model loads lazily on the first message, only from the local Hugging Face cache
(`local_files_only`); nothing is downloaded. `make chat` runs without `--reload`, so the model
is loaded once. The API runs **one generation at a time**: a second request while one is running
gets `429 busy`, and the UI offers **Retry**. A failed generation stores nothing.

## What one turn does

1. **Retrieval.** BM25 (top-3) over the session's knowledge base. If the question looks like a
   follow-up, the previous user question is added to the retrieval query: either it has fewer than
   2 content words ("Canada?", "Why?"), or it has a reference word and fewer than 4 content words
   ("What about Vancouver?", "Does that apply to sale items?"). A question that names its own
   topic ("What is your shipping policy for Canada?") stands alone. The inspector says when the
   previous question was used.
2. **Prompt.** The session's recorded prompt template (`rag-v1` … `rag-v5`, unchanged, so recorded
   prompt hashes still match). On the first turn the messages are identical to the batch
   pipeline's. With history, a short chat note (`chat-v2`) is placed *before* the template's rules.
   History is the last 3 exchanges within 6,000 characters, as plain text. Passages appear only in the latest user message, so
   earlier answers can't be used as evidence.
3. **Generation.** Model id and revision, temperature/max tokens/seed, latency, prompt and
   generated tokens, tokens per second and peak memory (MLX).
4. **Live lexical check.** The methodology 2.0.0 RAG checks against **this turn's retrieved
   passages only**: citation validity, claim-level lexical groundedness and exact numbers.
   Abstention is reported but not graded, because chat has no ground truth about whether a
   question is answerable. The optional model judge is not run.

> The label says it plainly: *Not verified semantic correctness; not a release-gate result.*
> Word overlap can reject a correct paraphrase and accept a contradiction. Read the answer.

## Chat diagnostics (heuristic, chat-only)

A separate rule-based layer (`chat-diagnostics-v1`, `backend/evalops/chat/diagnostics.py`) flags
answers that may be misleading. It never changes the live check, methodology 2.0.0, runs or
gates, and it never edits or adds citations. Each warning shows the exact claim, the evidence
passage and the reason, and is marked *heuristic*. There are three states:

| State | Raised when |
|---|---|
| **Potential contradiction** | The answer says something is allowed and a retrieved passage says it is not, or the reverse. Covers return/refund/exchange, cancellation and warranty coverage, for a subject sharing at least two specific words. Also raised when two sentences in the same answer disagree that way |
| **Citation concern** | A cited id doesn't exist or wasn't retrieved this turn, or the cited passage shares under ⅓ of the sentence's words while another retrieved passage matches at least 30 points better |
| **Needs human review** | An opposite policy exists, but the subject match is weak (one shared word, such as "unopened mattresses" vs "unsealed mattresses"), or no retrieved passage matches the sentence at all |

Polarity is assigned only to explicit constructions ("can be returned", "cannot be returned",
"non-refundable", "eligible for a refund", "does not cover"). "You do not need to return the
damaged item" is not treated as a prohibition. A citation placed after the final period is
attributed to that sentence for diagnostics only.

**Measured (read-only, no inference), 2026-10-14:**

- **12 recorded live chat replies:** all 4 wrong custom-sofa answers flagged *Potential
  contradiction*, and all 4 wrong-passage citations flagged. No false positives.
- **112 recorded single-turn answers:** 1 *Potential contradiction*, the documented custom-sofa
  error. All 5 unrelated-citation warnings were genuinely wrong citations.
- Of 20 *Needs human review* items, about 14 were unsupported or invented statements. 4 were
  correct paraphrases the word matching can't recognise.

**Not covered:** contradictions outside the three action groups (shipping destinations, fees,
amounts), conditions and exceptions ("only within 1 hour"), and evidence that wasn't retrieved.
Diagnostics only see this turn's passages. Answers with no warnings may still be wrong.

## Execution Inspector

Every assistant reply has an **Inspect** button showing the stored trace: retrieval query and
whether the previous turn was used; passage ids, text and BM25 scores; template, prompt hash,
history included/dropped and the exact messages sent; model, settings and generation metrics; and
the live check with per-claim support. The UI shows only what the API stored.

## Save as test case

From the inspector you can turn a question into a regression test. **You** choose the expected
evidence from the passages retrieved on that turn, or mark that the assistant should decline.
Saving is disabled until one of those is chosen, and the model's answer is never copied into the
case. Cases go to a separate `<dataset>-chat-captures` dataset with a copy of the knowledge base, so
the source dataset, its version and existing runs don't change. For follow-ups, rewrite the
question so it stands alone before saving.

## Privacy and scope

- Local-only and protected by the API authentication dependency (binds to `127.0.0.1`).
- Messages and traces are stored in the local SQLite database so the inspector can show them.
  They are never written to logs. Deleting a chat deletes its messages and traces.
- Chat sessions are separate tables (migration `0004`, additive). Chat never creates evaluation
  runs, comparisons or gate results.

## Findings from real-model testing (Qwen2.5-1.5B, 2026-10-14)

Three questions, temperature 0, a small sample, not a benchmark:

- **Missing citations (chat-v1).** Placing the chat note *after* the template's citation rule
  lowered citation compliance. Offline: answered replies with a citation went from 7/7 in the
  batch format to 4/7 with the note appended. Uncited earlier replies in history lowered it
  further. chat-v2 (note first, only with history): 8/8 answered replies cited.
- **Citations present ≠ citations correct.** With chat-v2, only 2 of 8 cited the passage that
  actually supports the answer. One cited a passage that wasn't retrieved, which the check flags.
  Others cited a retrieved but irrelevant passage (for example, address changes for a shipping
  question), which the check does **not** flag. Citations are therefore never added automatically.
- **Contradictions are invisible to the lexical check.** "Custom-made furniture can be returned"
  and "… cannot be returned" receive the same result against KB-RET-02. All three prompts
  answered the custom-sofa question "Yes" (wrong). The live check failed them only because of word
  coverage ("sofa", "30" not in the passage), not because it detected the contradiction.
- **Per-sentence citation support** ("cited passage supports its sentence") is shown when the
  model puts the citation inside the sentence. A citation after the final period is not attached
  to any sentence by the methodology 2.0.0 splitter, so it isn't measured.

## Limitations

- One small local model. Expect slow first replies (model load) and an imperfect answer quality.
- Follow-up detection is a word-list heuristic; it can miss or over-trigger.
- The lexical check is a signal, not a verdict (see [RAG evaluation](rag-evaluation.md)).
- No streaming; the reply appears when generation finishes.
