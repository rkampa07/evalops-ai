"""Experimental semantic evaluator: aggregation, guards and backends (scripted fixture NLI).

No real model runs here. Each audit finding has regression tests (marked F2..F6 below).
"""

import builtins
import inspect
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from evalops.semantic.aggregate import AggregationConfig, _different_concept
from evalops.semantic.negation import negation_problems
from evalops.semantic.nli import (
    FixtureNLI,
    SemanticBackendUnavailable,
    TransformersNLI,
    require_commit_sha,
    verify_cached_snapshot,
)
from evalops.semantic.quantities import binding_problems, complementary_only, quantities, unverified
from evalops.semantic.segments import evidence_units, segments
from evalops.semantic.verdict import CONTRADICTED, INSUFFICIENT, NEEDS_REVIEW, SUPPORTED
from evalops.semantic.verifier import SemanticVerifier

KB = {
    p["passage_id"]: p["text"]
    for p in json.loads(
        (Path(__file__).resolve().parents[2] / "examples/rag/kestrel_policy_kb.json").read_text()
    )["passages"]
}
NEUTRAL = {"entailment": 0.05, "neutral": 0.9, "contradiction": 0.05}
ENTAIL = {"entailment": 0.92, "neutral": 0.06, "contradiction": 0.02}
CONTRA = {"entailment": 0.02, "neutral": 0.05, "contradiction": 0.93}
SHA = "0123456789abcdef0123456789abcdef01234567"


def scripted(rules):
    """rules: [(premise substring, hypothesis substring, scores)]; first match wins."""

    def fn(premise, hypothesis):
        for p, h, scores in rules:
            if p in premise and h in hypothesis:
                return scores
        return NEUTRAL

    return FixtureNLI(fn)


def evidence(*ids):
    return {i: KB[i] for i in ids}


# ------------------------------------------------------------------ aggregation basics


def test_supported_paraphrase_quotes_the_entailing_evidence():
    v = SemanticVerifier(scripted([("within 5 business days", "five business days", ENTAIL)]))
    verdict = v.verify(
        "When will my refund arrive?",
        "Refunds are issued within five business days of the return reaching the warehouse.",
        evidence("KB-RET-04", "KB-RET-01"),
    )
    assert verdict.label == SUPPORTED and verdict.guards == []
    assert verdict.deciding[0]["passage_id"] == "KB-RET-04"
    assert verdict.deciding[0]["unit"] in KB["KB-RET-04"]  # quoted verbatim, not generated
    assert "not proof" in verdict.note


def test_contradiction_custom_sofa():
    v = SemanticVerifier(scripted([("Custom-made furniture", "custom-made sofa", CONTRA)]))
    verdict = v.verify(
        "Can I return a custom-made sofa?",
        "Yes, you can return a custom-made sofa within 30 days.",
        evidence("KB-RET-02", "KB-RET-01", "KB-RET-03"),
    )
    assert verdict.label == CONTRADICTED and verdict.deciding[0]["passage_id"] == "KB-RET-02"


def test_nothing_decisive_is_insufficient_evidence():
    verdict = SemanticVerifier(scripted([]), AggregationConfig(include_question=False)).verify(
        "Can you gift wrap my order?", "Gift wrapping is available.", evidence("KB-ORD-01")
    )
    assert verdict.label == INSUFFICIENT and verdict.deciding == []
    assert SemanticVerifier(scripted([])).verify("q", "x is y.", {}).label == INSUFFICIENT


def test_scores_below_threshold_do_not_decide():
    weak = {"entailment": 0.45, "neutral": 0.3, "contradiction": 0.25}
    v = SemanticVerifier(scripted([("", "", weak)]), AggregationConfig(include_question=False))
    assert v.verify("q", "Refunds are issued.", evidence("KB-RET-04")).label == INSUFFICIENT


def test_citations_are_not_an_input():
    params = list(inspect.signature(SemanticVerifier.verify).parameters)
    assert params == ["self", "question", "claim", "evidence"]


def test_verdicts_are_deterministic_and_record_uncalibrated_scores():
    v = SemanticVerifier(scripted([("Custom-made", "sofa", CONTRA)]))
    args = ("q", "You can return a custom-made sofa.", evidence("KB-RET-02", "KB-RET-01"))
    first, second = v.verify(*args).to_dict(), v.verify(*args).to_dict()
    assert first == second
    assert first["uncalibrated_scores"] and "confidence" not in json.dumps(first)


def test_guards_can_be_disabled_for_ablation():
    config = AggregationConfig(
        number_guard=False, hedge_guard=False, polarity_guard=False, detached_guard=False
    )
    v = SemanticVerifier(scripted([("Refunds are issued within 5", "Refunds", ENTAIL)]), config)
    verdict = v.verify("q", "Refunds are issued within 2 business days.", evidence("KB-RET-04"))
    assert verdict.label == SUPPORTED and verdict.config["digest"] == config.digest()
    assert AggregationConfig().digest() != config.digest()


# ------------------------------------------------------------------ F2 numeric reasoning

RETURNS_30 = scripted([("returned within 30 days", "return", ENTAIL)])  # ignores numbers


def test_f2_30_day_policy_does_not_support_a_60_day_claim():
    verdict = SemanticVerifier(RETURNS_30).verify(
        "How long do I have to return a lamp?",
        "You can return items within 60 days of delivery.",
        evidence("KB-RET-01"),
    )
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["number"]
    assert "60" in verdict.reason


def test_f2_numbers_in_the_question_are_not_evidence():
    verdict = SemanticVerifier(RETURNS_30).verify(
        "My receipt says I have 60 days to return items. Is that right?",
        "Yes, you can return items within 60 days of delivery.",
        evidence("KB-RET-01"),
    )
    assert verdict.label == NEEDS_REVIEW and "number" in verdict.guards


def test_f2_matching_30_day_claim_is_supported():
    verdict = SemanticVerifier(RETURNS_30).verify(
        "How long do I have?",
        "Most items can be returned within thirty days of delivery.",
        evidence("KB-RET-01"),
    )
    assert verdict.label == SUPPORTED


@pytest.mark.parametrize(
    "claim, policy, verified",
    [
        ("Refunds are issued within 5 days.", "Refunds are issued within 5 business days.", False),
        (
            "Refunds are issued in 5 business days.",
            "Refunds are issued within 5 business days.",
            False,
        ),
        ("Return shipping is free over $75.", "Return shipping is free for orders over $75.", True),
        (
            "Return shipping is free over $80.",
            "Return shipping is free for orders over $75.",
            False,
        ),
        (
            "A $9.95 fee applies to orders of $75 or less.",
            "For orders of $75 or less, a $9.95 fee applies.",
            True,
        ),
        (
            "A $9.95 fee applies to orders over $75.",
            "For orders of $75 or less, a $9.95 fee applies.",
            False,
        ),
        (
            "Orders arrive in 3 to 7 business days.",
            "Orders usually arrive in 7 to 12 business days.",
            False,
        ),
        (
            "Prices dropping within a week qualify.",
            "If the price drops within 7 days, it qualifies.",
            True,
        ),
        ("Lamps have a two-year warranty.", "Lamps have a 1-year warranty.", False),
        ("It costs 129.", "White-glove delivery is available for a $129 fee.", False),
    ],
)
def test_f2_quantities_need_the_same_unit_value_range_and_bound(claim, policy, verified):
    assert (unverified(claim, policy) == []) is verified


def test_f2_parser_reads_ranges_comparators_and_conditions():
    found = {(q.low, q.high, q.unit, q.bound) for q in quantities(KB["KB-RET-03"])}
    assert found == {(75, 75, "money", ">"), (75, 75, "money", "<="), (9.95, 9.95, "money", "=")}
    (canada,) = quantities(KB["KB-SHP-02"])
    assert (canada.low, canada.high, canada.unit) == (7, 12, "business_day")


RET03 = {"KB-RET-03": KB["KB-RET-03"]}
PASSAGE_ONLY = FixtureNLI(lambda p, h: ENTAIL if p == KB["KB-RET-03"] else NEUTRAL)  # gullible


@pytest.mark.parametrize(
    "claim",
    [
        "$9.95 return shipping applies to orders over $75.",
        "A $9.95 return shipping fee applies to orders over $75.",  # sem-030 (contradicted)
        "Return shipping is free for orders of $75 or less.",
        "Orders of $75 or less get free return shipping.",
    ],
)
def test_f2_ret03_wrong_condition_binding_is_never_supported(claim):
    verdict = SemanticVerifier(PASSAGE_ONLY).verify("Is return shipping free?", claim, RET03)
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["number"]


@pytest.mark.parametrize(
    "claim",
    [
        "Return shipping is free for orders over $75.",
        "For orders of $75 or less, a $9.95 return shipping fee is deducted from the refund.",
    ],
)
def test_f2_ret03_correct_readings_stay_supported(claim):
    assert SemanticVerifier(PASSAGE_ONLY).verify("q", claim, RET03).label == SUPPORTED


