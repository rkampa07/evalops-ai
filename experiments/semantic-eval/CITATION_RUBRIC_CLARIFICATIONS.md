# Citation rubric clarifications

This supplement clarifies how to apply the citation classes in `HUMAN_REVIEW.md`. It does not
change benchmark labels or decisions. The examples below are DEV examples only.

## Established interpretation

The existing rubric establishes that citation quality is separate from claim truth: `correct`
means the retrieved citation is appropriate evidence for evaluating the claim whether it supports,
contradicts, or leaves the claim unresolved. A contradiction between claim and passage does not by
itself make a citation wrong.

The existing DEV example **sem-048** establishes a useful `wrong_passage` case. The claim concerns
an LED bulb's warranty period. The cited passage, KB-WAR-01, is about furniture warranties; the
retrieved KB-WAR-02 passage specifically covers LED bulbs. The citation is in the warranty domain,
but another retrieved passage is needed to evaluate this claim's specific subject.

The finalized human decisions record `partial` for **sem-019**, **sem-020**, and **sem-054**, and
`correct` for **sem-059**. Those labels are established decisions. Where their rationale goes
beyond the existing definitions, the application below is a proposed clarification, not a claim
that the original rubric already stated that rule.

## Proposed operational clarification

- **`correct`** — The cited, retrieved passage is materially appropriate evidence for evaluating
  the claim as a whole. It may support, contradict, or leave the claim unresolved. Agreement with
  the claim is not required.
- **`partial`** — The citation materially evaluates only part of a multi-part claim, or covers a
  narrower scope or policy facet than the claim asserts. The label concerns the coverage of the
  citation, not whether the claim is true. Mere word overlap with one part is not enough: the
  omitted part, broader scope, or missing facet must be material to evaluating the claim.
- **`wrong_passage`** — The cited passage is related to the same policy domain or issue, but does
  not address the specific subject, condition, or rule needed to evaluate the claim; another
  retrieved passage does. Sem-048 is the established example.
- **`unrelated`** — The cited passage does not materially address the claim's policy issue. A
  shared broad topic word alone (such as “warranty”) does not make a passage related.
- **`not_retrieved`** — The cited passage ID is absent from the recorded retrieved-passage set.
  Whether the ID exists elsewhere does not change this class.

For boundaries between the classes: use `partial` when the citation addresses a material subset
of the claim's scope or facets; use `wrong_passage` when it is related but a different retrieved
passage is needed for the specific claim; use `unrelated` when it does not materially address the
policy issue. Use `correct` when the passage remains appropriate evidence for the claim as a
whole, including when it leaves the claim unresolved. If those distinctions cannot be made from
the passage and claim, the class needs human adjudication rather than inference from lexical
similarity.

## DEV boundary examples

- **sem-019 — finalized `partial`.** The claim says clearance items marked final sale are eligible
  for a refund. Cited KB-RET-02 says those items “cannot be returned.” The passage addresses the
  returnability facet and the relevant item scope, but does not state refund eligibility; the
  review explanation also distinguishes refunds that may occur without a return. Treating this as
  `partial` is consistent with the proposed incomplete-facet clarification. The original rubric
  did not explicitly say whether this return/refund distinction is partial or simply relevant
  evidence that leaves refund eligibility unresolved.
- **sem-020 — finalized `partial`.** The claim covers all items marked final sale. KB-RET-02 only
  names clearance items marked final sale. The passage covers a narrower item scope, matching the
  proposed scope clarification. It does not establish that every final-sale item is non-returnable.
- **sem-048 — finalized `wrong_passage`.** KB-WAR-01 concerns furniture warranties, while retrieved
  KB-WAR-02 gives the LED-bulb warranty period. This is related-domain evidence, but the specific
  retrieved passage is needed.
- **sem-054 — finalized `partial`; rationale unresolved.** The claim says white-glove delivery
  includes full assembly. KB-SHP-03 describes in-room placement and packaging removal, but does
  not mention assembly. The recorded `partial` label suggests the citation covers some service
  facets but not the asserted assembly facet. However, the claim is a single assertion, and the
  passage's list is not stated to be exhaustive. Under the established `correct` definition, the
  passage might instead be relevant evidence that leaves assembly unresolved. The existing rubric
  and decision record do not settle this confidently; further human adjudication is needed before
  treating this as a general rule for single-facet claims.
- **sem-059 — finalized `correct`; rationale unresolved.** The claim says chat support is staffed
  only by human agents. Cited KB-SUP-01 gives chat support hours, not staffing information. The
  recorded `correct` decision may interpret a passage about chat support as appropriate context
  that leaves staffing unresolved. It may also be only topically related. The rubric does not set
  a precise threshold between material evidence and shared topic; further human adjudication is
  needed before using this case as a general rule.

These clarifications do not revise the named examples' finalized decisions. They identify where
the recorded labels establish an outcome but do not establish a reusable rationale.
