"""Allowlisted input construction and provenance for frozen citation-evaluator-v4."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .citation_evaluator_v4 import (
    VERSION,
    CitationInput,
    CitationPrediction,
    predict_citation_quality,
)
from .engine.rag import _strip_citations, parse_citations
from .engine.versioning import knowledge_base_hash
from .errors import (
    InconsistentRetrievalError,
    MissingRetrievalMetadataError,
    StaleKnowledgeBaseError,
    UnprocessableError,
)
from .models import EvaluationResult, EvaluationRun

FROZEN_PREDICTOR_SHA256 = "33ee2d9f39258b5c7f8f6c53fa82b95730a880e500de819cb7aa262d0c51d84b"
INPUT_CONTRACT = (
    "claim_text",
    "citation_ids",
    "retrieved_passages",
    "knowledge_base_ids",
    "question_text",
    "citation_expected",
)
FROZEN_SOURCE = (
    Path(__file__).resolve().parents[2] / "experiments/semantic-eval/citation_evaluator.py"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_frozen_predictor() -> str:
    """Fail closed if the importable packaged copy differs from the reviewed frozen source."""
    vendored = Path(__file__).with_name("citation_evaluator_v4.py")
    digest = sha256_file(vendored)
    if digest != FROZEN_PREDICTOR_SHA256:
        raise RuntimeError("frozen citation predictor source hash mismatch")
    if FROZEN_SOURCE.exists() and sha256_file(FROZEN_SOURCE) != digest:
        raise RuntimeError("packaged citation predictor differs from frozen source")
    return digest


def runner_sha256() -> str:
    """Hash the input adapter and worker that invoke the unchanged predictor."""
    root = Path(__file__).resolve().parent
    entries = []
    for relative in (
        "citation_assessments.py",
        "workers/citation_assessment.py",
        "services/citation_assessments.py",
        "engine/rag.py",
    ):
        path = root / relative
        entries.append((relative, sha256_file(path)))
    return canonical_hash(entries)


def config_provenance() -> tuple[str, str, str]:
    predictor_hash = verify_frozen_predictor()
    runner_hash = runner_sha256()
    config_digest = canonical_hash(
        {
            "evaluator": VERSION,
            "predictor_sha256": predictor_hash,
            "runner_sha256": runner_hash,
            "input_contract": INPUT_CONTRACT,
            "non_applicable_policy": "exclude from five-class accuracy",
            "special_status_policy": "report separately; never coerce to a scored class",
            "multiple_citations": "frozen predictor returns aggregate and per-citation decisions",
        }
    )
    return predictor_hash, runner_hash, config_digest


def build_citation_input(
    run: EvaluationRun,
    result: EvaluationResult,
    passages: Mapping[str, str],
    *,
    citation_expected: bool,
) -> tuple[CitationInput, str, str | None]:
    """Build predictor input only from saved response/question/retrieval and verified KB text."""
    snapshot = result.case_snapshot
    question = snapshot.get("input") if isinstance(snapshot, dict) else None
    if result.output is None:
        raise UnprocessableError("The saved result has no response text for assessment.")

    citations = tuple(parse_citations(result.output))
    claim_text = _strip_citations(result.output)
    if not citations:
        # The frozen predictor resolves these special statuses before reading evidence. Do not
        # turn a missing/old retrieval snapshot into a false five-class result when no citation
        # was supplied; an expected-but-absent citation is still missing, and an unrequired one
        # is still not applicable.
        predictor_input = CitationInput(
            claim_text=claim_text,
            citation_ids=(),
            retrieved_passages={},
            knowledge_base_ids=frozenset(),
            question_text=question if isinstance(question, str) else None,
            citation_expected=citation_expected,
        )
        fingerprint = canonical_hash(
            {
                "claim_text": predictor_input.claim_text,
                "question_text": predictor_input.question_text,
                "citation_ids": [],
                "retrieved_passages": {},
                "knowledge_base_ids": [],
                "citation_expected": predictor_input.citation_expected,
                "knowledge_base_hash": None,
            }
        )
        return predictor_input, fingerprint, run.knowledge_base_hash

    if result.retrieved_passage_ids is None:
        raise MissingRetrievalMetadataError(
            "This saved result has no recorded retrieval metadata and cannot be assessed."
        )
    current_kb_hash = knowledge_base_hash(passages)
    if run.knowledge_base_hash != current_kb_hash:
        raise StaleKnowledgeBaseError(
            "The saved run knowledge base differs from the current dataset knowledge base."
        )
    if not isinstance(question, str):
        question = None

    registry = frozenset(passages)
    retrieved_ids = tuple(result.retrieved_passage_ids)
    inconsistent = set(retrieved_ids) - registry
    if inconsistent:
        raise InconsistentRetrievalError(
            "Recorded retrieval references do not match the verified knowledge base."
        )
    retrieved = {passage_id: passages[passage_id] for passage_id in retrieved_ids}
    predictor_input = CitationInput(
        claim_text=claim_text,
        citation_ids=citations,
        retrieved_passages=retrieved,
        knowledge_base_ids=registry,
        question_text=question,
        citation_expected=citation_expected,
    )
    fingerprint = canonical_hash(
        {
            "claim_text": predictor_input.claim_text,
            "question_text": predictor_input.question_text,
            "citation_ids": list(predictor_input.citation_ids),
            "retrieved_passages": dict(predictor_input.retrieved_passages),
            "knowledge_base_ids": sorted(predictor_input.knowledge_base_ids),
            "citation_expected": predictor_input.citation_expected,
            "knowledge_base_hash": current_kb_hash,
        }
    )
    return predictor_input, fingerprint, current_kb_hash


def prediction_payload(prediction: CitationPrediction) -> dict[str, Any]:
    return {
        "version": prediction.version,
        "status": str(prediction.status),
        "reason_code": prediction.reason_code,
        "reason": prediction.reason,
        "per_citation": [
            {
                "citation_id": item.citation_id,
                "status": str(item.status),
                "reason_code": item.reason_code,
                "reason": item.reason,
                "component_coverage": list(item.component_coverage),
            }
            for item in prediction.per_citation
        ],
    }


def predict_citation(inputs: CitationInput) -> CitationPrediction:
    verify_frozen_predictor()
    return predict_citation_quality(inputs)