def test_f2_quantities_from_different_sentences_never_combine():
    problems = binding_problems("A $9.95 fee applies to orders over $75.", KB["KB-RET-03"])
    assert problems and any("sentence" in p or "ambiguous" in p for p in problems)


def test_f2_competing_value_in_an_equally_related_sentence_is_ambiguous():
    text = "Standard returns take 5 days. Standard returns take 10 days."
    assert any("ambiguous" in p for p in binding_problems("Standard returns take 5 days.", text))


def test_m1_am_and_pm_are_different():
    assert binding_problems("Phone support opens at 8 pm Pacific time.", KB["KB-SUP-01"])
    assert binding_problems("Phone support opens at 8 a.m. Pacific time.", KB["KB-SUP-01"]) == []
    assert binding_problems("Phone support opens at 8 am Pacific time.", KB["KB-SUP-01"]) == []
    gullible = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL))
    verdict = gullible.verify("q", "Phone support opens at 8 pm.", evidence("KB-SUP-01"))
    assert verdict.label == NEEDS_REVIEW


@pytest.mark.parametrize(
    "claim, passage",
    [
        ("Approved claims receive two replacement parts.", "KB-WAR-03"),
        ("Half of the return shipping fee is refunded.", "KB-RET-03"),
        ("You can get a price adjustment twice.", "KB-PAY-02"),
        ("Refunds are issued on the 5th business day.", "KB-RET-04"),
    ],
)
def test_m2_number_words_fractions_and_ordinals_go_to_review(claim, passage):
    gullible = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL))
    assert gullible.verify("q", claim, evidence(passage)).label == NEEDS_REVIEW


def test_m2_identical_amount_word_in_the_same_sentence_is_accepted():
    policy = {"X-1": "Approved claims receive two replacement parts."}
    v = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL))
    assert (
        v.verify("q", "Approved claims receive two replacement parts.", policy).label == SUPPORTED
    )


def test_m2_one_time_and_articles_are_not_amounts():
    assert (
        binding_problems("Customers can request a one-time price adjustment.", KB["KB-PAY-02"])
        == []
    )


# ------------------------------------------------------------------ negation guard (heuristic)

GULLIBLE = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL))  # entails everything: worst case
RET03_S2 = "For orders of $75 or less, a $9.95 return shipping fee is deducted from the refund."


def test_negation_confirmed_75_dollar_false_approval_is_now_review():
    claim = "For orders of $75 or less, nothing is deducted from the refund."
    only_s2 = FixtureNLI(lambda p, h: ENTAIL if p in (RET03_S2, KB["KB-RET-03"]) else NEUTRAL)
    verdict = SemanticVerifier(only_s2).verify("Is anything deducted?", claim, RET03)
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["negation"]
    assert "heuristic" in verdict.reason and "deduct" in verdict.reason


@pytest.mark.parametrize(
    "claim, policy",
    [
        ("No fee is deducted.", "A fee is deducted."),
        ("A fee is deducted.", "No fee is deducted."),
        ("Returns are not allowed.", "Returns are allowed."),
        ("Returns are allowed.", "Returns are not allowed."),
        ("Customers cannot receive a refund.", "Customers can receive a refund."),
        ("Customers can receive a refund.", "Customers cannot receive a refund."),
    ],
)
def test_negation_clear_polarity_flips_go_to_review(claim, policy):
    verdict = GULLIBLE.verify("q", claim, {"X-1": policy})
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["negation"]


@pytest.mark.parametrize(
    "claim, policy",
    [
        ("No fee is charged.", "The fee is waived."),
        ("The fee is waived.", "No fee is charged."),
        (
            "Return shipping costs nothing for orders over $75.",
            "Return shipping is free for orders over $75.",
        ),
    ],
)
def test_negation_equivalent_no_charge_statements_are_preserved(claim, policy):
    assert GULLIBLE.verify("q", claim, {"X-1": policy}).label == SUPPORTED


@pytest.mark.parametrize(
    "claim, pid, supported",
    [
        (
            "Custom-made furniture cannot be returned.",
            "KB-RET-02",
            True,
        ),  # list commas aren't clauses
        ("Custom-made furniture is non-returnable.", "KB-RET-02", True),
        ("Custom-made furniture can be returned.", "KB-RET-02", False),
        ("We ship to Canada.", "KB-SHP-02", True),  # the "not" is about other countries
        ("We do not ship to other countries.", "KB-SHP-02", True),
        ("We ship to other countries.", "KB-SHP-02", False),
        ("You do not need to return the damaged item.", "KB-DMG-01", True),
        ("You need to return the damaged item.", "KB-DMG-01", False),
        ("The warranty does not cover pet damage.", "KB-WAR-01", True),
        ("The warranty covers pet damage.", "KB-WAR-01", False),
        ("Kestrel takes card numbers over the phone.", "KB-PAY-01", False),
        (
            "Standard shipping within the contiguous United States takes 3 to 7 business days.",
            "KB-SHP-01",
            True,
        ),
        ("Items must be unused and in their original packaging.", "KB-RET-01", True),
    ],
)
def test_negation_is_scoped_to_the_same_action_in_policy_text(claim, pid, supported):
    verdict = GULLIBLE.verify("q", claim, evidence(pid))
    assert (verdict.label == SUPPORTED) is supported, verdict.reason


def test_negation_unrelated_not_elsewhere_in_the_passage_is_ignored():
    policy = {"X-1": "Refunds are issued within 5 business days. Gift cards are not refundable."}
    assert negation_problems("Refunds are issued within 5 business days.", policy["X-1"]) == []


@pytest.mark.parametrize(
    "claim, policy, reason",
    [
        ("It is not true that no fee is deducted.", "A fee is deducted.", "double or stacked"),
        ("Refunds are issued; no questions are asked.", "Refunds are issued.", "no counterpart"),
    ],
)
def test_negation_unreliable_polarity_goes_to_review(claim, policy, reason):
    problems = negation_problems(claim, policy)
    assert any(reason in p for p in problems)
    assert GULLIBLE.verify("q", claim, {"X-1": policy}).label == NEEDS_REVIEW


def test_negation_known_limit_antonyms_without_a_negator_are_not_detected():
    # Documented limitation: word lists, not semantics. The NLI model has to catch these.
    assert negation_problems("Returns are prohibited.", "Returns are allowed.") == []


def test_negation_guard_can_be_disabled_for_ablation():
    off = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL), AggregationConfig(negation_guard=False))
    assert off.verify("q", "No fee is deducted.", {"X-1": "A fee is deducted."}).label == SUPPORTED


# ------------------------------------------------------------------ subject / event binding, order

SHIPPING_OPTIONS = {"X-1": "Shipping options: $5 standard shipping, $15 express shipping."}


@pytest.mark.parametrize(
    "claim, supported",
    [
        ("Standard shipping costs $15.", False),  # value belongs to another item
        ("Shipping costs $15.", False),  # can't tell which item
        ("Express shipping costs $15.", True),
        ("Standard shipping costs $5.", True),
    ],
)
def test_subject_binding_value_must_attach_to_the_claimed_item(claim, supported):
    verdict = GULLIBLE.verify("q", claim, SHIPPING_OPTIONS)
    assert (verdict.label == SUPPORTED) is supported, verdict.reason
    if not supported:
        assert verdict.guards == ["number"] and "attach" in verdict.reason


@pytest.mark.parametrize(
    "claim, policy, supported",
    [
        (
            "Items can be returned within 30 days of purchase.",
            "Items can be returned within 30 days of delivery.",
            False,
        ),
        (
            "Items can be returned within 30 days of delivery.",
            "Items can be returned within 30 days of purchase.",
            False,
        ),
        (
            "Items can be returned within 30 days of delivery.",
            "Items can be returned within 30 days.",
            False,
        ),
        (
            "Items can be returned within 30 days of delivery.",
            "Items can be returned within 30 days of delivery.",
            True,
        ),
        (
            "Items can be returned within 30 days of arrival.",
            "Items can be returned within 30 days of delivery.",
            True,
        ),
        (
            "Items can be returned within 30 days.",
            "Items can be returned within 30 days of delivery.",
            True,
        ),
    ],
)
def test_event_binding_duration_must_start_from_the_same_event(claim, policy, supported):
    verdict = GULLIBLE.verify("q", claim, {"X-1": policy})
    assert (verdict.label == SUPPORTED) is supported, verdict.reason
    if not supported:
        assert verdict.guards == ["number"]


def test_event_binding_on_policy_text():
    assert binding_problems("Upload a photo within 14 days of purchase.", KB["KB-DMG-01"])
    assert (
        binding_problems(
            "Upload a photo on the Orders page within 14 days of delivery.", KB["KB-DMG-01"]
        )
        == []
    )
    assert (
        binding_problems(
            "Prices that drop within a week of your purchase qualify.", KB["KB-PAY-02"]
        )
        == []
    )


