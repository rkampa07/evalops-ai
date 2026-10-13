# Evaluation methodology

This document defines exactly what every EvalOps AI number means, how it is computed, and what it does **not** tell you. Implementation: `backend/evalops/engine/`.

## Scope and honest limits

EvalOps AI computes **deterministic lexical and structural checks**:

- Does the response match or overlap with a reference answer?
- Does it contain the phrases it must contain, and avoid the phrases it must not?
- Is it valid JSON that matches a schema?
- Was it fast enough?

It does **not**:

- verify that a response is factually correct,
- detect hallucinations or unsupported claims,
- judge tone, helpfulness, or reasoning quality,
- understand paraphrase. "Returns are accepted within a month" and "You have 30 days to return" share few tokens.

High token F1 means *the words overlap with the reference*. It does not mean *the answer is right*. The release report says so on every page. Treat these metrics as **regression detectors and guardrails**, and review failing and borderline cases by hand. Semantic and model-graded metrics are on the [roadmap](roadmap.md) and would be labelled as such.

## Text normalization

Used by exact match, token metrics, and term checks (`metrics.normalize_text`):

1. Unicode NFKC normalization (e.g. full-width `ＡＢＣ` → `ABC`).
2. Case folding (unless the run sets `case_sensitive: true`).
3. Every character that is not a word character or whitespace becomes a space (punctuation removal).
4. Whitespace collapsed to single spaces and trimmed.

Tokens are the space-separated pieces of the normalized text. Articles and stop words are **not** removed.

## Per-case checks

A check runs only when the test case defines it. **A case passes only if every applicable check passes.** A test case must define at least one check; the API rejects cases that could never fail.

| Check | Defined by | Passes when | Metrics recorded |
|---|---|---|---|
| Exact match | `expected_output` + run `require_exact_match: true` | normalized output == normalized reference | `exact_match` (always recorded when a reference exists) |
| Token F1 | `expected_output` | `token_f1 ≥ min_token_f1` (run config, default 0.5) | `token_precision`, `token_recall`, `token_f1` |
| Required terms | `required_terms` | every term is present | `required_terms_missing`, `required_terms_passed` |
| Forbidden terms | `forbidden_terms` | no term is present | `forbidden_terms_found`, `forbidden_terms_passed` |
| JSON Schema | `json_schema` | output parses as JSON **and** validates (Draft 2020-12) | `json_parsed`, `json_valid`, `schema_errors` (≤5, with JSON paths) |
| Latency | case `max_latency_ms`, else run `default_max_latency_ms` | `latency_ms ≤ limit` | `latency_ms`, `latency_limit_ms`, `latency_passed` |
| Response present | always | a response was submitted for the case | `responded` |

### Token precision, recall, F1

The SQuAD-style bag-of-tokens overlap, with multiset intersection:

```
overlap   = Σ_t min(count_pred(t), count_ref(t))
precision = overlap / |pred tokens|
recall    = overlap / |ref tokens|
F1        = 2·P·R / (P + R)
```

Edge cases: both empty → 1.0; exactly one empty → 0.0; no overlap → 0.0.

Example: reference *"the cat sat down"*, prediction *"the cat sat"* → P = 1.0, R = 0.75, F1 ≈ 0.857.

### Term matching

Terms are normalized the same way as text and matched as **whole-token phrases**: `" " + term + " "` must occur in `" " + text + " "`.

- `"refund"` does **not** match `"refunded"`. Add the variants you care about.
- `"$500"` normalizes to `"500"` and matches `"a $500 credit"`.
- Forbidden-term checks are **conservative**: a refusal that *quotes* a forbidden word ("I can't guarantee…") is still flagged. Pick forbidden phrases that are unambiguous, or review flagged cases.

### JSON Schema

