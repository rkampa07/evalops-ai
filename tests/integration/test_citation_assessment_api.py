from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

from evalops.api.deps import WorkspaceAccess, get_workspace_access
from evalops.auth import AuthenticatedPrincipal
from evalops.citation_assessments import config_provenance
from evalops.db import make_engine, make_session_factory, run_migrations
from evalops.errors import QuotaExceededError
from evalops.models import (
    CitationAssessmentJob,
    CitationAssessmentQueue,
    CitationAssessmentResult,
    Workspace,
)
from evalops.services.citation_assessments import CitationAssessmentService
from evalops.workers.citation_assessment import AssessmentCancelled, CitationAssessmentWorker


def _saved_result(client, *, output=None, retrieved_ids="default", extra_passages=None):
    dataset = client.post("/datasets", json={"name": f"citation-{uuid4().hex}"}).json()
    case = client.post(
        f"/datasets/{dataset['id']}/test-cases",
        json={
            "key": "refund",
            "input": "Where does a refund go?",
            "expected_output": "original payment method",
        },
    )
    assert case.status_code == 201, case.text
    passage = client.post(
        f"/datasets/{dataset['id']}/passages",
        json={
            "passages": [
                {
                    "passage_id": "KB-RET-01",
                    "text": "Refunds are issued to the original payment method.",
                },
                *(extra_passages or []),
            ]
        },
    )
    assert passage.status_code == 201, passage.text
    response = {
        "case_key": "refund",
        "output": output or "Refunds go to the original payment method [KB-RET-01].",
    }
    if retrieved_ids == "default":
        response["retrieved_passage_ids"] = ["KB-RET-01"]
    elif retrieved_ids is not None:
        response["retrieved_passage_ids"] = retrieved_ids
    run_response = client.post(
        "/evaluations/runs",
        json={
            "dataset_id": dataset["id"],
            "model_name": "fixture",
            "prompt_version": "test",
            "responses": [response],
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    return run, run["results"][0]


def _submit(client, run, result, key="citation-key", expected=True):
    return client.post(
        f"/evaluations/runs/{run['id']}/results/{result['id']}/assessments",
        headers={"Idempotency-Key": key},
        json={"evaluator": "citation", "citation_expected": expected},
    )


def test_submit_replay_conflict_worker_result_and_lexical_snapshot_unchanged(client):
    run, result = _saved_result(client)
    original_summary = run["summary"]
    original_metrics = result["metrics"]

    submitted = _submit(client, run, result)
    assert submitted.status_code == 202, submitted.text
    job = submitted.json()
    assert job["status"] == "queued"
    replay = _submit(client, run, result)
    assert replay.status_code == 200 and replay.json()["assessment_id"] == job["assessment_id"]
    conflict = _submit(client, run, result, expected=False)
    assert conflict.status_code == 409

    assert client.get(job["result_url"]).status_code == 409
    worker = CitationAssessmentWorker(client.app.state.session_factory)
    assert worker.run_once() == job["assessment_id"]
    saved = client.get(job["result_url"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "correct"
    assert saved.json()["reason_code"] == "citation_set_has_appropriate_passage"
    state = client.get(job["status_url"]).json()
    assert state["status"] == "succeeded" and state["progress"]["fraction"] == 1
    after = client.get(f"/evaluations/runs/{run['id']}").json()
    assert after["summary"] == original_summary
    assert after["results"][0]["metrics"] == original_metrics
    assert client.delete(f"/evaluations/runs/{run['id']}").status_code == 409

    session = client.app.state.session_factory()
    try:
        with pytest.raises(IntegrityError):
            session.execute(update(CitationAssessmentResult).values(reason="tampered"))
        session.rollback()
    finally:
        session.close()


def test_submission_requires_explicit_citation_evaluator_and_expected_flag(client):
    run, result = _saved_result(client)
    endpoint = f"/evaluations/runs/{run['id']}/results/{result['id']}/assessments"
    headers = {"Idempotency-Key": "strict-citation-input"}
    missing_flag = client.post(endpoint, headers=headers, json={"evaluator": "citation"})
    wrong_kind = client.post(
        endpoint,
        headers={"Idempotency-Key": "wrong-kind"},
        json={"evaluator": "claim", "citation_expected": True},
    )
    assert missing_flag.status_code == 422
    assert wrong_kind.status_code == 422


def test_editor_submission_viewer_read_and_cross_workspace_not_found(client):
    run, result = _saved_result(client)
    submitted = _submit(client, run, result)
    assessment_id = submitted.json()["assessment_id"]

    client.app.dependency_overrides[get_workspace_access] = lambda: WorkspaceAccess(
        AuthenticatedPrincipal(None, "test", "viewer", "development"), 1, "viewer"
    )
    assert client.get(f"/evaluations/assessments/{assessment_id}").status_code == 200
    denied = _submit(client, run, result, key="viewer-attempt")
    assert denied.status_code == 403
    client.app.dependency_overrides.pop(get_workspace_access)

    session = client.app.state.session_factory()
    try:
        session.add(Workspace(id=77, name="Other", slug="other-workspace", is_legacy=False))
        session.commit()
    finally:
        session.close()
    client.app.dependency_overrides[get_workspace_access] = lambda: WorkspaceAccess(
        AuthenticatedPrincipal(None, "test", "other", "development"), 77, "owner"
    )
    assert client.get(f"/evaluations/assessments/{assessment_id}").status_code == 404
    other = _submit(client, run, result, key="other-workspace")
    assert other.status_code == 404
    client.app.dependency_overrides.pop(get_workspace_access)


@pytest.mark.parametrize(
    ("retrieved_ids", "output", "citation_expected", "expected_status"),
    [
        (None, "Refunds go to the original payment method [KB-RET-01].", True, 409),
        ([], "Refunds go to the original payment method [KB-RET-01].", True, 202),
        (["KB-RET-01"], "Refunds go to the original payment method.", True, 202),
        (["KB-RET-01"], "Refunds go to the original payment method [KB-UNKNOWN-01].", True, 202),
        (["KB-RET-01"], "Refunds go to the original payment method.", False, 202),
    ],
)
def test_missing_empty_missing_citation_invalid_and_not_applicable(
    client, retrieved_ids, output, citation_expected, expected_status
):
    run, result = _saved_result(client, output=output, retrieved_ids=retrieved_ids)
    response = _submit(client, run, result, expected=citation_expected)
    assert response.status_code == expected_status, response.text
    if expected_status == 409:
        assert response.json()["error"]["code"] == "retrieval_metadata_missing"
        return
    job = response.json()
    worker = CitationAssessmentWorker(client.app.state.session_factory)
    worker.run_once()
    predicted = client.get(job["result_url"]).json()["status"]
    expected = {
        ((), True): "missing_citation",
        (("KB-UNKNOWN-01",), True): "invalid_citation",
        ((), False): "not_applicable",
    }
    citations = ("KB-UNKNOWN-01",) if "KB-UNKNOWN-01" in output else ()
    if retrieved_ids == []:
        assert predicted == "not_retrieved"
    else:
        assert predicted == expected[(citations, citation_expected)]


def test_stale_kb_is_rejected_before_queue_reservation(client):
    run, result = _saved_result(client)
    changed = client.patch(
        f"/datasets/{run['dataset_id']}/passages/KB-RET-01",
        json={"text": "Refunds are issued to a different destination."},
    )
    assert changed.status_code == 200
    response = _submit(client, run, result)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_knowledge_base"
    session = client.app.state.session_factory()
    try:
        assert session.get(CitationAssessmentQueue, 1).queued_count == 0
    finally:
        session.close()


def test_kb_change_after_submission_fails_job_without_output(client):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="stale-after-submit").json()
    changed = client.patch(
        f"/datasets/{run['dataset_id']}/passages/KB-RET-01",
        json={"text": "Changed evidence after the request was queued."},
    )
    assert changed.status_code == 200
    worker = CitationAssessmentWorker(client.app.state.session_factory)
    worker.run_once()
    status = client.get(job["status_url"]).json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "stale_knowledge_base"
    assert client.get(job["result_url"]).status_code == 409


def test_multiple_citations_are_all_preserved_in_output(client):
    run, result = _saved_result(
        client,
        output=(
            "Refunds go to the original payment method [KB-RET-01] and are reviewed by finance "
            "[KB-POL-02]."
        ),
        retrieved_ids=["KB-RET-01", "KB-POL-02"],
        extra_passages=[{"passage_id": "KB-POL-02", "text": "Refunds are reviewed by finance."}],
    )
    job = _submit(client, run, result, key="multiple-citations").json()
    CitationAssessmentWorker(client.app.state.session_factory).run_once()
    prediction = client.get(job["result_url"]).json()
    assert [item["citation_id"] for item in prediction["per_citation"]] == [
        "KB-RET-01",
        "KB-POL-02",
    ]


def test_queue_cancellation_running_cancel_retry_and_lease_fencing(client, monkeypatch):
    run, result = _saved_result(client)
    queued = _submit(client, run, result, key="cancel-queued").json()
    cancelled = client.post(f"/evaluations/assessments/{queued['assessment_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"

    running = _submit(client, run, result, key="cancel-running").json()
    worker = CitationAssessmentWorker(client.app.state.session_factory)
    claimed = worker.claim_next()
    assert claimed and claimed[0] == running["assessment_id"]
    job_id, token = claimed
    assert client.post(f"/evaluations/assessments/{job_id}/cancel").json()["status"] == "running"
    with pytest.raises(AssessmentCancelled):
        worker._load_and_verify(job_id, token)
    assert client.get(f"/evaluations/assessments/{job_id}").json()["status"] == "cancelled"

    retry_job = _submit(client, run, result, key="retry-once").json()
    import evalops.workers.citation_assessment as worker_module

    original_predict = worker_module.predict_citation
    calls = {"count": 0}

    def transient(_inputs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("predict", {}, sqlite3.OperationalError("database is locked"))
        return original_predict(_inputs)

    monkeypatch.setattr(worker_module, "predict_citation", transient)
    worker.run_once()
    assert client.get(retry_job["status_url"]).json()["status"] == "queued"
    monkeypatch.setattr(worker_module, "predict_citation", original_predict)
    session = client.app.state.session_factory()
    try:
        retry_row = session.get(CitationAssessmentJob, retry_job["assessment_id"])
        retry_row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    worker.run_once()
    assert client.get(retry_job["status_url"]).json()["status"] == "succeeded"

    stale_job = _submit(client, run, result, key="lease-fence").json()
    claimed = worker.claim_next()
    assert claimed and claimed[0] == stale_job["assessment_id"]
    job_id, token = claimed
    session = client.app.state.session_factory()
    try:
        row = session.get(CitationAssessmentJob, job_id)
        row.lease_owner = "replacement-worker"
        row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
        session.commit()
    finally:
        session.close()
    worker._fail(job_id, token, "stale", "must not overwrite")
    assert client.get(stale_job["status_url"]).json()["status"] == "running"


def test_cancellation_during_prediction_prevents_output(client, monkeypatch):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="cancel-during-predict").json()
    import evalops.workers.citation_assessment as worker_module

    worker = CitationAssessmentWorker(client.app.state.session_factory)
    original_predict = worker_module.predict_citation

    def cancel_while_predicting(inputs):
        response = client.post(f"/evaluations/assessments/{job['assessment_id']}/cancel")
        assert response.status_code == 200
        return original_predict(inputs)

    monkeypatch.setattr(worker_module, "predict_citation", cancel_while_predicting)
    worker.run_once()
    assert client.get(job["status_url"]).json()["status"] == "cancelled"
    assert client.get(job["result_url"]).status_code == 409


def test_expired_lease_recovers_after_worker_crash_without_partial_output(client):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="crash-recovery").json()
    worker = CitationAssessmentWorker(client.app.state.session_factory)
    claimed = worker.claim_next()
    assert claimed and claimed[0] == job["assessment_id"]
    # Simulate process loss after claiming but before prediction/finalization.
    session = client.app.state.session_factory()
    try:
        row = session.get(CitationAssessmentJob, job["assessment_id"])
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    worker.recover_one_expired()
    session = client.app.state.session_factory()
    try:
        row = session.get(CitationAssessmentJob, job["assessment_id"])
        assert row.status == "queued"
        assert session.scalar(select(CitationAssessmentResult.id)) is None
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()
    worker.run_once()
    assert client.get(job["status_url"]).json()["status"] == "succeeded"


def test_transient_retry_exhaustion_releases_capacity(client, monkeypatch):
    run, result = _saved_result(client)
    job = _submit(client, run, result, key="retry-exhaustion").json()
    import evalops.workers.citation_assessment as worker_module

    def transient(_inputs):
        raise OperationalError("predict", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(worker_module, "predict_citation", transient)
    worker = CitationAssessmentWorker(client.app.state.session_factory)
    for _ in range(3):
        worker.run_once()
        session = client.app.state.session_factory()
        try:
            row = session.get(CitationAssessmentJob, job["assessment_id"])
            if row.status == "queued":
                row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()
        finally:
            session.close()
    state = client.get(job["status_url"]).json()
    assert state["status"] == "failed"
    assert state["error"]["code"] == "retry_exhausted"
    session = client.app.state.session_factory()
    try:
        assert session.get(CitationAssessmentQueue, 1).queued_count == 0
    finally:
        session.close()


def test_per_workspace_queue_cap_and_concurrent_worker_claims(client):
    run, result = _saved_result(client)
    session = client.app.state.session_factory()
    try:
        svc = CitationAssessmentService(
            session,
            workspace_id=1,
            user_id=None,
            submitter_key="test-cap",
            max_queued_per_workspace=2,
        )
        a, _ = svc.submit(run["id"], result["id"], citation_expected=True, idempotency_key="cap-1")
        a_id = a.id
        b, _ = svc.submit(run["id"], result["id"], citation_expected=True, idempotency_key="cap-2")
        b_id = b.id
        with pytest.raises(QuotaExceededError):
            svc.submit(run["id"], result["id"], citation_expected=True, idempotency_key="cap-3")
    finally:
        session.close()
    worker_a = CitationAssessmentWorker(client.app.state.session_factory, worker_id="a")
    worker_b = CitationAssessmentWorker(client.app.state.session_factory, worker_id="b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda worker: worker.claim_next(), (worker_a, worker_b)))
    assert sum(claim is not None for claim in claims) == 1
    assert {claim[0] for claim in claims if claim} <= {a_id, b_id}


def test_concurrent_idempotent_submission_reserves_one_job(client):
    run, result = _saved_result(client)
    factory = client.app.state.session_factory

    def submit_once(_index):
        session = factory()
        try:
            service = CitationAssessmentService(
                session,
                workspace_id=1,
                user_id=None,
                submitter_key="concurrent-test",
            )
            job, replayed = service.submit(
                run["id"], result["id"], citation_expected=True, idempotency_key="same-key"
            )
            return job.id, replayed
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        submissions = list(pool.map(submit_once, range(2)))
    assert submissions[0][0] == submissions[1][0]
    session = factory()
    try:
        assert (
            session.scalar(
                select(CitationAssessmentJob.id).where(
                    CitationAssessmentJob.idempotency_key == "same-key"
                )
            )
            is not None
        )
        assert session.get(CitationAssessmentQueue, 1).queued_count == 1
    finally:
        session.close()


def test_source_and_configuration_provenance_is_saved_on_job(client):
    run, result = _saved_result(client)
    predictor, runner, digest = config_provenance()
    job = _submit(client, run, result).json()
    assert job["predictor_sha256"] == predictor
    assert job["runner_sha256"] == runner
    assert job["config_digest"] == digest


def test_migration_from_0012_preserves_existing_run_data(tmp_path):
    from fastapi.testclient import TestClient

    from evalops.config import Settings
    from evalops.main import create_app

    url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    run_migrations(url, revision="0012")
    settings = Settings(
        database_url=url,
        auto_migrate=False,
        auth_mode="development",
        environment="development",
        allowed_hosts=["testserver"],
        cors_origins=[],
    )
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as client:
        run, _ = _saved_result(client)
    run_migrations(url, revision="0013")
    engine = make_engine(url)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        now = datetime.now(UTC)
        job = CitationAssessmentJob(
            id=7,
            evaluator_kind="citation",
            workspace_id=1,
            submitter_key="dev:local",
            run_id=run["id"],
            result_id=run["results"][0]["id"],
            dataset_id=run["dataset_id"],
            case_key="refund",
            idempotency_key="legacy-citation",
            request_hash="a" * 64,
            request_payload={"citation_expected": True},
            citation_expected=True,
            status="succeeded",
            input_fingerprint="b" * 64,
            knowledge_base_hash=run["knowledge_base_hash"],
            predictor_sha256="c" * 64,
            runner_sha256="d" * 64,
            config_digest="e" * 64,
            completed_count=1,
            total_count=1,
            attempt_count=1,
            created_at=now,
            updated_at=now,
            started_at=now,
            finished_at=now,
        )
        session.add(job)
        session.add(
            CitationAssessmentResult(
                job_id=7,
                workspace_id=1,
                run_id=run["id"],
                result_id=run["results"][0]["id"],
                case_key="refund",
                evaluator_version="citation-evaluator-v4",
                predictor_sha256="c" * 64,
                runner_sha256="d" * 64,
                config_digest="e" * 64,
                input_fingerprint="b" * 64,
                knowledge_base_hash=run["knowledge_base_hash"],
                status="correct",
                reason_code="legacy_reason",
                reason="legacy output retained",
                per_citation=[],
                prediction_payload={"per_citation": []},
                created_at=now,
            )
        )
        session.commit()
    finally:
        session.close()
        engine.dispose()
    run_migrations(url)
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM evaluation_runs")).scalar() == 1
            assert conn.execute(text("SELECT count(*) FROM evaluation_results")).scalar() == 1
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0014"
            assert conn.execute(text("SELECT count(*) FROM citation_assessment_jobs")).scalar() == 1
            assert (
                conn.execute(text("SELECT count(*) FROM citation_assessment_results")).scalar() == 1
            )
            assert conn.execute(text("SELECT count(*) FROM assessment_idempotency")).scalar() == 1
            assert (
                conn.execute(text("SELECT next_id FROM assessment_id_sequence WHERE id=1")).scalar()
                == 8
            )
            assert (
                conn.execute(
                    text("SELECT queued_count FROM citation_assessment_queue_state WHERE id=1")
                ).scalar()
                == 0
            )
    finally:
        engine.dispose()