WARRANTIES = {"W-1": "Lamps have a 1-year warranty.", "W-2": "Lamps have a 2-year warranty."}


@pytest.mark.parametrize(
    "claim", ["Lamps have a 1-year warranty.", "Lamps have a 2-year warranty."]
)
def test_conflicting_passages_give_the_same_verdict_in_either_order(claim):
    forward = GULLIBLE.verify("q", claim, WARRANTIES)
    backward = GULLIBLE.verify("q", claim, dict(reversed(list(WARRANTIES.items()))))
    assert forward.label == backward.label == NEEDS_REVIEW
    assert (forward.guards, forward.deciding, forward.reason) == (
        backward.guards,
        backward.deciding,
        backward.reason,
    )


@pytest.mark.parametrize(
    "claim",
    [
        "Lamps and light fixtures have a 1-year warranty.",
        "Furniture has a 2-year limited warranty covering manufacturing defects.",
        "LED bulbs sold separately are covered for 90 days.",
        "Approved claims receive a repair kit, a replacement part, or a replacement item.",
    ],
)
@pytest.mark.parametrize("backend", ["gullible", "selective"])
def test_verdicts_are_invariant_to_every_evidence_order(claim, backend):
    import itertools

    ids = ["KB-WAR-01", "KB-WAR-02", "KB-WAR-03"]
    selective = scripted(
        [
            ("Lamps and light fixtures", "Lamps", ENTAIL),
            ("Furniture has", "Furniture", ENTAIL),
            ("LED bulbs", "LED", ENTAIL),
            ("Approved claims", "Approved", ENTAIL),
        ]
    )
    model = GULLIBLE if backend == "gullible" else SemanticVerifier(selective)
    outcomes = {
        (v.label, tuple(v.guards), json.dumps(v.deciding, sort_keys=True), v.reason)
        for order in itertools.permutations(ids)
        for v in [model.verify("q", claim, {i: KB[i] for i in order})]
    }
    assert len(outcomes) == 1, outcomes


@pytest.mark.parametrize(
    "claim, policy",
    [
        ("Refunds post in up to 7 days.", "Refunds post in no more than 7 days."),
        ("Refunds post in no more than 7 days.", "Refunds post in up to 7 days."),
        ("Refunds post within 7 days.", "Refunds post in no more than 7 days."),
        ("Claims need no fewer than 2 photos.", "Claims need at least 2 photos."),
    ],
)
def test_equivalent_bounds_are_not_rejected(claim, policy):
    verdict = GULLIBLE.verify("q", claim, {"X-1": policy})
    assert verdict.label == SUPPORTED, verdict.reason


def test_threshold_and_amount_in_one_sentence_are_not_rivals():
    claim = "For orders of $75 or less, a fee is deducted from the refund."
    assert binding_problems(claim, KB["KB-RET-03"]) == []


def test_bound_phrases_are_not_negation_but_real_negation_still_counts():
    assert (
        negation_problems("Refunds post in up to 7 days.", "Refunds post in no more than 7 days.")
        == []
    )
    assert negation_problems("Refunds do not post in 7 days.", "Refunds post in 7 days.")
    policy = {"X-1": "Refunds post in no more than 7 days."}
    assert GULLIBLE.verify("q", "Refunds post in more than 7 days.", policy).label == NEEDS_REVIEW


# ------------------------------------------------------------------ Stage A failure classes
# Independent fixtures (invented policies, not benchmark examples). Each scripted model
# reproduces the failure mode seen with a real small NLI model.


def scripted_exact(table):
    """{premise: scores}; anything else is neutral."""
    return FixtureNLI(lambda p, h: table.get(p, NEUTRAL))


GYM = {
    "G-1": "Members can freeze their gym membership for up to 3 months per year.",
    "G-2": "Lockers must be emptied every night before closing.",
}


def test_class1_unrelated_set_aside_contradiction_does_not_block_support():
    model = scripted_exact({GYM["G-1"]: ENTAIL, GYM["G-2"]: CONTRA})
    verdict = SemanticVerifier(model).verify(
        "Can I pause my membership?",
        "Members can freeze their gym membership for up to 3 months per year.",
        GYM,
    )
    assert verdict.label == SUPPORTED and verdict.guards == []
    assert [i["passage_id"] for i in verdict.ignored_contradictions] == ["G-2"]
    assert verdict.ignored_contradictions[0]["kind"] == "unrelated"
    assert "Lockers" in verdict.ignored_contradictions[0]["unit"]


def test_class1_unrelated_contradiction_alone_is_not_a_contradicted_verdict():
    model = scripted_exact({GYM["G-2"]: CONTRA})
    verdict = SemanticVerifier(model, AggregationConfig(include_question=False)).verify(
        "q", "The pool opens at 6 am.", {"G-2": GYM["G-2"]}
    )
    assert verdict.label == INSUFFICIENT and verdict.ignored_contradictions


def test_class1_related_contradiction_still_counts():
    model = scripted_exact({GYM["G-1"]: CONTRA})
    verdict = SemanticVerifier(model).verify(
        "q",
        "Members can freeze their gym membership for up to 6 months per year.",
        {"G-1": GYM["G-1"]},
    )
    assert verdict.label == CONTRADICTED and verdict.ignored_contradictions == []


def test_class1_unrelated_contradiction_in_the_question_pass_is_set_aside():
    model = FixtureNLI(
        lambda p, h: CONTRA if "Customer question" in p and "Lockers" in p else NEUTRAL
    )
    verdict = SemanticVerifier(model).verify(
        "Is the pool open early?", "The pool opens at 6 am.", {"G-2": GYM["G-2"]}
    )
    assert verdict.label == INSUFFICIENT and verdict.config["question_pass"] == "run"
    assert verdict.ignored_contradictions[0]["with_question"] is True


@pytest.mark.parametrize(
    "policy, claim, guard",
    [
        ("Hotel shuttles may run up to 20 minutes late during peak hours.",
         "Hotel shuttles always run on time during peak hours.", "hedged_contradiction"),
        ("Checkout usually takes place by 11 am.", "Checkout takes place by 11 am at the latest.",
         "hedged_contradiction"),
        ("The breakfast buffet includes eggs, fruit, and pastries.",
         "The breakfast buffet includes pancakes.", "open_list"),
        ("Room service offers drinks such as tea and coffee.",
         "Room service offers fresh orange juice drinks.", "open_list"),
    ],
)  # fmt: skip
def test_class2_hedged_or_open_list_contradictions_are_insufficient(policy, claim, guard):
    verdict = SemanticVerifier(scripted_exact({policy: CONTRA})).verify("q", claim, {"X-1": policy})
    assert verdict.label == INSUFFICIENT and verdict.guards == [guard]


@pytest.mark.parametrize(
    "policy, claim",
    [
        ("Hotel shuttles run every 30 minutes.", "Hotel shuttles run every 10 minutes."),
        # Permission "may" is not a hedge: a real contradiction stays contradicted.
        ("Guests may request a late checkout.", "Guests cannot request a late checkout."),
        ("The breakfast buffet includes only eggs, fruit, and pastries.",
         "The breakfast buffet includes pancakes."),
        ("Breakfast does not include pancakes.", "Breakfast includes pancakes."),
    ],
)  # fmt: skip
def test_class2_unhedged_or_exclusive_contradictions_stay_contradicted(policy, claim):
    verdict = SemanticVerifier(scripted_exact({policy: CONTRA})).verify("q", claim, {"X-1": policy})
    assert verdict.label == CONTRADICTED and verdict.guards == []


def test_class2_hedge_in_an_unrelated_sentence_does_not_soften_a_contradiction():
    first = "Spa bookings are non-refundable."
    policy = {"S-1": first + " Weekend slots may fill quickly."}
    verdict = SemanticVerifier(scripted_exact({first: CONTRA})).verify(
        "q", "Spa bookings are refundable.", policy
    )
    assert verdict.label == CONTRADICTED


PARKING = {
    "P-1": "Parking is free for stays over 3 days. "
    "For stays of 3 days or less, parking costs $15 per day."
}
P_FREE, P_FEE = (
    "Parking is free for stays over 3 days.",
    "For stays of 3 days or less, parking costs $15 per day.",
)


def test_class3_complementary_condition_does_not_block_matching_support():
    model = scripted_exact({P_FREE: ENTAIL, PARKING["P-1"]: ENTAIL, P_FEE: CONTRA})
    verdict = SemanticVerifier(model).verify("Is parking free for a week?", P_FREE, PARKING)
    assert verdict.label == SUPPORTED and verdict.guards == []
    assert verdict.ignored_contradictions[0]["kind"] == "complementary"
    assert P_FEE in verdict.ignored_contradictions[0]["unit"]


