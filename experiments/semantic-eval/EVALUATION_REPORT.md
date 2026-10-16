# Semantic evaluation — frozen v12 and citation-evaluator-v4

## Scope and interpretation

This is an exploratory evaluation of two separate tasks:

- **Claim verification** asks whether retrieved policy evidence supports a claim, contradicts
  it, or leaves it insufficiently evidenced. Those are the three substantive human labels.
  `needs_review` is the claim evaluator's abstention, not a fourth gold class; it counts as
  incorrect against a substantive gold label.
- **Citation quality** asks whether a cited passage is appropriate evidence for evaluating the
  claim. It does **not** measure whether the claim itself is true. A citation can be `correct`
  when it supports, contradicts, or leaves the claim unresolved. Its five scored classes are
  `correct`, `wrong_passage`, `unrelated`, `not_retrieved`, and `partial`. `unresolved` is a
  predictor abstention and is reported separately from those classes.

The benchmark has 59 finalized examples: 39 DEV and 20 held-out TEST. Claim accuracy and citation
accuracy are reported separately, and DEV and TEST are not pooled. Saved artifacts:
[claim DEV](results/nli_dev_reviewed_92aacfdf47908332_20261016T151154.json),
[claim TEST](results/nli_test_reviewed_92aacfdf47908332_20261016T151346.json),
[citation DEV](results/citation_dev_2122c636400ed66a_20261016T225131Z.json), and
[citation TEST](results/citation_test_2122c636400ed66a_20261016T225336Z.json).

## Claim verification — frozen semantic-aggregate-v12

### Results

| Split | Examples | Correct | Incorrect | Accuracy | `needs_review` abstentions |
|---|---:|---:|---:|---:|---:|
| DEV | 39 | 31 | 8 | **79.49%** | 2 (5.13%) |
| TEST | 20 | 14 | 6 | **70.00%** | 4 (20.00%) |

Confusion matrices use human gold rows and evaluator predictions as columns.

**DEV**

| Gold / prediction | Supported | Contradicted | Insufficient evidence | Needs review |
|---|---:|---:|---:|---:|
| Supported | 9 | 0 | 3 | 0 |
| Contradicted | 0 | 10 | 1 | 2 |
| Insufficient evidence | 0 | 2 | 12 | 0 |

DEV errors: `sem-003` supported→insufficient_evidence; `sem-011` contradicted→needs_review;
`sem-019` insufficient_evidence→contradicted; `sem-021` contradicted→needs_review;
`sem-027` contradicted→insufficient_evidence; `sem-031` supported→insufficient_evidence;
`sem-049` supported→insufficient_evidence; `sem-055` insufficient_evidence→contradicted.
The false contradictions are **sem-019** and **sem-055**.

**TEST**

| Gold / prediction | Supported | Contradicted | Insufficient evidence | Needs review |
|---|---:|---:|---:|---:|
| Supported | 4 | 0 | 1 | 3 |
| Contradicted | 0 | 4 | 0 | 1 |
| Insufficient evidence | 0 | 1 | 6 | 0 |

| TEST class | Precision | Recall |
|---|---:|---:|
| Supported | 100.0% | 50.0% |
| Contradicted | 80.0% | 80.0% |
| Insufficient evidence | 85.7% | 85.7% |

TEST errors: `sem-001` supported→needs_review; `sem-002` supported→insufficient_evidence;
`sem-004` supported→needs_review; `sem-008` supported→needs_review;
`sem-014` contradicted→needs_review; `sem-033` insufficient_evidence→contradicted.
`sem-033` is the TEST false contradiction. Neither split had a false-supported prediction; these
small samples do not establish zero false-support risk.

## Citation quality — frozen citation-evaluator-v4

Accuracy counts unresolved predictions as non-correct over all applicable citations. Coverage is
the fraction of applicable examples assigned one of the five scored classes; classified-only
accuracy conditions on that subset. “Non-applicable” means no citation decision was required.

### Results

