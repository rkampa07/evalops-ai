"""Chat-only diagnostics (heuristic warnings). Methodology 2.0.0 scoring is not involved."""

import json
from pathlib import Path

import pytest

from evalops.chat.diagnostics import (
    CITATION_CONCERN,
    NEEDS_HUMAN_REVIEW,
    POTENTIAL_CONTRADICTION,
    diagnose,
    polarity,
)
from evalops.engine import rag
from evalops.llm.rag_pipeline import ABSTAIN_SENTENCE

KB = {
    p["passage_id"]: p["text"]
    for p in json.loads(
        (Path(__file__).resolve().parents[2] / "examples/rag/kestrel_policy_kb.json").read_text()
    )["passages"]
}
RETURNS = ["KB-RET-02", "KB-RET-01", "KB-RET-03"]  # retrieved live for the custom-sofa question
SHIPPING = ["KB-SHP-02", "KB-RET-03", "KB-ORD-02"]  # retrieved live for the Canada questions


def warnings(output, retrieved_ids):
    result = diagnose(output, {i: KB[i] for i in retrieved_ids}, KB)
    assert all(w["heuristic"] for w in result["warnings"])
    assert "Not verified semantic correctness" in result["label"]
    return [(w["state"], w["kind"]) for w in result["warnings"]], result["warnings"]


def test_correct_custom_sofa_refusal_has_no_warnings():
    assert warnings("No, custom-made furniture cannot be returned [KB-RET-02].", RETURNS)[0] == []
    assert warnings("No. Custom-made sofas are non-returnable. [KB-RET-02]", RETURNS)[0] == []


@pytest.mark.parametrize(
    "answer",
    [
        "Yes, you can return a custom-made sofa within 30 days [KB-RET-01].",  # live rag-v5
        "Yes, custom-made furniture can be returned [KB-RET-02].",  # passes the lexical check
    ],
)
def test_incorrect_custom_sofa_permission_is_a_potential_contradiction(answer):
    kinds, found = warnings(answer, RETURNS)
    assert (POTENTIAL_CONTRADICTION, "policy_polarity") in kinds
    warning = next(w for w in found if w["kind"] == "policy_polarity")
    assert warning["evidence"]["passage_id"] == "KB-RET-02"
    assert "cannot be returned" in warning["evidence"]["text"]
    assert warning["claim"].startswith("Yes")


def test_lexical_check_alone_cannot_see_the_contradiction():
    # Why this layer exists: methodology 2.0.0 scores "can" and "cannot" identically.
    def score(answer):
        metrics, failures = rag.evaluate_rag(
            answer, relevant_ids=[], expect_abstain=False, retrieved_ids=RETURNS,
            knowledge_base=KB, min_groundedness=0.8,
        )  # fmt: skip
        return metrics["groundedness"], failures

    assert (
        score("Yes, custom-made furniture can be returned [KB-RET-02].")
        == score("No, custom-made furniture cannot be returned [KB-RET-02].")
        == (1.0, [])
    )


def test_live_self_contradicting_answer_is_flagged_twice():
    answer = (
        "Yes, you can return a custom-made sofa within 30 days. However, please note that "
        "custom-made items are non-returnable."
    )  # recorded live, rag-v1 + chat-v1
    kinds, _ = warnings(answer, RETURNS)
    assert (POTENTIAL_CONTRADICTION, "policy_polarity") in kinds
    assert (POTENTIAL_CONTRADICTION, "self_contradiction") in kinds


def test_final_sale_versus_eligible_for_refund():
    kinds, found = warnings(
        "Clearance items marked final sale are eligible for a refund [KB-RET-02].", RETURNS
    )
    assert kinds == [(POTENTIAL_CONTRADICTION, "policy_polarity")]
    assert "final" in found[0]["reason"] and found[0]["evidence"]["passage_id"] == "KB-RET-02"


@pytest.mark.parametrize(
    "answer",
    [
        "The shipping policy for Vancouver is the same as for other Canadian cities. Orders to "
        "Vancouver usually arrive within 7 to 12 business days. [KB-SHP-02]",
        "We ship to Vancouver, Canada [KB-SHP-02]. Orders usually arrive in 7 to 12 business days "
        "[KB-SHP-02].",
    ],
)
def test_correct_vancouver_paraphrase_has_no_warnings(answer):
    assert warnings(answer, SHIPPING)[0] == []


def test_retrieved_but_unrelated_citation():
    answer = (
        "Our shipping policy for Canada is that we ship only to Canada. Orders usually arrive "
        "within 7 to 12 business days. [KB-ORD-02]"
    )  # recorded live, rag-v3: passed the lexical check
    kinds, found = warnings(answer, SHIPPING)
    assert kinds == [(CITATION_CONCERN, "citation_unrelated")]
    assert found[0]["evidence"]["passage_id"] == "KB-ORD-02"
    assert "KB-SHP-02" in found[0]["reason"] and "not changed automatically" in found[0]["reason"]


def test_citation_not_retrieved_and_nonexistent():
    kinds, found = warnings("We ship to Canada only. [KB-RET-02]", SHIPPING)
    assert kinds == [(CITATION_CONCERN, "citation_not_retrieved")]
    assert found[0]["evidence"]["passage_id"] == "KB-RET-02"
    kinds, _ = warnings("We ship to Canada only [KB-XYZ-99].", SHIPPING)
    assert kinds == [(CITATION_CONCERN, "citation_nonexistent")]


def test_unsupported_statement_with_no_matching_evidence_needs_review():
    kinds, found = warnings(
        "Every order comes with a complimentary gift basket and a personal thank-you call.",
        SHIPPING,
    )
    assert kinds == [(NEEDS_HUMAN_REVIEW, "no_matching_evidence")]
    assert "paraphrase" in found[0]["reason"]  # uncertainty is stated, not a verdict


def test_weak_subject_match_is_uncertain_not_a_contradiction():
    # RET-02 is about *unsealed* mattresses: scope differs, so ask a human.
    kinds, _ = warnings("Unopened mattresses can be returned within 30 days [KB-RET-01].", RETURNS)
    assert kinds == [(NEEDS_HUMAN_REVIEW, "policy_polarity_uncertain")]


@pytest.mark.parametrize(
    "sentence, expected",
    [
        ("Most items can be returned within 30 days.", {"return or refund": "allowed"}),
        ("Gift cards cannot be returned.", {"return or refund": "not allowed"}),
        ("Sale items are non-refundable.", {"return or refund": "not allowed"}),
        ("You are not eligible for a refund.", {"return or refund": "not allowed"}),
        ("You do not need to return the damaged item.", {}),  # not a prohibition
        ("Return shipping is free for orders over $75.", {}),  # noun use, no rule
        ("Refunds are issued within 5 business days.", {}),
        ("It does not cover pet damage.", {"warranty coverage": "not allowed"}),
        ("Orders can be changed or cancelled within 1 hour.", {"cancel": "allowed"}),
    ],
)
def test_polarity_needs_an_explicit_construction(sentence, expected):
    assert polarity(sentence) == expected


@pytest.mark.parametrize("pid", sorted(KB))
def test_quoting_a_passage_with_its_own_citation_raises_nothing(pid):
    # False-positive guard: every policy, quoted verbatim and cited, against the whole KB.
    output = " ".join(f"{s} [{pid}]" for s in KB[pid].split(". "))
    assert warnings(output, list(KB))[0] == []


def test_abstention_is_not_diagnosed():
    result = diagnose(ABSTAIN_SENTENCE, {}, KB)
    assert result["warnings"] == [] and result["claims_checked"] == 0
