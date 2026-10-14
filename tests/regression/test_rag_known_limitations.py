"""KNOWN LIMITATION: reproduce a semantic contradiction that passes lexical scoring.

This pins an observed false positive, not a correctness requirement. If semantic checks are
introduced, review and update or retire this example; do not retain the incorrect pass merely
to keep this test green. No model inference, API writes, or fixture modifications are needed.
"""

import json
from pathlib import Path

import pytest

from evalops.engine.scoring import CaseSpec, EvalConfig, ResponseSpec, score_case

RAG = Path(__file__).resolve().parents[2] / "examples/rag"
CASE_KEY = "q-p2-custom-sofa-return"


def test_known_limitation_custom_sofa_contradiction_passes_lexical_scoring():
    """KNOWN LIMITATION: a cited general rule masks an explicit non-returnable exception."""
    dataset = json.loads((RAG / "prospective/kestrel_prospective_v2.json").read_text())
    recording = json.loads(
        (RAG / "prospective/responses/qwen2.5-1.5b_rag-v5_prospective_v2.json").read_text()
    )
    case = next(c for c in dataset["test_cases"] if c["key"] == CASE_KEY)
    response = next(r for r in recording["responses"] if r["case_key"] == CASE_KEY)
    retrieved_ids = response["retrieved_passage_ids"]
    evidence = {
        p["passage_id"]: p["text"] for p in dataset["passages"] if p["passage_id"] in retrieved_ids
    }

    assert case["input"] == "Can I return a custom-made sofa?"
    assert case["relevant_passage_ids"] == ["KB-RET-02"]
    assert retrieved_ids[0] == "KB-RET-02"
    # Expected policy answer is NO: the specific exclusion overrides the general window.
    # The fixture has no reference-answer or term check that would catch this contradiction.
    assert "Custom-made furniture" in evidence["KB-RET-02"]
    assert "cannot be returned" in evidence["KB-RET-02"]
    assert response["output"] == (
        "Yes, you can return a custom-made sofa within 30 days of delivery for a full refund "
        "to the original payment method [KB-RET-01]."
    )

    fields = CaseSpec.__dataclass_fields__
    result = score_case(
        CaseSpec(**{key: value for key, value in case.items() if key in fields}),
        ResponseSpec(
            output=response["output"],
            latency_ms=response.get("latency_ms"),
            retrieved_passage_ids=retrieved_ids,
        ),
        EvalConfig(**recording["config"]),
        knowledge_base=evidence,
    )

    # These assertions demonstrate the false pass; they do NOT endorse the model answer.
    assert result.passed is True
    assert result.failures == []
    assert result.metrics["retrieval_recall"] == 1.0
    assert result.metrics["citations_valid"] == result.metrics["citations_total"] == 1
    assert result.metrics["groundedness"] == 1.0
    assert result.metrics["claims_total"] == 1
    claim = result.metrics["claims"][0]
    assert claim["supported"] is True
    assert claim["passage_id"] == "KB-RET-01"
    assert claim["coverage"] == pytest.approx(2 / 3, abs=1e-6)
    assert claim["missing_numbers"] == []