Regex safety: user-supplied `pattern` values run through the `regex` module with a
**100 ms limit per match** and a **5 s total budget per run**. A timeout fails the case
("exceeded the evaluation limit"); it never stalls the server. `patternProperties` is
rejected when a case is saved, and again when one is evaluated, because `jsonschema`
evaluates those regexes internally without a time limit. Patterns are limited to 1,000
characters and 50 per schema. Any other schema-evaluation error (e.g. an unresolvable
remote `$ref`) fails the case instead of failing the request.

The output is `strip()`ped and parsed with `json.loads`. Prose around the JSON ("Here is the order: {…}") fails as `json_parsed: false`. That is intentional, because downstream parsers fail the same way. Schemas are validated when the test case is saved, so an invalid schema can never silently pass.

### Latency

Latency is **reported by the client** (`latency_ms` per response); EvalOps does not time anything itself. If a limit applies and no latency was reported, the check **fails** rather than passing silently.

### Missing responses

Cases with no submitted response are scored as failures (`missing_response`) and count in the pass-rate denominator. Responses referencing unknown case keys are rejected with 422 so typos can't silently drop cases.

## Run-level aggregates

| Metric | Definition | Denominator |
|---|---|---|
| `pass_rate` | passed cases / total cases | all test cases in the dataset |
| `exact_match_rate` | mean of `exact_match` | responded cases with a reference |
| `mean_token_precision/recall/f1` | arithmetic mean | responded cases with a reference |
| `required_terms_pass_rate` | mean of `required_terms_passed` | responded cases with required terms |
| `forbidden_terms_pass_rate` | mean of `forbidden_terms_passed` | responded cases with forbidden terms |
| `forbidden_violation_count` | total forbidden terms found | all responded cases |
| `schema_pass_rate` | mean of `json_valid` | responded cases with a schema |
| `latency_pass_rate` | mean of `latency_passed` | responded cases with a latency limit |
| `latency_p50/p95_ms` | percentile with linear interpolation (numpy default) | responded cases that reported latency |
| `tag_pass_rates` | pass rate per tag | cases carrying that tag |
| `failure_counts` | number of cases failing each check | all cases |

When no case applies, a rate is **`null` ("n/a"), never 0 or 1**, so a dataset without schemas can't report a "100% schema pass rate".

## Regression detection (baseline vs candidate)

Both runs must use the same dataset. Cases are joined on `case_key`.

### Comparison compatibility (checked before any regression claim)

A difference between two runs is only a *model* regression if everything else was held
constant. Before comparing, EvalOps checks:

| Check | If it fails | Severity |
|---|---|---|
| Same scoring config (`min_token_f1`, `require_exact_match`, `case_sensitive`, `default_max_latency_ms`) | Runs are **not comparable** | error |
| Same scoring-methodology **major** version | Not comparable | error |
| Methodology differs only in minor/patch | Comparable, warning shown | warning |
| Each case scored against an identical definition (hash of input, reference, terms, schema, latency limit) | That case is excluded as `definition_changed` | warning |
| Legacy result with no recorded definition | That case is excluded as `definition_unknown` | warning |
| Case exists in only one run | Excluded (`only_in_baseline` / `only_in_candidate`) | warning |
| At least one comparable case remains | Otherwise not comparable (`no_comparable_cases`) | error |

When runs are **not comparable**, the comparison returns `compatible: false`,
`has_regression: false` with no metric deltas or case verdicts, and lists the reasons.
Comparison gates (`max_pass_rate_drop`, `max_regressed_cases`) **fail closed** with a
"not comparable" message. The release is blocked, but no regression is claimed.

When comparable, aggregate metric deltas are **recomputed over the comparable cases only**, so
an edited, added or removed case can't move the baseline or candidate averages. A run's own
gates (`min_pass_rate`, etc.) still use its full summary.

### Versioning

- **Test case**: `version` increments and `content_hash` (SHA-256 of the canonical scoring
  definition) changes whenever input, reference, terms, schema or latency limit change. Editing
  only tags changes neither, because tags are labels, not scoring rules.
- **Dataset**: `version` increments whenever a case is added, deleted, renamed or its
  definition changes.
