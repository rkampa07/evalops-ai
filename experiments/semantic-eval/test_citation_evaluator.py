"""Synthetic-only tests for citation-evaluator-v4; no benchmark rows or model calls."""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from citation_evaluator import (  # noqa: E402
    CitationInput,
    CitationStatus as S,
    _components,
    predict_citation_quality,
)


def predict(claim, cited, retrieved, *, question=None, kb=None, expected=True):
    return predict_citation_quality(
        CitationInput(
            claim_text=claim,
            citation_ids=tuple(cited),
            retrieved_passages=retrieved,
            knowledge_base_ids=frozenset(kb if kb is not None else set(retrieved) | set(cited)),
            question_text=question,
            citation_expected=expected,
        )
    )


def test_correct_means_appropriate_evidence_even_when_it_contradicts_claim():
    result = predict(
        "Custom furniture can be returned.", ["P1"],
        {"P1": "Custom furniture cannot be returned."},
    )
    assert result.status == S.CORRECT
    assert result.per_citation[0].reason_code == "claim_relevance_sufficient"


def test_wrong_passage_when_related_citation_is_outmatched_by_retrieved_rule():
    result = predict(
        "Canadian orders arrive in 7 to 12 business days.", ["P_US"],
        {
            "P_US": "US standard shipping takes 3 to 7 business days.",
            "P_CA": "Canadian orders usually arrive in 7 to 12 business days.",
        },
    )
    assert result.status == S.WRONG_PASSAGE
    assert result.per_citation[0].reason_code == "specific_retrieved_passage_matches"


def test_unrelated_when_cited_passage_has_no_claim_term_overlap():
    result = predict(
        "Rewards members receive free delivery.", ["P"],
        {"P": "Furniture has a two-year limited warranty for manufacturing defects."},
    )
    assert result.status == S.UNRELATED
    assert result.per_citation[0].reason_code == "no_claim_term_overlap"


def test_partial_for_compound_claim_with_one_uncovered_part():
    result = predict(
        "Refund arrives within five days and an email confirmation is sent.", ["P"],
        {"P": "Refund arrives within five business days after the return reaches our warehouse."},
    )
    assert result.status == S.PARTIAL
    assert result.per_citation[0].reason_code == "compound_claim_partial_coverage"


def test_known_but_unretrieved_precedes_relevance_scoring():
    result = predict("A claim with several useful terms.", ["P_OLD"], {}, kb={"P_OLD"})
    assert result.status == S.NOT_RETRIEVED
    assert result.per_citation[0].reason_code == "known_passage_not_retrieved"


def test_missing_citation_and_not_applicable_are_distinct():
    missing = predict("Claim text", [], {}, expected=True)
    n_a = predict("Claim text", [], {}, expected=False)
    assert missing.status == S.MISSING_CITATION
    assert n_a.status == S.NOT_APPLICABLE


def test_invalid_id_is_separate_from_not_retrieved():
    result = predict("A claim with several useful terms.", ["P_UNKNOWN"], {}, kb=set())
    assert result.status == S.INVALID_CITATION
    assert result.per_citation[0].reason_code == "unknown_passage_id"


def test_invalid_id_precedes_other_citations_in_aggregate_but_keeps_each_result():
    result = predict(
        "Returns are accepted within 30 days.", ["P_GOOD", "P_UNKNOWN"],
        {"P_GOOD": "Returns are accepted within 30 days."}, kb={"P_GOOD"},
    )
    assert result.status == S.INVALID_CITATION
    assert [decision.status for decision in result.per_citation] == [
        S.CORRECT, S.INVALID_CITATION,
    ]


def test_multiple_citations_are_each_reported_and_union_covers_compound_claim():
    result = predict(
        "Refunds return to the original payment method and the refunds are issued within five business days.",
        ["P_DEST", "P_TIME"],
        {
            "P_DEST": "Refunds return to the original payment method.",
            "P_TIME": "Refunds are issued within five business days.",
        },
    )
    assert result.status == S.CORRECT
    assert len(result.per_citation) == 2
    assert [d.citation_id for d in result.per_citation] == ["P_DEST", "P_TIME"]
    assert all(d.status == S.PARTIAL for d in result.per_citation)