def test_class3_contradiction_under_the_same_condition_is_kept():
    claim = "Parking is free for stays of 3 days or less."
    model = scripted_exact({P_FEE: CONTRA, P_FREE: CONTRA})
    verdict = SemanticVerifier(model).verify("q", claim, PARKING)
    assert verdict.label == CONTRADICTED
    assert verdict.deciding[0]["unit"] == P_FEE  # the opposite-condition sentence was set aside


LIBRARY = {"L-1": "Reference books, rare maps, and periodicals cannot be borrowed."}


def test_class4_exception_inside_a_list_missed_by_the_model_goes_to_review():
    verdict = SemanticVerifier(scripted_exact({})).verify(
        "Can I borrow a map?", "Rare maps can be borrowed.", LIBRARY
    )
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["possible_exception"]
    assert "cannot be borrowed" in verdict.reason


@pytest.mark.parametrize(
    "claim, policy",
    [
        ("Rare maps cannot be borrowed.", LIBRARY),  # same polarity
        (
            "Rare maps can be borrowed.",
            {"L-2": "Laptops cannot be used in the quiet room."},
        ),  # unrelated
    ],
)
def test_class4_no_false_exception_flags(claim, policy):
    verdict = SemanticVerifier(
        scripted_exact({}), AggregationConfig(include_question=False)
    ).verify("q", claim, policy)
    assert verdict.label == INSUFFICIENT and verdict.guards == []


def test_screening_never_turns_a_contradiction_into_support_without_entailment():
    # Set-aside contradictions never create support; an ordinary opposite numeric condition
    # alone is insufficient and unrelated-only evidence is also insufficient.
    model = scripted_exact({GYM["G-2"]: CONTRA, P_FEE: CONTRA})
    verdict = SemanticVerifier(model, AggregationConfig(include_question=False)).verify(
        "q", P_FREE, {"G-2": GYM["G-2"], **PARKING}
    )
    assert verdict.label == INSUFFICIENT
    assert verdict.ignored_contradictions
    unrelated_only = SemanticVerifier(scripted_exact({GYM["G-2"]: CONTRA}), NO_Q).verify(
        "q", P_FREE, {"G-2": GYM["G-2"]}
    )
    assert unrelated_only.label == INSUFFICIENT


# ------------------------------------------------------------------ v7: Codex v6 audit (H1, H2)

NO_Q = AggregationConfig(include_question=False)


def two_passages(support, contradict):
    return FixtureNLI(
        lambda p, h: ENTAIL if p == support else CONTRA if p == contradict else NEUTRAL
    )


CODEX_CASES = [
    ("Smoking is permitted.", "Smoking is permitted.", "Tobacco use is prohibited."),  # synonym
    ("Pets are welcome.", "Pets are welcome.", "Pets are prohibited."),  # short claim
    ("Refunds are available after 30 days.", "Refunds are available after 30 days.",
     "Refunds are available only within 30 days."),  # exclusive condition
]  # fmt: skip


@pytest.mark.parametrize("claim, support, contradict", CODEX_CASES)
@pytest.mark.parametrize("order", ["support_first", "contradiction_first"])
def test_codex_h1_h2_strong_contradiction_never_lets_a_claim_be_approved(
    claim, support, contradict, order
):
    ev = (
        {"A": support, "B": contradict}
        if order == "support_first"
        else {"B": contradict, "A": support}
    )
    verdict = SemanticVerifier(two_passages(support, contradict), NO_Q).verify("q", claim, ev)
    assert verdict.label == NEEDS_REVIEW, verdict.reason


@pytest.mark.parametrize(
    "claim, support, contradict",
    [
        ("Smoking is allowed on balconies.", "Smoking is allowed on balconies.",
         "Cigarettes are banned throughout the property."),
        ("Dogs are welcome.", "Dogs are welcome.", "Animals are not allowed."),
        ("Late checkout is free.", "Late checkout is free.", "A fee applies after noon."),
    ],
)  # fmt: skip
def test_v7_synonym_and_short_claim_contradictions_go_to_review(claim, support, contradict):
    verdict = SemanticVerifier(two_passages(support, contradict), NO_Q).verify(
        "q", claim, {"A": support, "B": contradict}
    )
    assert verdict.label == NEEDS_REVIEW


def test_v10_synonym_based_contradiction_remains_actionable_without_overlap():
    claim, contradict = "Smoking is permitted.", "Tobacco use is prohibited."
    verdict = SemanticVerifier(two_passages("(none)", contradict), NO_Q).verify(
        "q", claim, {"B": contradict}
    )
    assert verdict.label == CONTRADICTED


@pytest.mark.parametrize(
    "exclusive",
    [
        "Refunds are available only within 30 days.",
        "Refunds are available exclusively within 30 days.",
        "Refunds must be requested no later than 30 days.",
        "Refunds are available within 30 days at the latest.",
        "Refunds are not available after 30 days.",
    ],
)
def test_v7_exclusive_conditions_are_never_treated_as_harmless(exclusive):
    claim = "Refunds are available after 30 days."
    assert complementary_only(claim, exclusive) is False
    verdict = SemanticVerifier(two_passages(claim, exclusive), NO_Q).verify(
        "q", claim, {"A": claim, "B": exclusive}
    )
    assert verdict.label == NEEDS_REVIEW


def test_v10_non_exclusive_complementary_condition_alone_is_insufficient():
    other = "Refunds within 30 days go to the original payment method."
    claim = "Refunds after 30 days are issued as store credit."
    verdict = SemanticVerifier(scripted_exact({other: CONTRA}), NO_Q).verify(
        "q", claim, {"B": other}
    )
    assert verdict.label == INSUFFICIENT and verdict.guards == ["complementary_condition"]


@pytest.mark.parametrize("order", [0, 1])
def test_v10_complementary_condition_in_the_same_policy_does_not_block_support(order):
    first = "Refunds after 30 days are issued as store credit."
    second = "Refunds within 30 days go to the original payment method."
    text = " ".join([first, second] if order == 0 else [second, first])
    model = FixtureNLI(
        lambda p, h: ENTAIL if p in (first, text) else CONTRA if p == second else NEUTRAL
    )
    verdict = SemanticVerifier(model, NO_Q).verify("q", first, {"R-1": text})
    assert verdict.label == SUPPORTED and verdict.guards == []
    assert verdict.ignored_contradictions[0]["kind"] == "complementary"


FREE_50 = "Shipping is free on orders over $50."


@pytest.mark.parametrize(
    "claim, other, complementary",
    [
        (FREE_50, "Orders of $50 or less pay $5 shipping.", True),
        ("Orders of $50 or less pay $5 shipping.", FREE_50, True),
        (FREE_50, FREE_50, False),  # same condition
        (FREE_50, "Orders of $40 or less pay $5 shipping.", False),  # a different value
        (FREE_50, "Only orders of $50 or less pay shipping.", False),  # exclusive wording
    ],
)
def test_v7_numeric_boundary_complements(claim, other, complementary):
    assert complementary_only(claim, other) is complementary


def test_v7_related_conflicting_passages_go_to_review_in_both_orders():
    support, contradict = "Pets are welcome in all rooms.", "Pets are not allowed in any rooms."
    for ev in ({"A": support, "B": contradict}, {"B": contradict, "A": support}):
        verdict = SemanticVerifier(two_passages(support, contradict), NO_Q).verify(
            "q", "Pets are welcome in all rooms.", ev
        )
        assert verdict.label == NEEDS_REVIEW


REFUND_AFTER = "Refunds are available after 30 days."
SWEEP_CASES = [
    ("Smoking is permitted.", {"A": "Smoking is permitted.", "B": "Tobacco use is prohibited."}),
    (REFUND_AFTER, {"A": REFUND_AFTER, "B": "Refunds are available only within 30 days."}),
    ("Parking is free for stays over 3 days.", PARKING),
    ("Members can freeze their gym membership for up to 3 months per year.", GYM),
    ("Return shipping is free for orders over $75.", evidence("KB-RET-03", "KB-SHP-01")),
]


# ------------------------------------------------------------------ v8: Codex v7 audit

REFUND_IMPLICIT = [
    "Refund eligibility expires within 30 days.",
    "Refunds are restricted to requests within 30 days.",
    "Refunds are available within 30 days, with late requests rejected.",
]
FREE_50_100 = "Shipping is free for orders over $50 and under $100."
MIXED = "Orders of $50 or less pay shipping; orders over $75 pay shipping."


def layouts(support, contradict):
    return {
        "separate": {"A": support, "B": contradict},
        "separate_reversed": {"B": contradict, "A": support},
        "same_passage": {"P": f"{support} {contradict}"},
        "same_passage_reversed": {"P": f"{contradict} {support}"},
    }