| Split | Applicable | Non-applicable | Correct / all applicable | Coverage | Classified-only accuracy | Unresolved | Missing / invalid |
|---|---:|---:|---:|---:|---:|---:|---:|
| DEV | 32 | 7 | 27/32 (**84.38%**) | 28/32 (87.50%) | 27/28 (96.43%) | 4 | 0 / 0 |
| TEST | 16 | 4 | 11/16 (**68.75%**) | 14/16 (87.50%) | 11/14 (78.57%) | 2 | 0 / 0 |

The DEV-to-TEST accuracy difference is **15.63 percentage points** (84.38% versus 68.75%). With
only 16 applicable TEST cases, this is a descriptive gap, not evidence of a statistically reliable
performance difference.

Confusion matrices use human citation gold rows. The five rubric classes are columns; abstentions
are shown in a separate `unresolved` column and are not folded into a scored class.

**DEV**

| Gold / prediction | Correct | Wrong passage | Unrelated | Not retrieved | Partial | Unresolved |
|---|---:|---:|---:|---:|---:|---:|
| Correct | 18 | 0 | 0 | 0 | 1 | 1 |
| Wrong passage | 0 | 3 | 0 | 0 | 0 | 0 |
| Unrelated | 0 | 0 | 1 | 0 | 0 | 1 |
| Not retrieved | 0 | 0 | 0 | 2 | 0 | 0 |
| Partial | 0 | 0 | 0 | 0 | 3 | 2 |

DEV errors and abstentions: `sem-019` partial→unresolved (`citation_set_ambiguous`);
`sem-025` correct→partial (`citation_set_partial_compound_coverage`);
`sem-046` unrelated→unresolved (`citation_set_ambiguous`);
`sem-054` partial→unresolved (`citation_set_ambiguous`); and
`sem-059` correct→unresolved (`citation_set_ambiguous`). The rationales for the finalized
human labels on **sem-054** and **sem-059** remain rubric-sensitive; see
[citation rubric clarifications](CITATION_RUBRIC_CLARIFICATIONS.md).

**TEST**

| Gold / prediction | Correct | Wrong passage | Unrelated | Not retrieved | Partial | Unresolved |
|---|---:|---:|---:|---:|---:|---:|
| Correct | 9 | 0 | 0 | 0 | 1 | 1 |
| Wrong passage | 0 | 2 | 0 | 0 | 1 | 0 |
| Unrelated | 0 | 0 | 0 | 0 | 0 | 0 |
| Not retrieved | 0 | 0 | 0 | 0 | 0 | 0 |
| Partial | 1 | 0 | 0 | 0 | 0 | 1 |

| TEST class | Gold support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| Correct | 11 | 10 | 90.0% | 81.8% | 85.7% |
| Wrong passage | 3 | 2 | 100.0% | 66.7% | 80.0% |
| Unrelated | 0 | 0 | N/A | N/A | N/A |
| Not retrieved | 0 | 0 | N/A | N/A | N/A |
| Partial | 2 | 2 | 0.0% | 0.0% | N/A* |

\*The saved scorer does not define F1 when precision plus recall is zero (it records `null`).
There are no held-out examples for `unrelated` or `not_retrieved`, so their TEST performance
cannot be estimated. TEST produced no correct `partial` predictions.

The five incorrect TEST predictions are preserved here:

| Example | Human gold | Prediction | Saved reason code |
|---|---|---|---|
| sem-010 | correct | partial | `citation_set_partial_scope` |
| sem-012 | correct | unresolved | `citation_set_ambiguous` |
| sem-017 | wrong_passage | partial | `citation_set_partial_scope` |
| sem-039 | partial | unresolved | `citation_set_ambiguous` |
| sem-043 | partial | correct | `citation_set_has_appropriate_passage` |

The TEST set is small (16 applicable examples), with no examples in two classes and only two
`partial` examples. Six of 24 KB families occur in both DEV and TEST, so this is an example-level
split rather than a family-held-out evaluation; related policy examples may cross the split.
The DEV and TEST citation artifacts contain the same frozen predictor and configuration. No
tuning was performed after TEST results were viewed.

