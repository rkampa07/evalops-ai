from dataclasses import fields

import pytest

from evalops.citation_assessments import (
    FROZEN_PREDICTOR_SHA256,
    FROZEN_SOURCE,
    build_citation_input,
    verify_frozen_predictor,
)
from evalops.citation_evaluator_v4 import CitationInput, CitationStatus, predict_citation_quality
from evalops.engine.versioning import knowledge_base_hash
from evalops.errors import (
    InconsistentRetrievalError,
    MissingRetrievalMetadataError,
    StaleKnowledgeBaseError,
)
from evalops.models import EvaluationResult, EvaluationRun


def _objects(retrieved_ids):
    passages = {"KB-RET-01": "Refunds are issued to the original payment method."}
    run = EvaluationRun(
        id=1, dataset_id=1, workspace_id=7, knowledge_base_hash=knowledge_base_hash(passages)
    )
    result = EvaluationResult(
        id=2,
        run_id=1,
        case_key="refund",
        output="Refunds go to the original payment method [KB-RET-01].",
        passed=False,
        metrics={"claim_gold": "secret metric"},
        failures=[{"message": "secret failure"}],
        retrieved_passage_ids=retrieved_ids,
        case_snapshot={
            "input": "Where does the refund go?",
            "expected_output": "private reference",
        },
        model_grades={"verdict": "private grade"},
    )
    return run, result, passages


def test_frozen_predictor_copy_matches_approved_source_hash():
    assert verify_frozen_predictor() == FROZEN_PREDICTOR_SHA256
    assert FROZEN_SOURCE.exists()


def test_predictor_input_is_allowlisted_and_excludes_reference_and_lexical_fields():
    run, result, passages = _objects(["KB-RET-01"])
    inputs, _fingerprint, _kb_hash = build_citation_input(
        run, result, passages, citation_expected=True
    )
    assert {item.name for item in fields(CitationInput)} == {
        "claim_text",
        "citation_ids",
        "retrieved_passages",
        "knowledge_base_ids",
        "question_text",
        "citation_expected",
    }
    assert inputs.claim_text == "Refunds go to the original payment method."
    assert inputs.question_text == "Where does the refund go?"
    assert inputs.citation_ids == ("KB-RET-01",)
    assert "private reference" not in repr(inputs)
    assert "secret metric" not in repr(inputs)
    assert "private grade" not in repr(inputs)


def test_missing_retrieval_metadata_is_distinct_from_explicit_empty():
    run, result, passages = _objects(None)
    with pytest.raises(MissingRetrievalMetadataError):
        build_citation_input(run, result, passages, citation_expected=True)

    run, result, passages = _objects([])
    inputs, _, _ = build_citation_input(run, result, passages, citation_expected=True)
    assert inputs.retrieved_passages == {}
    assert predict_citation_quality(inputs).status == "not_retrieved"


def test_stale_kb_and_inconsistent_recorded_retrieval_fail_closed():
    run, result, passages = _objects(["KB-RET-01"])
    with pytest.raises(StaleKnowledgeBaseError):
        build_citation_input(run, result, {"KB-RET-01": "changed evidence"}, citation_expected=True)
    other_passages = {"KB-OTHER-01": "Other policy text."}
    run.knowledge_base_hash = knowledge_base_hash(other_passages)
    with pytest.raises(InconsistentRetrievalError):
        build_citation_input(run, result, other_passages, citation_expected=True)


def test_unresolved_is_preserved_as_an_abstention():
    prediction = predict_citation_quality(
        CitationInput(
            claim_text="The delivery option may have a regional service condition.",
            citation_ids=("KB-RET-01",),
            retrieved_passages={"KB-RET-01": "Delivery windows vary by location."},
            knowledge_base_ids=frozenset({"KB-RET-01"}),
        )
    )
    assert prediction.status == CitationStatus.UNRESOLVED
    assert prediction.reason_code == "citation_set_ambiguous"
    assert prediction.per_citation[0].reason_code == "relevance_ambiguous"


@pytest.mark.parametrize(
    ("citation_ids", "expected", "status"),
    [
        ((), False, "not_applicable"),
        ((), True, "missing_citation"),
        (("KB-UNKNOWN-01",), True, "invalid_citation"),
    ],
)
def test_special_statuses_remain_outside_the_five_scored_classes(citation_ids, expected, status):
    prediction = predict_citation_quality(
        CitationInput(
            claim_text="Refunds use the original payment method.",
            citation_ids=citation_ids,
            retrieved_passages={},
            knowledge_base_ids=frozenset(),
            citation_expected=expected,
        )
    )
    assert prediction.status == status
