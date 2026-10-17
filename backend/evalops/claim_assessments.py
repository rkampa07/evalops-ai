"""Allowlisted input and frozen configuration provenance for semantic-aggregate-v12."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .engine.rag import split_claims
from .engine.versioning import knowledge_base_hash
from .errors import (
    InconsistentRetrievalError,
    MissingRetrievalMetadataError,
    StaleKnowledgeBaseError,
    UnprocessableError,
)
from .models import EvaluationResult, EvaluationRun
from .semantic.aggregate import AGGREGATION_VERSION, AggregationConfig

MODEL_ID = "cross-encoder/nli-deberta-v3-small"
MODEL_REVISION = "fa2804872c3b4bd748f38c0185cc85775361e735"
MAX_CLAIMS = 10
MAX_RESPONSE_CHARS = 20_000
MAX_QUESTION_CHARS = 20_000
MAX_EVIDENCE_CHARS = 20_000
MAX_RETRIEVED_PASSAGES = 50
MAX_CLAIM_CHARS = 4_000
INPUT_CONTRACT = (
    "response_text",
    "question_text",
    "retrieved_passage_ids",
    "retrieved_passage_text",
)
SEMANTIC_SOURCE_DIR = Path(__file__).with_name("semantic")


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_sha256() -> str:
    """Fingerprint frozen semantic sources plus the claim splitter and this input adapter."""
    root = Path(__file__).resolve().parent
    entries = [("claim_assessments.py", hashlib.sha256(Path(__file__).read_bytes()).hexdigest())]
    paths = sorted(SEMANTIC_SOURCE_DIR.glob("*.py"))
    paths.extend(
        [
            root / "engine/rag.py",
            root / "services/claim_assessments.py",
            root / "workers/claim_assessment.py",
        ]
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        entries.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
    return canonical_hash(entries)


def config_digest() -> str:
    """Keep v12's exact AggregationConfig digest and bind the pinned model/input contract."""
    config = AggregationConfig()
    # v12's validated configuration digest is the AggregationConfig digest itself.
    if config.version != AGGREGATION_VERSION:
        raise RuntimeError("semantic evaluator version mismatch")
    return config.digest()


def build_claim_inputs(
    run: EvaluationRun,
    result: EvaluationResult,
    passages: dict[str, str],
) -> tuple[str, list[dict[str, Any]], dict[str, str], str, str | None]:
    """Return question, split claims, recorded retrieved evidence, input hash, and KB hash."""
    if result.output is None:
        raise UnprocessableError("The saved result has no response text for assessment.")
    if len(result.output) > MAX_RESPONSE_CHARS:
        raise UnprocessableError("The saved response exceeds the claim assessment input limit.")
    snapshot = result.case_snapshot
    question = snapshot.get("input") if isinstance(snapshot, dict) else None
    if not isinstance(question, str) or not question.strip():
        raise UnprocessableError("The saved result has no question snapshot for assessment.")
    if len(question) > MAX_QUESTION_CHARS:
        raise UnprocessableError("The saved question exceeds the claim assessment input limit.")
    if result.retrieved_passage_ids is None:
        raise MissingRetrievalMetadataError(
            "This saved result has no recorded retrieval metadata and cannot be assessed."
        )
    if len(result.retrieved_passage_ids) > MAX_RETRIEVED_PASSAGES:
        raise UnprocessableError("The recorded retrieval set exceeds the claim assessment limit.")

    current_kb_hash = knowledge_base_hash(passages)
    if run.knowledge_base_hash != current_kb_hash:
        raise StaleKnowledgeBaseError(
            "The saved run knowledge base differs from the current dataset knowledge base."
        )
    if not isinstance(result.retrieved_passage_ids, list) or any(
        not isinstance(passage_id, str) or not passage_id
        for passage_id in result.retrieved_passage_ids
    ):
        raise InconsistentRetrievalError("Recorded retrieval metadata is malformed.")
    retrieved_ids = list(result.retrieved_passage_ids)
    if len(set(retrieved_ids)) != len(retrieved_ids):
        raise InconsistentRetrievalError(
            "Recorded retrieval metadata contains duplicate passage IDs."
        )
    missing = set(retrieved_ids) - set(passages)
    if missing:
        raise InconsistentRetrievalError(
            "Recorded retrieval references do not match the verified knowledge base."
        )
    evidence = {passage_id: passages[passage_id] for passage_id in retrieved_ids}
    evidence_chars = sum(len(text) for text in evidence.values())
    if evidence_chars > MAX_EVIDENCE_CHARS:
        raise UnprocessableError("Retrieved evidence exceeds the claim assessment input limit.")

    claims = split_claims(result.output)
    if len(claims) > MAX_CLAIMS:
        raise UnprocessableError(f"The response contains more than {MAX_CLAIMS} assessable claims.")
    if any(len(claim.text) > MAX_CLAIM_CHARS for claim in claims):
        raise UnprocessableError("A claim exceeds the claim assessment input limit.")
    serialized = [{"text": claim.text} for claim in claims]
    fingerprint = canonical_hash(
        {
            "question_text": question,
            "response_text": result.output,
            "claims": serialized,
            "retrieved_passage_ids": retrieved_ids,
            "retrieved_passages": evidence,
            "knowledge_base_hash": current_kb_hash,
        }
    )
    return question, serialized, evidence, fingerprint, current_kb_hash


def claim_output(claim_text: str, verdict) -> dict[str, Any]:
    deciding = [
        {
            "passage_id": item.get("passage_id"),
            "relation": item.get("relation"),
            "scope": item.get("scope"),
            "unit": item.get("unit"),
        }
        for item in verdict.deciding
    ]
    branch = "+".join(verdict.guards) or (
        "question_context" if verdict.config.get("question_pass") == "run" else "evidence_only"
    )
    return {
        "claim_text": claim_text,
        "label": verdict.label,
        "abstained": verdict.label == "needs_human_review",
        "reason": verdict.reason,
        "diagnostic_branch": branch,
        "deciding_evidence": deciding,
        "guards": list(verdict.guards),
        "config": verdict.config,
        "model": verdict.model,
        "uncalibrated_scores": verdict.uncalibrated_scores,
        "ignored_contradictions": verdict.ignored_contradictions,
    }


def make_local_verifier():
    """Construct a verifier only when a claim worker has acquired a job; weights load on score()."""
    from .semantic.nli import TransformersNLI
    from .semantic.verifier import SemanticVerifier

    backend = TransformersNLI(MODEL_ID, MODEL_REVISION, max_length=512, batch_size=16)
    return SemanticVerifier(backend, AggregationConfig())