def support_vs_contradict(support, contradict):
    """Entails exactly the supporting sentence; contradicts every unit containing the other."""
    return FixtureNLI(
        lambda p, h: ENTAIL if p == support else CONTRA if contradict in p else NEUTRAL
    )


@pytest.mark.parametrize("contradict", REFUND_IMPLICIT)
@pytest.mark.parametrize(
    "layout", ["separate", "separate_reversed", "same_passage", "same_passage_reversed"]
)
def test_v8_codex_f1_implicit_exclusivity_never_approves(contradict, layout):
    ev = layouts(REFUND_AFTER, contradict)[layout]
    verdict = SemanticVerifier(support_vs_contradict(REFUND_AFTER, contradict), NO_Q).verify(
        "q", REFUND_AFTER, ev
    )
    assert verdict.label == NEEDS_REVIEW, verdict.reason


@pytest.mark.parametrize(
    "layout", ["separate", "separate_reversed", "same_passage", "same_passage_reversed"]
)
def test_v8_codex_f2_complementary_condition_cannot_hide_another_contradiction(layout):
    ev = layouts(FREE_50_100, MIXED)[layout]
    verdict = SemanticVerifier(support_vs_contradict(FREE_50_100, MIXED), NO_Q).verify(
        "q", FREE_50_100, ev
    )
    assert verdict.label == NEEDS_REVIEW, verdict.reason
    if verdict.ignored_contradictions:  # the diagnosis is kept, but it didn't approve
        assert verdict.ignored_contradictions[0]["kind"] == "complementary"


@pytest.mark.parametrize(
    "claim, contradict",
    [
        ("Breakfast is free for stays over 2 days.",
         "For stays of 2 days or less breakfast costs $10, and weekend stays pay for breakfast."),
        ("Returns are accepted after 14 days.",
         "Returns are accepted within 14 days; afterwards they are declined."),
        ("Late checkout is free after 2 pm.",
         "Late checkout is free until 2 pm, then $20 per hour."),
    ],
)  # fmt: skip
@pytest.mark.parametrize("layout", ["separate", "same_passage_reversed"])
def test_v8_mixed_clauses_and_multiple_conditions_never_approve(claim, contradict, layout):
    ev = layouts(claim, contradict)[layout]
    verdict = SemanticVerifier(support_vs_contradict(claim, contradict), NO_Q).verify(
        "q", claim, ev
    )
    assert verdict.label != SUPPORTED, verdict.reason


@pytest.mark.parametrize("claim, support, contradict", CODEX_CASES)
@pytest.mark.parametrize("layout", ["same_passage", "same_passage_reversed"])
def test_v8_previous_v6_v7_failures_also_hold_inside_one_passage(
    claim, support, contradict, layout
):
    ev = layouts(support, contradict)[layout]
    verdict = SemanticVerifier(support_vs_contradict(support, contradict), NO_Q).verify(
        "q", claim, ev
    )
    assert verdict.label == NEEDS_REVIEW


@pytest.mark.parametrize("contradict", [*REFUND_IMPLICIT, MIXED])
def test_v10_explicit_or_mixed_numeric_conditions_remain_contradictions(contradict):
    claim = FREE_50_100 if contradict == MIXED else REFUND_AFTER
    model = FixtureNLI(lambda p, h: CONTRA if contradict in p else NEUTRAL)
    verdict = SemanticVerifier(model, NO_Q).verify("q", claim, {"B": contradict})
    assert verdict.label == CONTRADICTED


SWEEP_CASES += [
    (REFUND_AFTER, {"P": f"{REFUND_AFTER} {REFUND_IMPLICIT[0]}"}),
    (REFUND_AFTER, {"P": f"{REFUND_IMPLICIT[2]} {REFUND_AFTER}"}),
    (FREE_50_100, {"P": f"{FREE_50_100} {MIXED}"}),
    (FREE_50_100, {"A": FREE_50_100, "B": MIXED}),
]


# -------------------------------------------------------- Stage 1: scoped contradiction screening


def test_stage1_supported_claim_ignores_unrelated_model_contradiction():
    claim = "Members can cancel a booking before arrival."
    policy = "Members can cancel a booking before arrival."
    unrelated = "Pool towels must be returned before checkout."
    model = scripted_exact({policy: ENTAIL, unrelated: CONTRA})
    verdict = SemanticVerifier(model, NO_Q).verify("q", claim, {"P-1": policy, "P-2": unrelated})
    assert verdict.label == SUPPORTED
    assert verdict.ignored_contradictions[0]["kind"] == "unrelated"


def test_stage1_supported_claim_ignores_opposite_numeric_condition():
    claim = "Delivery is free for orders over $50."
    support = "Delivery is free for orders over $50."
    opposite = "Orders of $50 or less pay a $5 delivery fee."
    model = scripted_exact({support: ENTAIL, opposite: CONTRA})
    verdict = SemanticVerifier(model, NO_Q).verify("q", claim, {"P-1": support, "P-2": opposite})
    assert verdict.label == SUPPORTED
    assert verdict.ignored_contradictions[0]["kind"] == "complementary"


def test_stage1_standard_shipping_does_not_contradict_return_shipping():
    claim = "Standard shipping is free for premium orders."
    support = "Standard shipping is free for premium orders."
    other_concept = "Return shipping is charged for premium orders."
    model = scripted_exact({support: ENTAIL, other_concept: CONTRA})
    verdict = SemanticVerifier(model, NO_Q).verify(
        "q", claim, {"P-1": support, "P-2": other_concept}
    )
    assert verdict.label == SUPPORTED
    assert verdict.ignored_contradictions[0]["kind"] == "different_concept"


def test_stage1_opposite_numeric_condition_alone_is_insufficient():
    claim = "Parking is free for stays over 3 days."
    other_condition = "For stays of 3 days or less, parking costs $15 per day."
    verdict = SemanticVerifier(scripted_exact({other_condition: CONTRA}), NO_Q).verify(
        "q", claim, {"P-1": other_condition}
    )
    assert verdict.label == INSUFFICIENT
    assert verdict.ignored_contradictions[0]["kind"] == "complementary"


def test_stage1_narrower_evidence_scope_does_not_support_broader_claim():
    claim = "Items marked final sale cannot be returned."
    narrower = "Clearance items marked final sale cannot be returned."
    verdict = SemanticVerifier(scripted_exact({narrower: ENTAIL}), NO_Q).verify(
        "q", claim, {"P-1": narrower}
    )
    assert verdict.label == INSUFFICIENT
    assert verdict.guards == ["scope"]
    assert "clearance" in verdict.reason.lower()


def test_stage1_specific_policy_exception_still_blocks_approval():
    claim = "Gift cards can be returned."
    general = "Most items can be returned within 30 days."
    exception = "Gift cards cannot be returned."
    model = scripted_exact({general: ENTAIL, exception: CONTRA})
    verdict = SemanticVerifier(model, NO_Q).verify("q", claim, {"P-1": general, "P-2": exception})
    assert verdict.label != SUPPORTED
    assert verdict.label in (CONTRADICTED, NEEDS_REVIEW)


# ------------------------------------------------------------------ v9: Stage B failure classes
# Independent, invented policies; scripted models reproduce the observed failure modes.

GUEST = "Members may bring one guest per visit; guests may not use the sauna."
ONTARIO = "We deliver to Ontario only; we do not deliver to other provinces."


@pytest.mark.parametrize(
    "claim, policy, supported",
    [
        (GUEST, GUEST, True),  # identical multi-clause claim: was flagged against one clause
        ("Members may bring one guest per visit.", GUEST, True),
        ("Members may bring one guest per visit; guests may use the sauna.", GUEST, False),
        ("We deliver to Ontario.", ONTARIO, True),
        ("We deliver to other provinces.", ONTARIO, False),
    ],
)
def test_v9_class1_negation_compares_clause_with_clause(claim, policy, supported):
    verdict = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL), NO_Q).verify(
        "q", claim, {"X": policy}
    )
    assert (verdict.label == SUPPORTED) is supported, verdict.reason
    if not supported:
        assert verdict.guards == ["negation"]


D_FREE = "Delivery is free for orders over $100."
D_FEE = "Orders of $100 or less pay an $8 delivery fee."
EXACT_FEE = "For an order of exactly $100, an $8 delivery fee applies."


@pytest.mark.parametrize("passage", [f"{D_FREE} {D_FEE}", f"{D_FEE} {D_FREE}"])
def test_v9_class2_exact_value_is_not_contradicted_by_a_strict_threshold(passage):
    model = FixtureNLI(lambda p, h: CONTRA if p == D_FREE else NEUTRAL)
    verdict = SemanticVerifier(model, NO_Q).verify("q", EXACT_FEE, {"D": passage})
    assert verdict.label == INSUFFICIENT and verdict.guards == ["complementary_condition"]
    assert verdict.ignored_contradictions[0]["kind"] == "complementary"