- **Run**: records `dataset_version` and `methodology_version`; every result stores
  `case_version`, `case_hash` and a full `case_snapshot` of the definition it was scored
  against, so history survives later edits and deletions.
- **Methodology** (`engine/versioning.py`, semver): MAJOR = scores may change for the same
  inputs (not comparable); MINOR = additive/robustness changes that leave well-formed inputs'
  scores unchanged; PATCH = no scoring impact. 1.0.0 → 1.1.0 added time-limited regex
  evaluation (below).
- **Pre-existing data** (migration `0002`): runs are marked methodology `1.0.0`. A legacy
  result gets a definition hash only if its test case still exists and was not modified after
  the run; otherwise its definition is recorded as unknown rather than guessed.

**Per case** (`regression.compare_runs`):

| Classification | Rule |
|---|---|
| `regressed` | passed in baseline, fails in candidate |
| `fixed` | failed in baseline, passes in candidate |
| `f1_drop` | same status, but token F1 fell by more than `case_f1_drop_threshold` (default 0.15). An early warning, not counted as a regression |
| `unchanged_pass` / `unchanged_fail` | same status, no large F1 drop |
| `only_in_baseline` / `only_in_candidate` | case set changed between runs |

**Aggregate metrics**: each tracked metric gets a status of `improved`, `regressed`, `unchanged`, or `not_applicable`:

- Rates and F1: regressed if `candidate − baseline < −metric_tolerance` (default 0.02, i.e. 2 points).
- `latency_p95_ms`: lower is better; regressed if it rises by more than `latency_tolerance_pct` of the baseline (default 20%).

`has_regression` is true if any case regressed or any tracked metric regressed. Tolerances exist because small datasets are noisy: on 12 cases one flip moves the pass rate by 8.3 points. Size tolerances to your dataset.

## Release gates

A gate policy is a set of optional thresholds (`engine/gates.py`):

| Gate | Metric | Comparator |
|---|---|---|
| `min_pass_rate`, `min_mean_token_f1`, `min_exact_match_rate`, `min_required_terms_pass_rate`, `min_schema_pass_rate`, `min_latency_pass_rate` | the matching aggregate | `≥` |
| `max_forbidden_violations`, `max_p95_latency_ms`, `max_failed_cases` | the matching aggregate | `≤` |
| `max_pass_rate_drop` | `max(0, baseline − candidate)` pass rate | `≤` (needs baseline) |
| `max_regressed_cases` | count of `regressed` cases | `≤` (needs baseline) |

Rules:

- Thresholds are **inclusive**.
- A gate whose metric is not applicable is **skipped** (shown, not blocking).
- A comparison gate configured **without a baseline fails closed**.
- Decision = `pass` only if no gate failed. At least one gate must be configured.

**Default release policy** (used by the report endpoint): `min_pass_rate 0.9`, `max_forbidden_violations 0`, `min_schema_pass_rate 1.0`, plus `max_pass_rate_drop 0` and `max_regressed_cases 0` when a baseline is supplied. When no baseline is given, the report says regression detection was not performed.

## Reproducibility

- Scoring is a pure function of (test case, response, run config): no randomness, clocks, or network.
- The run's scoring config is stored with the run.
- `tests/regression/test_golden_scores.py` pins exact aggregate values for the committed fixtures. Any change to normalization, tokenization or aggregation must update those values deliberately.

## Choosing thresholds: practical guidance

- Use token F1 for answers with a canonical wording (policies, facts with fixed phrasing). For open-ended answers prefer required/forbidden terms, or keep `min_token_f1` low and rely on terms.
- Put **safety and compliance** rules in forbidden/required terms and gate them at zero violations.
- Gate structured outputs with JSON Schema at 100%. A parser failure in production is an outage, not a quality dip.
- Start with `max_regressed_cases: 0` and review each regressed case. Some will be golden-set bugs, so fix the dataset rather than loosening the gate.
