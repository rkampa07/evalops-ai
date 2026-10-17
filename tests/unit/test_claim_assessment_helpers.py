import pytest

from evalops.claim_assessments import (
    AGGREGATION_VERSION,
    MAX_CLAIMS,
    build_claim_inputs,
    claim_output,
    config_digest,
    source_sha256,
)
from evalops.engine.versioning import knowledge_base_hash
from evalops.errors import (
    InconsistentRetrievalError,
    MissingRetrievalMetadataError,
    StaleKnowledgeBaseError,
    UnprocessableError,
)
from evalops.models import EvaluationResult, EvaluationRun
from evalops.semantic.aggregate import AggregationConfig
from evalops.semantic.verdict import SemanticVerdict


def _objects(retrieved_ids):
    passages = {"KB-RET-01": "Refunds go to the original payment method."}
    run = EvaluationRun(
        id=1,
        dataset_id=1,
        workspace_id=8,
        knowledge_base_hash=knowledge_base_hash(passages),
    )
    result = EvaluationResult(
        id=2,
        run_id=1,
        case_key="refund",
        output="Refunds go to the original payment method [KB-RET-01].",
        passed=False,
        metrics={"private metric": "do not expose"},
        failures=[{"expected": "private reference"}],
        retrieved_passage_ids=retrieved_ids,
        case_snapshot={
            "input": "Where does a refund go?",
            "expected_output": "private gold answer",
            "relevant_passage_ids": ["gold-only-id"],
        },
        model_grades={"grade": "private grade"},
    )
    return run, result, passages


def test_v12_configuration_digest_and_source_provenance_are_stable():
    assert AGGREGATION_VERSION == "semantic-aggregate-v12"
    assert config_digest() == AggregationConfig().digest() == "92aacfdf47908332"
    assert len(source_sha256()) == 64


def test_claim_input_allowlist_splits_claims_and_excludes_gold_and_lexical_data():
    run, result, passages = _objects(["KB-RET-01"])
    question, claims, evidence, fingerprint, kb_hash = build_claim_inputs(run, result, passages)
    assert question == "Where does a refund go?"
    assert claims == [{"text": "Refunds go to the original payment method."}]
    assert evidence == passages
    assert len(fingerprint) == len(kb_hash) == 64
    assert "private gold answer" not in repr((question, claims, evidence))
    assert "private metric" not in repr((question, claims, evidence))
    assert "private grade" not in repr((question, claims, evidence))
    assert "gold-only-id" not in repr((question, claims, evidence))


def test_null_retrieval_fails_but_explicit_empty_retrieval_is_preserved():
    run, result, passages = _objects(None)
    with pytest.raises(MissingRetrievalMetadataError):
        build_claim_inputs(run, result, passages)
    run, result, passages = _objects([])
    question, claims, evidence, fingerprint, _ = build_claim_inputs(run, result, passages)
    assert question and claims and evidence == {}
    assert len(fingerprint) == 64


def test_stale_kb_and_inconsistent_retrieval_fail_closed():
    run, result, passages = _objects(["KB-RET-01"])
    with pytest.raises(StaleKnowledgeBaseError):
        build_claim_inputs(run, result, {"KB-RET-01": "changed policy"})
    other = {"KB-OTHER-01": "A different policy passage."}
    run.knowledge_base_hash = knowledge_base_hash(other)
    with pytest.raises(InconsistentRetrievalError):
        build_claim_inputs(run, result, other)


def test_claim_count_and_input_lengths_are_bounded():
    run, result, passages = _objects([])
    result.output = " ".join(
        f"Policy option number {index} applies." for index in range(MAX_CLAIMS + 1)
    )
    with pytest.raises(UnprocessableError, match="more than"):
        build_claim_inputs(run, result, passages)
    result.output = "A" * 20_001
    with pytest.raises(UnprocessableError, match="response"):
        build_claim_inputs(run, result, passages)


@pytest.mark.parametrize(
    ("label", "abstained"),
    [
        ("supported", False),
        ("contradicted", False),
        ("insufficient_evidence", False),
        ("needs_human_review", True),
    ],
)
def test_claim_output_preserves_each_label_and_abstention(label, abstained):
    verdict = SemanticVerdict(
        label=label,
        reason="synthetic reason",
        deciding=[{"passage_id": "KB-RET-01", "relation": "support", "unit": "evidence"}],
        guards=["synthetic_branch"],
        model={"backend": "fixture"},
        config={"question_pass": "not needed"},
    )
    payload = claim_output("synthetic claim", verdict)
    assert payload["claim_text"] == "synthetic claim"
    assert payload["label"] == label
    assert payload["abstained"] is abstained
    assert payload["diagnostic_branch"] == "synthetic_branch"
    assert payload["deciding_evidence"][0]["passage_id"] == "KB-RET-01"