def test_v9_class2_wrong_exact_value_claim_is_still_contradicted():
    claim = "For an order of exactly $100, delivery is free."
    model = FixtureNLI(lambda p, h: CONTRA if p == D_FEE else NEUTRAL)
    verdict = SemanticVerifier(model, NO_Q).verify("q", claim, {"D": f"{D_FREE} {D_FEE}"})
    assert verdict.label == CONTRADICTED  # "$100 or less" includes exactly $100


EXACT_100 = "A fee applies to orders of exactly $100."


@pytest.mark.parametrize(
    "claim, other, complementary",
    [
        (EXACT_100, "Fees apply to orders over $100.", True),
        (EXACT_100, "Fees apply to orders under $100.", True),
        (EXACT_100, "Fees apply to orders of $100 or more.", False),  # inclusive bound
        (EXACT_100, "Fees apply to orders of $100 or less.", False),  # inclusive bound
        ("Fees apply to orders over $100.", EXACT_100, True),
    ],
)
def test_v9_class2_exact_value_boundaries(claim, other, complementary):
    assert complementary_only(claim, other) is complementary


SHUTTLE = (
    "The airport shuttle, which includes luggage handling and bottled water, "
    "is available for a $40 fee."
)


@pytest.mark.parametrize(
    "claim",
    [
        "The airport shuttle is free for guests staying over 3 days.",
        "The airport shuttle costs $25.",
    ],
)
def test_v9_class3_open_list_does_not_soften_a_fee_or_price_contradiction(claim):
    verdict = SemanticVerifier(scripted_exact({SHUTTLE: CONTRA})).verify("q", claim, {"H": SHUTTLE})
    assert verdict.label == CONTRADICTED and verdict.guards == []


def test_v11_nonexclusive_list_missing_a_feature_is_insufficient():
    claim = "The airport shuttle includes a free newspaper."
    verdict = SemanticVerifier(scripted_exact({SHUTTLE: CONTRA})).verify("q", claim, {"H": SHUTTLE})
    assert verdict.label == INSUFFICIENT and verdict.guards == ["open_list"]


RETURN_FEE = "For orders of $50 or less, a $6 return shipping fee is deducted from the refund."
STORE = {"S-1": RETURN_FEE, "S-2": "Premium membership costs $40 per year."}


@pytest.mark.parametrize("order", [("S-1", "S-2"), ("S-2", "S-1")])
def test_v9_class4_differently_qualified_concept_is_not_a_contradiction(order):
    ev = {k: STORE[k] for k in order}
    model = FixtureNLI(lambda p, h: CONTRA if p == RETURN_FEE else NEUTRAL)
    verdict = SemanticVerifier(model, NO_Q).verify(
        "q", "Premium members get free shipping on every order.", ev
    )
    assert verdict.label == INSUFFICIENT and verdict.guards == []
    assert verdict.ignored_contradictions[0]["kind"] == "different_concept"


def test_v9_class4_same_concept_contradiction_is_kept():
    claim = "Return shipping is free for orders of $50 or less."
    model = FixtureNLI(lambda p, h: CONTRA if p == RETURN_FEE else NEUTRAL)
    verdict = SemanticVerifier(model, NO_Q).verify("q", claim, {"S-1": RETURN_FEE})
    assert verdict.label == CONTRADICTED


@pytest.mark.parametrize(
    "claim, text, concepts",
    [
        ("Premium members get free shipping.", RETURN_FEE, ["ship"]),
        ("Return shipping is free.", RETURN_FEE, []),  # same qualifier
        ("Shipping is free.", RETURN_FEE, []),  # claim doesn't qualify the word
        ("Hotel shuttles always run on time.", "Hotel shuttles run late.", []),  # adverb
        # modal
        ("Guests cannot request a late checkout.", "Guests may request a late checkout.", []),
        ("Room service offers fresh juice drinks.", "Room service offers drinks such as tea.", []),
    ],
)  # fmt: skip
def test_v9_class4_concept_check_ignores_modals_adverbs_and_shared_words(claim, text, concepts):
    assert _different_concept(claim, text) == concepts


SWEEP_CASES += [
    (GUEST, {"X": GUEST}),
    ("We deliver to other provinces.", {"X": ONTARIO}),
    (EXACT_FEE, {"D": f"{D_FREE} {D_FEE}"}),
    ("The airport shuttle is free for guests staying over 3 days.", {"H": SHUTTLE}),
    ("Premium members get free shipping on every order.", STORE),
]


# ------------------------------------------------------------------ F3 hedges


def test_f3_certain_claim_from_hedged_evidence_is_insufficient():
    v = SemanticVerifier(scripted([("usually arrive in 7 to 12", "7 to 12", ENTAIL)]))
    verdict = v.verify(
        "How long?", "Orders to Canada take 7 to 12 business days.", evidence("KB-SHP-02")
    )
    assert verdict.label == INSUFFICIENT and verdict.guards == ["hedge"]


def test_f3_hedged_paraphrase_with_equivalent_range_is_supported():
    v = SemanticVerifier(scripted([("usually arrive in 7 to 12", "7 to 12", ENTAIL)]))
    verdict = v.verify(
        "How long?",
        "Canadian orders usually arrive within 7 to 12 business days.",
        evidence("KB-SHP-02"),
    )
    assert verdict.label == SUPPORTED


def test_f3_unrelated_hedge_in_the_same_passage_is_ignored():
    # Only the whole RET-04 passage entails; its "Banks may take ..." sentence is about banks.
    v = SemanticVerifier(scripted([(KB["KB-RET-04"], "Refunds are issued", ENTAIL)]))
    verdict = v.verify(
        "When is my refund issued?",
        "Refunds are issued within 5 business days after the returned item arrives at our "
        "warehouse.",
        evidence("KB-RET-04"),
    )
    assert verdict.deciding[0]["scope"] == "passage"
    assert verdict.label == SUPPORTED and verdict.guards == []


@pytest.mark.parametrize(
    "policy, claim",
    [
        (
            "Refunds can take up to 10 business days to post.",
            "Refunds can take up to 10 business days to post.",
        ),
        (
            "Warranty claims need at least 2 photos.",
            "You need at least 2 photos for a warranty claim.",
        ),
        (
            "Customers may request a one-time price adjustment.",
            "Customers can request a one-time price adjustment.",
        ),
    ],
)
def test_f3_bounds_and_permission_are_not_hedges(policy, claim):
    v = SemanticVerifier(scripted([(policy, "", ENTAIL)]))
    assert v.verify("q", claim, {"X-1": policy}).label == SUPPORTED


# ------------------------------------------------------------------ F5 question context


def test_f5_question_with_a_wrong_assumption_never_makes_a_claim_supported():
    # This "model" believes whatever the customer asserts.
    v = SemanticVerifier(scripted([("custom sofas are returnable", "custom sofa", ENTAIL)]))
    verdict = v.verify(
        "Since custom sofas are returnable, how do I send mine back?",
        "You can send your custom sofa back.",
        evidence("KB-RET-02", "KB-RET-01"),
    )
    assert verdict.label == INSUFFICIENT and verdict.guards == ["question_context"]
    assert "unverified" in verdict.reason


def test_f5_evidence_alone_is_judged_first_and_the_question_is_not_a_premise_there():
    seen = []
    v = SemanticVerifier(FixtureNLI(lambda p, h: seen.append(p) or ENTAIL))
    verdict = v.verify(
        "I bought it 5 days ago.",
        "Customers can request a one-time price adjustment.",
        evidence("KB-PAY-02"),
    )
    assert verdict.label == SUPPORTED and verdict.config["question_pass"] == "not needed"
    assert seen == [KB["KB-PAY-02"]]


def test_f5_question_context_cannot_establish_an_unsupported_condition():
    # R1: "bought 5 days ago" satisfies PAY-02's 7-day condition, but only by trusting the customer.
    v = SemanticVerifier(scripted([("bought 5 days ago", "price adjustment", ENTAIL)]))
    question = "The rug I bought 5 days ago is now cheaper. Can I get the difference back?"
    claim = "Yes, you can request a price adjustment for the difference."
    verdict = v.verify(question, claim, evidence("KB-PAY-02"))
    assert verdict.label == INSUFFICIENT and verdict.config["question_pass"] == "run"
    off = SemanticVerifier(v.backend, AggregationConfig(include_question=False))
    assert off.verify(question, claim, evidence("KB-PAY-02")).label == INSUFFICIENT