def test_question_context_only_resolves_pronouns_not_claim_facts():
    resolved = predict(
        "It arrives in 7 days.", ["P"], {"P": "Shipping usually arrives in 7 days."},
        question="How long does shipping take?",
    )
    no_pronoun = predict(
        "Rewards members get free shipping.", ["P"],
        {"P": "Furniture has a limited warranty for manufacturing defects."},
        question="Can members get free shipping?",
    )
    assert resolved.status == S.CORRECT
    assert no_pronoun.status == S.UNRELATED


def test_ambiguous_weak_overlap_abstains():
    result = predict(
        "The delivery option may have a regional service condition.", ["P"],
        {"P": "Delivery windows vary by location."},
    )
    assert result.status == S.UNRESOLVED
    assert result.per_citation[0].reason_code == "relevance_ambiguous"


def test_coordinated_nouns_are_not_split_but_independent_and_clause_is():
    coordinated = _components(
        "Duties and taxes for overseas orders are collected during checkout."
    )
    independent = _components(
        "A parcel arrived damaged, and you can request a replacement."
    )
    assert len(coordinated) == 1
    assert len(independent) == 2


def test_independent_proposition_can_be_uncovered_without_splitting_a_noun_list():
    result = predict(
        "A parcel arrived damaged, and you can request an inspection.",
        ["P"],
        {"P": "A parcel arrived damaged."},
    )
    assert result.status == S.PARTIAL


def test_relevant_contradictory_evidence_and_different_quantity_remain_correct_citations():
    result = predict(
        "Return shipping costs $12.95 for small orders.",
        ["P"],
        {"P": "Return shipping is free over $75; smaller orders pay a $9.95 fee."},
    )
    assert result.status == S.CORRECT


def test_narrower_and_broader_policy_scope_are_partial():
    narrower = predict(
        "Items marked final sale cannot be returned.",
        ["P"],
        {"P": "Clearance items marked final sale cannot be returned."},
    )
    broader = predict(
        "A sofa can be returned.",
        ["P"],
        {"P": "Most items can be returned within 30 days."},
    )
    assert narrower.status == S.PARTIAL
    assert broader.status == S.PARTIAL


def test_specific_missing_facet_abstains_instead_of_using_topic_overlap():
    assembly = predict(
        "Premium room delivery includes complete assembly.",
        ["P"],
        {"P": "Premium room delivery includes placement and packaging removal."},
    )
    staffing = predict(
        "Chat service is staffed by trained agents.",
        ["P"],
        {"P": "Chat service is available weekdays from 8 am to 5 pm."},
    )
    assert assembly.status == S.UNRESOLVED
    assert staffing.status == S.UNRESOLVED
    assert assembly.per_citation[0].reason_code == "asserted_facet_not_addressed"
    assert staffing.per_citation[0].reason_code == "asserted_facet_not_addressed"


def test_related_but_less_specific_citation_is_wrong_passage_not_unrelated():
    result = predict(
        "Separately purchased amber bulbs are covered for 60 days under the lighting warranty.",
        ["P_GENERAL"],
        {
            "P_GENERAL": "Furniture has a two-year warranty for manufacturing defects.",
            "P_SPECIFIC": "Separately purchased amber bulbs are covered for 60 days.",
        },
    )
    assert result.status == S.WRONG_PASSAGE
    assert result.per_citation[0].reason_code == "specific_retrieved_passage_matches"


def test_unrelated_citation_stays_unrelated_despite_strong_retrieved_alternative():
    result = predict(
        "Rewards members receive free delivery.",
        ["P_WARRANTY"],
        {
            "P_WARRANTY": "Furniture has a two-year warranty for manufacturing defects.",
            "P_DELIVERY": "Rewards members receive free delivery on qualifying orders.",
        },
    )
    assert result.status == S.UNRELATED
    assert result.per_citation[0].reason_code == "no_claim_term_overlap"


def test_weak_generic_topic_overlap_does_not_establish_wrong_passage():
    result = predict(
        "Priority handling is available for orders.",
        ["P_TRACKING"],
        {
            "P_TRACKING": "Orders can be tracked in your account.",
            "P_PRIORITY": "Priority handling is available for qualifying orders.",
        },
    )
    assert result.status == S.UNRESOLVED
    assert result.per_citation[0].reason_code == "relevance_ambiguous"


