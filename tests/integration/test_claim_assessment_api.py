from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from evalops.api.deps import WorkspaceAccess, get_workspace_access
from evalops.auth import AuthenticatedPrincipal
from evalops.models import (
    AssessmentIdempotency,
    AssessmentIdSequence,
    CitationAssessmentQueue,
    ClaimAssessmentJob,
    ClaimAssessmentResult,
)
from evalops.semantic.nli import SemanticBackendUnavailable
from evalops.semantic.verdict import SemanticVerdict
from evalops.services.claim_assessments import ClaimAssessmentService
from evalops.workers.citation_assessment import CitationAssessmentWorker
from evalops.workers.claim_assessment import ClaimAssessmentWorker, LeaseLost


def _saved_result(client, output="Refunds go to the original payment method."):
    dataset = client.post("/datasets", json={"name": f"claim-{uuid4().hex}"}).json()
    case = client.post(
        f"/datasets/{dataset['id']}/test-cases",
        json={
            "key": "refund",
            "input": "Where does a refund go?",
            "expected_output": "private gold answer",
        },
    )
    assert case.status_code == 201, case.text
    passages = client.post(
        f"/datasets/{dataset['id']}/passages",
        json={
            "passages": [
                {"passage_id": "KB-RET-01", "text": "Refunds go to the original payment method."}
            ]
        },
    )
    assert passages.status_code == 201, passages.text
    run_response = client.post(
        "/evaluations/runs",
        json={
            "dataset_id": dataset["id"],
            "model_name": "fixture",
            "prompt_version": "test",
            "responses": [
                {"case_key": "refund", "output": output, "retrieved_passage_ids": ["KB-RET-01"]}
            ],
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    return run, run["results"][0]


def _submit(client, run, result, *, key="claim-key"):
    return client.post(
        f"/evaluations/runs/{run['id']}/results/{result['id']}/assessments",
        headers={"Idempotency-Key": key},
        json={"evaluator": "claim"},
    )


class _FixtureVerifier:
    def __init__(self, labels):
        self.labels = iter(labels)
        self.calls = []

    def verify(self, question, claim, evidence):
        self.calls.append((question, claim, dict(evidence)))
        return SemanticVerdict(
            label=next(self.labels),
            reason="synthetic fixture verdict",
            deciding=[
                {
                    "passage_id": "KB-RET-01",
                    "relation": "support",
                    "scope": "sentence",
                    "unit": evidence.get("KB-RET-01", ""),
                }
            ],
            guards=["fixture_branch"],
            model={"backend": "fixture"},
            config={"question_pass": "not needed", "digest": "92aacfdf47908332"},
        )


def _worker(client, verifier):
    return ClaimAssessmentWorker(
        client.app.state.session_factory,
        worker_id=f"test-{uuid4().hex}",
        verifier_factory=lambda: verifier,
    )


def test_claim_submission_replay_and_cross_evaluator_key_conflict(client):
    run, result = _saved_result(client)
    lexical_summary = run["summary"]
    lexical_metrics = result["metrics"]
    submitted = _submit(client, run, result)
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()
    assert job["evaluator"] == "claim" and job["status"] == "queued"
    replay = _submit(client, run, result)
    assert replay.status_code == 200
    assert replay.json()["assessment_id"] == job["assessment_id"]
    other_kind = client.post(
        f"/evaluations/runs/{run['id']}/results/{result['id']}/assessments",
        headers={"Idempotency-Key": "claim-key"},
        json={"evaluator": "citation", "citation_expected": True},
    )
    assert other_kind.status_code == 409
    assert client.get(job["status_url"]).json()["evaluator"] == "claim"
    assert client.get(job["result_url"]).status_code == 409

    verifier = _FixtureVerifier(["supported"])
    worker = _worker(client, verifier)
    assert worker.run_once() == job["assessment_id"]
    saved = client.get(job["result_url"])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["status"] == "assessed"
    assert body["claims"][0]["label"] == "supported"
    assert verifier.calls[0] == (
        "Where does a refund go?",
        "Refunds go to the original payment method.",
        {"KB-RET-01": "Refunds go to the original payment method."},
    )
    assert client.get(job["status_url"]).json()["status"] == "succeeded"
    assert client.get(f"/evaluations/runs/{run['id']}").json()["summary"] == lexical_summary
    assert (
        client.get(f"/evaluations/runs/{run['id']}").json()["results"][0]["metrics"]
        == lexical_metrics
    )
    assert client.delete(f"/evaluations/runs/{run['id']}").status_code == 409


def test_all_four_claim_labels_and_abstention_are_persisted_separately(client):
    output = (
        "Refunds go to the original payment method. "
        "Refund requests take five days. "
        "Refunds may use another method. "
        "Refund processing depends on account status."
    )
    run, result = _saved_result(client, output)
    job = _submit(client, run, result, key="four-labels").json()
    verifier = _FixtureVerifier(
        ["supported", "contradicted", "insufficient_evidence", "needs_human_review"]
    )
    assert _worker(client, verifier).run_once() == job["assessment_id"]
    result_body = client.get(job["result_url"]).json()
    assert [claim["label"] for claim in result_body["claims"]] == [
        "supported",
        "contradicted",
        "insufficient_evidence",
        "needs_human_review",
    ]
    assert [claim["abstained"] for claim in result_body["claims"]] == [False, False, False, True]
    assert all(claim["diagnostic_branch"] == "fixture_branch" for claim in result_body["claims"])


def test_claim_workspace_isolation_and_viewer_submission_denial(client):
    run, result = _saved_result(client)
    job = _submit(client, run, result).json()
    client.app.dependency_overrides[get_workspace_access] = lambda: WorkspaceAccess(
        AuthenticatedPrincipal(None, "other", "editor", "development"), 999, "editor"
    )
    assert client.get(job["status_url"]).status_code == 404
    assert client.get(job["result_url"]).status_code == 404
    assert client.post(f"/evaluations/assessments/{job['assessment_id']}/cancel").status_code == 404
    client.app.dependency_overrides[get_workspace_access] = lambda: WorkspaceAccess(
        AuthenticatedPrincipal(None, "viewer", "viewer", "development"), 1, "viewer"
    )
    denied = _submit(client, run, result, key="viewer-claim")
    assert denied.status_code == 403


def test_claim_input_metadata_null_stale_and_empty_are_distinct(client):
    run, result = _saved_result(client)
    session = client.app.state.session_factory()
    try:
        from evalops.models import EvaluationResult as ResultRow

        row = session.get(ResultRow, result["id"])
        row.retrieved_passage_ids = None
        session.commit()
    finally:
        session.close()
    missing = _submit(client, run, result, key="missing-retrieval")
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "retrieval_metadata_missing"

    session = client.app.state.session_factory()
    try:
        from evalops.models import EvaluationResult as ResultRow

        row = session.get(ResultRow, result["id"])
        row.retrieved_passage_ids = []
        session.commit()
    finally:
        session.close()
    empty = _submit(client, run, result, key="empty-retrieval")
    assert empty.status_code == 202, empty.text
    verifier = _FixtureVerifier(["insufficient_evidence"])
    assert _worker(client, verifier).run_once() == empty.json()["assessment_id"]
    assert verifier.calls[0][2] == {}

    session = client.app.state.session_factory()
    try:
        from evalops.models import EvaluationRun as RunRow

        row = session.get(RunRow, run["id"])
        row.knowledge_base_hash = "f" * 64
        session.commit()
    finally:
        session.close()
    stale = _submit(client, run, result, key="stale-kb")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_knowledge_base"


def test_model_unavailable_is_sanitized_and_never_falls_back(client):
    run, result = _saved_result(client)
    submitted = _submit(client, run, result, key="no-model")
    assert submitted.status_code == 202
    job = submitted.json()
    called = []

    def missing_model():
        called.append(True)
        raise SemanticBackendUnavailable("private cache path /not-for-api")

    worker = ClaimAssessmentWorker(client.app.state.session_factory, verifier_factory=missing_model)
    # API submission itself has not attempted model initialization.
    assert called == []
    assert worker.run_once() == job["assessment_id"]
    status = client.get(job["status_url"]).json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "model_unavailable"
    assert "private cache path" not in status["error"]["message"]
    assert client.get(job["result_url"]).status_code == 409


def test_claim_cancel_mid_scoring_produces_no_partial_output(client):
    output = "First policy claim is available. Second policy claim is available."
    run, result = _saved_result(client, output)
    job = _submit(client, run, result, key="cancel-mid-score").json()
    app = client.app

    class CancellingVerifier:
        calls = 0

        def verify(self, question, claim, evidence):
            self.calls += 1
            if self.calls == 1:
                session = app.state.session_factory()
                try:
                    service = ClaimAssessmentService(
                        session, workspace_id=1, user_id=None, submitter_key="dev:local"
                    )
                    service.cancel(job["assessment_id"])
                finally:
                    session.close()
            return SemanticVerdict(label="supported", reason="fixture")

    assert _worker(client, CancellingVerifier()).run_once() == job["assessment_id"]
    assert client.get(job["status_url"]).json()["status"] == "cancelled"
    assert client.get(job["result_url"]).status_code == 409
    session = app.state.session_factory()
    try:
        assert session.scalar(select(ClaimAssessmentResult.id)) is None
    finally:
        session.close()


def test_claim_lease_fencing_retry_and_immutable_output(client, monkeypatch):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="lease-claim").json()
    worker = _worker(client, _FixtureVerifier(["supported"]))
    old_claim = worker.claim_next()
    assert old_claim
    session = client.app.state.session_factory()
    try:
        row = session.get(ClaimAssessmentJob, job["assessment_id"])
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    assert worker.recover_one_expired()
    session = client.app.state.session_factory()
    try:
        row = session.get(ClaimAssessmentJob, job["assessment_id"])
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        row.attempt_count = 1
        session.commit()
    finally:
        session.close()
    new_claim = worker.claim_next()
    assert new_claim and new_claim[1] != old_claim[1]
    with pytest.raises(LeaseLost):
        worker._finalize(job["assessment_id"], old_claim[1], [], ("bad", None, "bad", "bad"))

    # A transient database lock is retried safely before producing output.
    def transient_failure(*args, **kwargs):
        raise OperationalError("write", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(worker, "_finalize", transient_failure)
    # Finish the currently leased job through run_once after expiring/requeueing this attempt.
    session = client.app.state.session_factory()
    try:
        row = session.get(ClaimAssessmentJob, job["assessment_id"])
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    assert worker.recover_one_expired()
    session = client.app.state.session_factory()
    try:
        row = session.get(ClaimAssessmentJob, job["assessment_id"])
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        row.attempt_count = 1
        session.commit()
    finally:
        session.close()
    assert worker.run_once() == job["assessment_id"]
    session = client.app.state.session_factory()
    try:
        row = session.get(ClaimAssessmentJob, job["assessment_id"])
        assert row.status == "queued"
        assert row.error_code == "transient_infrastructure"
        assert session.scalar(select(ClaimAssessmentResult.id)) is None
    finally:
        session.close()


def test_assessment_ids_and_queue_are_shared_with_citation_worker(client):
    run, result = _saved_result(client)
    claim_job = _submit(client, run, result, key="shared-claim").json()
    citation = client.post(
        f"/evaluations/runs/{run['id']}/results/{result['id']}/assessments",
        headers={"Idempotency-Key": "shared-citation"},
        json={"evaluator": "citation", "citation_expected": True},
    )
    assert citation.status_code == 202, citation.text
    assert claim_job["assessment_id"] != citation.json()["assessment_id"]
    session = client.app.state.session_factory()
    try:
        assert session.scalar(select(CitationAssessmentQueue.queued_count)) == 2
        assert (
            session.scalar(select(AssessmentIdSequence.next_id)) > citation.json()["assessment_id"]
        )
        assert session.scalar(select(func.count()).select_from(AssessmentIdempotency)) == 2
    finally:
        session.close()
    # The existing citation worker does not consume or mutate claim jobs.
    citation_worker = CitationAssessmentWorker(client.app.state.session_factory)
    assert citation_worker.run_once() == citation.json()["assessment_id"]
    assert client.get(claim_job["status_url"]).json()["status"] == "queued"
    assert client.get(citation.json()["result_url"]).status_code == 200


def test_citation_worker_respects_active_claim_lease(client):
    run, result = _saved_result(client)
    claim_job = _submit(client, run, result, key="active-claim").json()
    claim_worker = _worker(client, _FixtureVerifier(["supported"]))
    assert claim_worker.claim_next()[0] == claim_job["assessment_id"]
    citation = client.post(
        f"/evaluations/runs/{run['id']}/results/{result['id']}/assessments",
        headers={"Idempotency-Key": "queued-citation"},
        json={"evaluator": "citation", "citation_expected": True},
    ).json()
    assert CitationAssessmentWorker(client.app.state.session_factory).run_once() is None
    assert client.get(citation["status_url"]).json()["status"] == "queued"


def test_queued_claim_cancellation_releases_shared_capacity(client):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="cancel-queued").json()
    cancelled = client.post(f"/evaluations/assessments/{job['assessment_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    session = client.app.state.session_factory()
    try:
        assert session.scalar(select(CitationAssessmentQueue.queued_count)) == 0
    finally:
        session.close()


def test_claim_result_cannot_be_updated_or_deleted(client):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="immutable-claim").json()
    assert _worker(client, _FixtureVerifier(["contradicted"])).run_once() == job["assessment_id"]
    session = client.app.state.session_factory()
    try:
        with pytest.raises(IntegrityError):
            session.execute(update(ClaimAssessmentResult).values(output_status="tampered"))
        session.rollback()
    finally:
        session.close()