def test_f5_conflicting_question_context_is_described_as_a_conflict():
    def fn(premise, hypothesis):
        if "Customer question" not in premise:
            return NEUTRAL  # evidence alone settles nothing
        if "Most items can be returned" in premise:
            return ENTAIL
        if "Custom-made" in premise:
            return CONTRA
        return NEUTRAL

    # Negation-based checks off so this exercises the question-context pass on its own
    # (the missed-exception check would otherwise send it to review earlier).
    verdict = SemanticVerifier(FixtureNLI(fn), AggregationConfig(negation_guard=False)).verify(
        "I bought a custom sofa last week.",
        "My custom-made sofa can be returned.",  # about the same item as RET-02 (relatedness rule)
        evidence("KB-RET-01", "KB-RET-02"),
    )
    assert verdict.label == NEEDS_REVIEW and "conflicts" in verdict.reason
    relations = {d["relation"] for d in verdict.deciding}
    assert relations == {"supports (with question)", "contradicts (with question)"}


def test_v12_question_only_contradiction_remains_insufficient():
    def fn(premise, hypothesis):
        return CONTRA if "Customer question" in premise else NEUTRAL

    verdict = SemanticVerifier(
        FixtureNLI(fn), AggregationConfig(negation_guard=False, polarity_guard=False)
    ).verify(
        "Is chat support staffed by people?",
        "Chat support is staffed only by human agents.",
        {"S-1": "Chat support is available every day."},
    )
    assert verdict.label == INSUFFICIENT
    assert verdict.guards == ["question_context"]
    assert verdict.config["question_pass"] == "run"


def test_v12_evidence_only_contradiction_stays_contradicted():
    policy = "Gift cards cannot be returned."
    claim = "Gift cards can be returned."
    verdict = SemanticVerifier(scripted([(policy, claim, CONTRA)])).verify(
        "Can I return a gift card?", claim, {"RET": policy}
    )
    assert verdict.label == CONTRADICTED
    assert verdict.guards == []
    assert verdict.config["question_pass"] == "not needed"


def test_v12_mixed_question_context_signals_still_need_review():
    allow = "Gift cards can be returned."
    deny = "Gift cards cannot be returned."

    def fn(premise, hypothesis):
        if "Customer question" not in premise:
            return NEUTRAL
        if allow in premise:
            return ENTAIL
        if deny in premise:
            return CONTRA
        return NEUTRAL

    verdict = SemanticVerifier(
        FixtureNLI(fn), AggregationConfig(negation_guard=False, polarity_guard=False)
    ).verify(
        "Can I return a gift card?",
        allow,
        {"ALLOW": allow, "DENY": deny},
    )
    assert verdict.label == NEEDS_REVIEW
    assert verdict.guards == ["question_context"]
    assert {d["relation"] for d in verdict.deciding} == {
        "supports (with question)",
        "contradicts (with question)",
    }


# ------------------------------------------------------------------ F6 segmentation


def test_f6_dependent_sentences_stay_attached():
    assert segments(KB["KB-ORD-01"]) == [KB["KB-ORD-01"]]  # "After that, contact support ..."
    assert segments(KB["KB-WAR-01"]) == [KB["KB-WAR-01"]]  # "It does not cover ..."
    assert len(segments(KB["KB-RET-01"])) == 2
    units = evidence_units(evidence("KB-RET-01", "KB-ORD-01"))
    assert ("KB-RET-01", KB["KB-RET-01"], "passage") in units
    assert not any(pid == "KB-ORD-01" and scope == "passage" for pid, _, scope in units)


def test_f6_support_from_a_detached_sentence_needs_the_whole_passage():
    first = KB["KB-RET-01"].split(". ")[0] + "."
    claim = "Most items can be returned within 30 days of delivery."
    only_sentence = FixtureNLI(lambda p, h: ENTAIL if p == first else NEUTRAL)
    verdict = SemanticVerifier(only_sentence).verify("q", claim, evidence("KB-RET-01"))
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["detached"]
    whole_too = FixtureNLI(lambda p, h: ENTAIL if p in (first, KB["KB-RET-01"]) else NEUTRAL)
    assert SemanticVerifier(whole_too).verify("q", claim, evidence("KB-RET-01")).label == SUPPORTED


def test_v11_return_eligibility_conditions_do_not_restrict_refund_destination():
    policy = (
        "Most items can be returned within 30 days of delivery for a full refund to the original "
        "payment method. Items must be unused and in their original packaging."
    )
    claim = "Refunds for returned items go back to the original payment method."
    verdict = SemanticVerifier(
        scripted([(policy.split(". ")[0], "original payment method", ENTAIL)])
    ).verify("Where does the refund go?", claim, {"RET": policy})
    assert verdict.label == SUPPORTED and verdict.guards == []


def test_v11_relevant_return_eligibility_condition_still_triggers_detached_guard():
    policy = (
        "Most items can be returned within 30 days of delivery. "
        "Items must be unused and in their original packaging."
    )
    claim = "Most items can be returned within 30 days of delivery."
    first = policy.split(". ")[0] + "."
    verdict = SemanticVerifier(FixtureNLI(lambda p, h: ENTAIL if p == first else NEUTRAL)).verify(
        "Can I return this?", claim, {"RET": policy}
    )
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["detached"]


def test_v11_cross_passage_numbers_in_different_regions_do_not_conflict():
    claim = "Standard shipping to Canada takes 7 to 12 business days."
    canada = "Standard shipping to Canada takes 7 to 12 business days."
    us = "Standard shipping within the contiguous United States takes 3 to 7 business days."
    model = FixtureNLI(lambda p, h: ENTAIL if p == canada else NEUTRAL)
    verdict = SemanticVerifier(model, AggregationConfig(include_question=False)).verify(
        "How long?", claim, {"CA": canada, "US": us}
    )
    assert verdict.label == SUPPORTED and verdict.guards == []


def test_v11_cross_passage_numbers_in_the_same_region_still_conflict():
    claim = "Standard shipping to Canada takes 7 to 12 business days."
    support = "Standard shipping to Canada takes 7 to 12 business days."
    conflict = "Standard shipping to Canada takes 3 to 7 business days."
    model = FixtureNLI(lambda p, h: ENTAIL if p == support else NEUTRAL)
    verdict = SemanticVerifier(model, AggregationConfig(include_question=False)).verify(
        "How long?", claim, {"CA-1": support, "CA-2": conflict}
    )
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["conflict"]


def test_v11_different_service_condition_quantities_do_not_conflict():
    claim = "Standard shipping to Canada takes 7 to 12 business days."
    standard = "Standard shipping to Canada takes 7 to 12 business days."
    express = "Express shipping to Canada takes 3 to 7 business days."
    model = FixtureNLI(lambda p, h: ENTAIL if p == standard else NEUTRAL)
    verdict = SemanticVerifier(model, AggregationConfig(include_question=False)).verify(
        "How long?", claim, {"STD": standard, "EXP": express}
    )
    assert verdict.label == SUPPORTED and verdict.guards == []


def test_v11_unrelated_shared_actor_does_not_trigger_possible_exception():
    evidence = {
        "SUP": "Chat support is available every day. Support agents and the assistant cannot "
        "promise credits, discounts, or compensation."
    }
    claim = "Chat support is staffed only by human agents."
    verdict = SemanticVerifier(
        scripted_exact({}), AggregationConfig(include_question=False)
    ).verify("Is chat support run by people?", claim, evidence)
    assert verdict.label == INSUFFICIENT and verdict.guards == []


def test_v11_specific_opposite_policy_still_triggers_possible_exception():
    policy = "Rare maps cannot be borrowed."
    verdict = SemanticVerifier(
        scripted_exact({}), AggregationConfig(include_question=False)
    ).verify("Can I borrow one?", "Rare maps can be borrowed.", {"LIB": policy})
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["possible_exception"]


def test_f6_rule_and_exception_disagreeing_needs_human_review():
    v = SemanticVerifier(
        scripted([("Most items can be returned", "sofa", ENTAIL), ("Custom-made", "sofa", CONTRA)])
    )
    verdict = v.verify(
        "q", "Yes, you can return a custom-made sofa.", evidence("KB-RET-02", "KB-RET-01")
    )
    assert verdict.label == NEEDS_REVIEW
    assert {d["relation"] for d in verdict.deciding} == {"supports", "contradicts"}


def test_polarity_guard_never_lets_a_known_can_cannot_conflict_pass_as_supported():
    # Negation guard off so this exercises the separate can/cannot rules on their own.
    v = SemanticVerifier(
        scripted([("Custom-made furniture", "custom-made furniture", ENTAIL)]),
        AggregationConfig(negation_guard=False),
    )
    verdict = v.verify(
        "q", "Yes, custom-made furniture can be returned.", evidence("KB-RET-02", "KB-RET-01")
    )
    assert verdict.label == NEEDS_REVIEW and verdict.guards == ["polarity"]


# ------------------------------------------------------------------ F4 revision pinning


@pytest.mark.parametrize(
    "bad", ["main", "v1.0", "refs/pr/1", "abc1234", SHA.upper(), SHA[:-1], "", None]
)
def test_f4_mutable_or_ambiguous_revisions_are_rejected(bad):
    with pytest.raises(ValueError, match="immutable commit"):
        TransformersNLI("cross-encoder/nli-deberta-v3-small", revision=bad)