## Frozen configurations and provenance

### Claim verification

- Evaluator: `semantic-aggregate-v12`; digest `92aacfdf47908332`.
- Model: `cross-encoder/nli-deberta-v3-small`, revision
  `fa2804872c3b4bd748f38c0185cc85775361e735`.
- Entailment and contradiction thresholds: 0.5; question context and existing guards enabled.
- Both runs used the default two-pass consistency check and produced identical predictions.
- Runtime / peak process RSS: DEV **13.152 s / 703.4 MB**; TEST **8.670 s / 755.8 MB**.
- Benchmark SHA-256: `23ed69e875a7a4d8be9059dc38cbcddf17d85a4084a7b6e4d6dff9cd01a561d8`.
- Split SHA-256: `75eaf297a94ebdbef8932c4763cabf8a7fceca0f2b97b5975b657db787faa574`.
- Finalized `human_review_decisions.csv` SHA-256: `ebd81be6452916468ec96f0156eb590698b9287bd0fd54de2daf506af750ca3e`.
- Evaluator and runner source hashes, and the saved environment/model metadata, are recorded in
  each linked claim result artifact.

### Citation quality

| Provenance item | DEV | TEST |
|---|---|---|
| Configuration digest | `2122c636400ed66a` | `2122c636400ed66a` |
| Predictor (`citation_evaluator.py`) SHA-256 | `33ee2d9f39258b5c7f8f6c53fa82b95730a880e500de819cb7aa262d0c51d84b` | same |
| DEV runner (`tools/semantic_eval/run_citation_dev.py`) SHA-256 | `49b00419e30bf860bc3fcefd2a67652462960db080aeac32220ce53739dd3e62` | same hash is listed in TEST artifact metadata; it does not identify the one-off driver |
| One-off TEST orchestration source/hash | Not applicable | Source not recovered; hash unavailable |
| Predictor-input SHA-256 | `abd5945abd5bdd1902a84aa34ad5b85bd2e61c9793aa0f8cc6917a6d843848ba` | `137471e30ecf43564b92af51b59b083c2a4bea0ce29137bf5e0fb0fed1930437` |
| Example-ID SHA-256 | `b657813b79a4922a46ab021609a67fe5c2dbe8dceda5cb7e470e6235e48ddd4a` | `b65674f60a52bdd548873d11b295231e8de27440641dbd62320751a08bed0d3b` |
| Human citation-label SHA-256 | `c1128be58262b28d7c1b7f6fcb8f0f84b87efdd3831f28c27ce4bf4312606a8f` | `c10f2b7788d4d7edff9ac58a108b3c3a4404c5de8773462cb30d8fe6c263471c` |
| Static KB ID registry SHA-256 | `345f0a15a337e61388f9246d1bf7a05d8e9b509fb6cce8fddfa5bc1c72b53347` | same |

The predictor hash identifies the classifier source; the runner hash identifies the reusable DEV
runner. The TEST evaluation was launched by a one-off inline orchestration driver. The saved TEST
artifact records the allowlisted predictor inputs, predictions, reasons, scoring labels, and
hashes, but does not contain that driver's source. Available local shell history and temporary
files did not yield the exact driver. Therefore no reconstructed script is presented as the
original, and the one-off TEST orchestration itself is not source-reproducible from the current
workspace. The artifact does preserve the inputs and outputs needed to audit the recorded result.

The TEST citation run took **0.008 s** and recorded **22.2 MB** peak memory. The DEV citation
artifact does not record runtime or peak memory.

## Limitations

This is a small exploratory benchmark, not a production readiness or release-gate assessment.
Claim TEST has 20 examples; citation TEST has only 16 applicable decisions. Class metrics are
unstable or unavailable for sparsely represented classes. Citation `sem-054` and `sem-059` retain
unresolved rubric rationale, and the DEV-to-TEST citation gap should be interpreted descriptively.
The example-level split shares six KB families across DEV and TEST. No tuning was performed after
viewing TEST results. Citation diagnostics and claim labels are separate tasks and must not be
used as substitutes for one another.