def test_strong_citation_remains_correct_despite_lexically_closer_alternative():
    result = predict(
        "Canadian orders arrive in 7 to 12 business days.",
        ["P_CANADA"],
        {
            "P_CANADA": "Canadian orders usually arrive in 7 to 12 business days.",
            "P_EXACT": "Canadian orders arrive in 7 to 12 business days for standard shipping.",
        },
    )
    assert result.status == S.CORRECT
    assert result.per_citation[0].reason_code == "claim_relevance_sufficient"


def test_single_claim_multiple_citations_keep_individual_results_and_aggregate_correct():
    result = predict(
        "Returns are accepted within 30 days.",
        ["P_RETURNS", "P_SHIPPING"],
        {
            "P_RETURNS": "Returns are accepted within 30 days of delivery.",
            "P_SHIPPING": "Standard shipping is free on orders over $49.",
        },
    )
    assert result.status == S.CORRECT
    assert [item.status for item in result.per_citation] == [S.CORRECT, S.UNRELATED]
    assert [item.citation_id for item in result.per_citation] == ["P_RETURNS", "P_SHIPPING"]


def test_material_condition_geography_mismatch_uses_the_matching_retrieved_passage():
    result = predict(
        "Orders to Canada usually arrive in 7 to 12 business days.",
        ["P_US"],
        {
            "P_US": "Orders to the United States usually arrive in 3 to 7 business days.",
            "P_CA": "Orders to Canada usually arrive in 7 to 12 business days.",
        },
    )
    assert result.status == S.WRONG_PASSAGE
    assert result.per_citation[0].reason_code == "specific_retrieved_passage_matches"


def test_different_value_for_same_geographic_condition_is_still_correct_evidence():
    result = predict(
        "Orders to Canada arrive in 3 to 7 business days.",
        ["P_CA"],
        {"P_CA": "Orders to Canada usually arrive in 7 to 12 business days."},
    )
    assert result.status == S.CORRECT
    assert result.per_citation[0].reason_code == "claim_relevance_sufficient"


def test_different_eligibility_threshold_is_a_material_condition_mismatch():
    result = predict(
        "Orders under $75 are eligible for free return shipping.",
        ["P_OVER"],
        {
            "P_OVER": "Orders over $75 are eligible for free return shipping.",
            "P_UNDER": "Orders under $75 are eligible for free return shipping.",
        },
    )
    assert result.status == S.WRONG_PASSAGE
    assert result.per_citation[0].reason_code == "specific_retrieved_passage_matches"


def test_different_explicit_policy_exception_is_a_material_condition_mismatch():
    result = predict(
        "Returns are accepted for clearance items.",
        ["P_FULL_PRICE"],
        {
            "P_FULL_PRICE": "Returns are accepted for full-price items.",
            "P_CLEARANCE": "Returns are accepted for clearance items.",
        },
    )
    assert result.status == S.WRONG_PASSAGE
    assert result.per_citation[0].reason_code == "specific_retrieved_passage_matches"


def test_different_product_category_is_a_material_condition_mismatch():
    result = predict(
        "Tablets are covered for two years under the device warranty.",
        ["P_PHONES"],
        {
            "P_PHONES": "Phones are covered for two years under the device warranty.",
            "P_TABLETS": "Tablets are covered for two years under the device warranty.",
        },
    )
    assert result.status == S.WRONG_PASSAGE
    assert result.per_citation[0].reason_code == "specific_retrieved_passage_matches"


def test_shared_topic_without_a_specific_claim_facet_does_not_become_correct():
    result = predict(
        "Premium support includes phone access.",
        ["P"],
        {"P": "Premium support operates weekdays from 8 am to 5 pm."},
    )
    assert result.status == S.UNRESOLVED


def test_predictor_input_has_no_claim_label_or_gold_decision_fields():
    fields = set(CitationInput.__dataclass_fields__)
    assert fields == {
        "claim_text", "citation_ids", "retrieved_passages", "knowledge_base_ids",
        "question_text", "citation_expected",
    }
    result = predict("Returns are accepted within 30 days.", ["P"],
                     {"P": "Returns are accepted within 30 days."})
    assert result.status == S.CORRECT