def test_f4_full_commit_sha_is_accepted():
    assert require_commit_sha(SHA) == SHA


REPO = "org/nli-model"
FULL_SNAPSHOT = {
    "config.json": b'{"id2label": {"0": "contradiction", "1": "entailment", "2": "neutral"}}',
    "model.safetensors": b"weights",
    "tokenizer_config.json": b"{}",
    "tokenizer.json": b"{}",
}


def make_cache(root, revision=SHA, files=FULL_SNAPSHOT, main=None):
    """The real hub cache layout: models--org--name/{blobs,snapshots/<sha>,refs/main},
    with snapshot files symlinked to content-addressed blobs."""
    import hashlib
    import os

    repo = root / ("models--" + REPO.replace("/", "--"))
    (repo / "blobs").mkdir(parents=True, exist_ok=True)
    snap = repo / "snapshots" / revision
    snap.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        blob = repo / "blobs" / hashlib.sha256(data).hexdigest()
        blob.write_bytes(data)
        os.symlink(os.path.relpath(blob, snap), snap / name)
    (repo / "refs").mkdir(exist_ok=True)
    (repo / "refs" / "main").write_text(main or revision)
    return snap


@pytest.fixture(autouse=True)
def _stub_huggingface_hub_when_absent(monkeypatch):
    """Provide offline cache lookup when optional huggingface_hub is not installed."""
    import importlib.util
    import os

    if importlib.util.find_spec("huggingface_hub") is not None:
        return

    def _try_to_load_from_cache(repo_id, filename, cache_dir=None, revision=None, repo_type=None):
        if not cache_dir:
            return None
        rev = revision or "main"
        repo_cache = os.path.join(cache_dir, f"models--{repo_id.replace('/', '--')}")
        if not os.path.isdir(repo_cache):
            return None
        ref_file = os.path.join(repo_cache, "refs", rev)
        if os.path.isfile(ref_file):
            with open(ref_file) as handle:
                rev = handle.read().strip()
        snapshots_dir = os.path.join(repo_cache, "snapshots")
        if not os.path.isdir(snapshots_dir) or rev not in os.listdir(snapshots_dir):
            return None
        cached_file = os.path.join(snapshots_dir, rev, filename)
        return cached_file if os.path.isfile(cached_file) else None

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        types.SimpleNamespace(try_to_load_from_cache=_try_to_load_from_cache),
    )


def test_f4_snapshot_files_are_verified_and_recorded(tmp_path):
    snap = make_cache(tmp_path)
    record = verify_cached_snapshot(REPO, SHA, cache_dir=tmp_path)
    assert record["commit"] == SHA and record["snapshot_dir"] == str(snap)
    assert set(record["files"]) == {
        "config.json",
        "model.safetensors",
        "tokenizer_config.json",
        "tokenizer.json",
    }
    import hashlib

    blob = hashlib.sha256(FULL_SNAPSHOT["model.safetensors"]).hexdigest()
    assert record["files"]["model.safetensors"]["blob"] == blob  # content-addressed provenance
    assert (
        record["files"]["config.json"]["sha256"]
        == hashlib.sha256(FULL_SNAPSHOT["config.json"]).hexdigest()
    )


def test_f4_unknown_commit_fails_closed_even_if_a_branch_points_elsewhere(tmp_path):
    other = "f" * 40
    make_cache(tmp_path, revision=other, main=other)  # refs/main -> other commit
    with pytest.raises(SemanticBackendUnavailable, match="not in the local cache"):
        verify_cached_snapshot(REPO, SHA, cache_dir=tmp_path)


@pytest.mark.parametrize(
    "drop, message",
    [("model.safetensors", "no model weights"), ("tokenizer.json", "tokenizer files missing"),
     ("tokenizer_config.json", "tokenizer files missing")],
)  # fmt: skip
def test_f4_incomplete_snapshot_fails_closed(tmp_path, drop, message):
    make_cache(tmp_path, files={k: v for k, v in FULL_SNAPSHOT.items() if k != drop})
    with pytest.raises(SemanticBackendUnavailable, match=message):
        verify_cached_snapshot(REPO, SHA, cache_dir=tmp_path)


def _fake_ml(monkeypatch):
    """Fake torch/transformers that, like transformers 5.x, set no _commit_hash anywhere."""

    class Tensor:
        def __init__(self, rows):
            self.rows = rows

        def tolist(self):
            return self.rows

    class NoGrad:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    torch = types.SimpleNamespace(
        manual_seed=lambda s: None, no_grad=NoGrad, softmax=lambda t, dim: t, __version__="fake"
    )

    class Model:
        config = types.SimpleNamespace(id2label={0: "contradiction", 1: "entailment", 2: "neutral"})

        def eval(self):
            return self

        def __call__(self, **enc):
            return types.SimpleNamespace(logits=Tensor([[0.1, 0.8, 0.1]] * enc["n"]))

    class Tokenizer:
        def __call__(self, premises, hypotheses, **kw):
            return {"n": len(premises)}

    calls = []
    auto_tok = types.SimpleNamespace(
        from_pretrained=lambda path, **kw: calls.append((path, kw)) or Tokenizer()
    )
    auto_model = types.SimpleNamespace(
        from_pretrained=lambda path, **kw: calls.append((path, kw)) or Model()
    )
    transformers = types.SimpleNamespace(
        AutoTokenizer=auto_tok, AutoModelForSequenceClassification=auto_model, __version__="fake"
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    return calls


def test_f4_model_and_tokenizer_load_from_the_verified_snapshot_directory(tmp_path, monkeypatch):
    snap = make_cache(tmp_path)
    calls = _fake_ml(monkeypatch)
    backend = TransformersNLI(REPO, revision=SHA, cache_dir=tmp_path)
    assert backend.score([("p", "h")]) == [
        {"contradiction": 0.1, "entailment": 0.8, "neutral": 0.1}
    ]
    assert calls == [(str(snap), {"local_files_only": True})] * 2  # exactly the verified files
    info = backend.describe()
    assert info["revision"] == info["resolved_snapshot_commit"] == SHA
    assert "model.safetensors" in info["snapshot_files"]


def test_f4_unverifiable_revision_never_reaches_the_loader(tmp_path, monkeypatch):
    calls = _fake_ml(monkeypatch)
    with pytest.raises(SemanticBackendUnavailable, match="not in the local cache"):
        TransformersNLI(REPO, revision=SHA, cache_dir=tmp_path).load()
    assert calls == []


def test_transformers_backend_explains_missing_torch_without_downloading(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def no_torch(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("No module named 'torch'")
        return real_import(name, *args, **kwargs)

    make_cache(tmp_path)
    monkeypatch.setattr(builtins, "__import__", no_torch)
    with pytest.raises(SemanticBackendUnavailable, match="torch is not installed"):
        TransformersNLI(REPO, revision=SHA, cache_dir=tmp_path).load()


def test_importing_the_package_loads_no_ml_framework():
    code = (
        "import sys, evalops.semantic.verifier, evalops.semantic.nli;"
        "print(any(m in sys.modules for m in ('torch', 'transformers', 'mlx')))"
    )
    backend = Path(__file__).resolve().parents[2] / "backend"
    out = subprocess.run([sys.executable, "-c", code], cwd=backend, capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


# ------------------------------------------------------------------ approval invariant sweep


@pytest.mark.parametrize("claim, ev", SWEEP_CASES)
def test_v8_exhaustive_sweep_approval_invariant(claim, ev):
    """Every assignment of entail / contradict / neutral to every evidence unit:
    screened contradictions don't veto independent support; without entailment, nothing supports.
    Insufficient evidence is allowed when every contradiction is screened as irrelevant or
    condition-mismatched."""
    import itertools

    units = [u for _, u, _ in evidence_units(ev)]
    checked = 0
    for combo in itertools.product((ENTAIL, CONTRA, NEUTRAL), repeat=len(units)):
        table = dict(zip(units, combo, strict=True))
        model = FixtureNLI(lambda p, h, t=table: t.get(p, NEUTRAL))
        verdict = SemanticVerifier(model, NO_Q).verify("q", claim, ev)
        checked += 1
        # Contradictions screened as unrelated, complementary, or different-concept no longer
        # veto independent support. A kept contradiction still cannot silently coexist with it.
        if verdict.label == SUPPORTED:
            assert not any(d["relation"] == "contradicts" for d in verdict.deciding), (claim, combo)
        if ENTAIL not in combo:
            assert verdict.label != SUPPORTED, (claim, combo)
            if CONTRA in combo and verdict.label == INSUFFICIENT:
                kinds = {i["kind"] for i in verdict.ignored_contradictions}
                assert kinds <= {"unrelated", "complementary", "different_concept"}, (
                    claim,
                    combo,
                    kinds,
                )
    assert checked == 3 ** len(units)
