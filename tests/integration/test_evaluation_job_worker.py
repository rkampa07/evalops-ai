from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError

from evalops.models import EvaluationJob, EvaluationJobQueue, EvaluationResult, EvaluationRun
from evalops.repositories.runs import RunRepository
from evalops.services.evaluations import EvaluationService
from evalops.workers.evaluation import EvaluationJobWorker, LeaseLost


def _body(dataset_id: int) -> dict:
    return {
        "dataset_id": dataset_id,
        "model_name": "worker-test-model",
        "prompt_version": "v1",
        "responses": [{"case_key": "case-1", "output": "answer"}],
    }


def _dataset(client) -> int:
    created = client.post("/datasets", json={"name": "worker-dataset", "description": ""})
    assert created.status_code == 201, created.text
    dataset_id = created.json()["id"]
    added = client.post(
        f"/datasets/{dataset_id}/test-cases/bulk",
        json={
            "test_cases": [
                {
                    "key": "case-1",
                    "input": "question",
                    "expected_output": "answer",
                    "required_terms": ["answer"],
                }
            ]
        },
    )
    assert added.status_code == 201, added.text
    return dataset_id


def _submit(client, dataset_id: int) -> int:
    response = client.post(
        "/evaluations/jobs",
        json=_body(dataset_id),
        headers={"Idempotency-Key": f"worker-{dataset_id}-{datetime.now(UTC).timestamp()}"},
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def _worker(client, **kwargs) -> EvaluationJobWorker:
    return EvaluationJobWorker(client.app.state.session_factory, **kwargs)


def _counts(client) -> tuple[int, int, int]:
    with client.app.state.session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(EvaluationRun)) or 0,
            session.scalar(select(func.count()).select_from(EvaluationResult)) or 0,
            session.get(EvaluationJobQueue, 1).queued_count,
        )


def test_worker_run_matches_synchronous_scoring_and_releases_capacity(client):
    dataset_id = _dataset(client)
    body = _body(dataset_id)
    sync = client.post("/evaluations/runs", json=body)
    assert sync.status_code == 201, sync.text
    job_id = _submit(client, dataset_id)
    worker = _worker(client)

    assert worker.run_once() == job_id
    job = client.get(f"/evaluations/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["run_id"] is not None
    background = client.get(f"/evaluations/runs/{job['run_id']}").json()
    assert background["summary"] == sync.json()["summary"]
    assert background["results"][0]["metrics"] == sync.json()["results"][0]["metrics"]
    assert background["results"][0]["failures"] == sync.json()["results"][0]["failures"]
    assert _counts(client)[2] == 0


def test_claim_is_atomic_under_concurrent_workers(client):
    job_id = _submit(client, _dataset(client))
    workers = [_worker(client, worker_id=f"worker-{n}") for n in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda worker: worker.claim_next(), workers))
    actual = [claim for claim in claimed if claim is not None]
    assert len(actual) == 1
    assert actual[0][0] == job_id
    assert _counts(client)[2] == 0


def test_changed_inputs_fail_before_scoring_and_do_not_create_runs(client):
    dataset_id = _dataset(client)
    job_id = _submit(client, dataset_id)
    case_id = client.get(f"/datasets/{dataset_id}/test-cases").json()[0]["id"]
    changed = client.patch(f"/test-cases/{case_id}", json={"expected_output": "changed"})
    assert changed.status_code == 200

    assert _worker(client).run_once() == job_id
    status = client.get(f"/evaluations/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "input_changed"
    assert _counts(client)[0:2] == (0, 0)


def test_knowledge_base_hash_mismatch_fails_before_scoring(client):
    dataset_id = _dataset(client)
    created = client.post(
        f"/datasets/{dataset_id}/passages",
        json={"passages": [{"passage_id": "KB-ONE", "text": "original"}]},
    )
    assert created.status_code == 201, created.text
    job_id = _submit(client, dataset_id)
    changed = client.patch(f"/datasets/{dataset_id}/passages/KB-ONE", json={"text": "changed"})
    assert changed.status_code == 200, changed.text

    assert _worker(client).run_once() == job_id
    result = client.get(f"/evaluations/jobs/{job_id}").json()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "input_changed"
    assert _counts(client)[0:2] == (0, 0)


def test_queued_cancellation_releases_capacity_without_claiming(client):
    job_id = _submit(client, _dataset(client))
    cancelled = client.post(f"/evaluations/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert _worker(client).run_once() is None
    assert _counts(client) == (0, 0, 0)


def test_running_cancellation_is_observed_between_cases(client, monkeypatch):
    dataset_id = _dataset(client)
    # Add a second case so cancellation is checked after the first scored result.
    added = client.post(
        f"/datasets/{dataset_id}/test-cases",
        json={"key": "case-2", "input": "second", "expected_output": "answer"},
    )
    assert added.status_code == 201, added.text
    job_id = _submit(client, dataset_id)
    original = EvaluationService._score
    did_cancel = False

    def cancel_after_first(case, *args, **kwargs):
        nonlocal did_cancel
        result = original(case, *args, **kwargs)
        if not did_cancel:
            did_cancel = True
            response = client.post(f"/evaluations/jobs/{job_id}/cancel")
            assert response.status_code == 200
        return result

    monkeypatch.setattr(EvaluationService, "_score", staticmethod(cancel_after_first))
    assert _worker(client).run_once() == job_id
    assert client.get(f"/evaluations/jobs/{job_id}").json()["status"] == "cancelled"
    assert _counts(client)[0:2] == (0, 0)


def test_crash_before_final_commit_leaves_no_partial_run(client, monkeypatch):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)
    claim = worker.claim_next()
    assert claim is not None
    _, token = claim
    snapshot = worker._load_snapshot(job_id, token)

    class SimulatedProcessCrash(BaseException):
        pass

    original_add = RunRepository.add

    def crash_after_run_rows(repository, run):
        original_add(repository, run)
        raise SimulatedProcessCrash

    monkeypatch.setattr(RunRepository, "add", crash_after_run_rows)
    with pytest.raises(SimulatedProcessCrash):
        worker._execute(job_id, token, *snapshot)
    assert _counts(client)[0:2] == (0, 0)
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        assert job.status == "running"
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert worker.recover_one_expired() is True
    with client.app.state.session_factory() as session:
        assert session.get(EvaluationJob, job_id).status == "queued"
        assert session.get(EvaluationJobQueue, 1).queued_count == 1


def test_expired_lease_recovery_fences_stale_worker(client):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)
    old = worker.claim_next()
    assert old is not None
    old_token = old[1]
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert worker.recover_one_expired() is True
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    new = worker.claim_next()
    assert new is not None and new[0] == job_id and new[1] != old_token
    with pytest.raises(LeaseLost):
        worker._checkpoint(job_id, old_token, 0)
    with pytest.raises(LeaseLost):
        worker._finalize(job_id, old_token, None, None, None, None, None, None)


def test_expired_worker_cannot_fail_or_retry_after_lease_loss(client):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)
    _, token = worker.claim_next()
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with pytest.raises(LeaseLost):
        worker._load_snapshot(job_id, token)
    worker._fail(job_id, token, "evaluation_failed", "sanitized")
    worker._retry_or_fail(job_id, token)
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        assert job.status == "running"
        assert job.error_code is None
        assert session.get(EvaluationJobQueue, 1).queued_count == 0
    assert worker.recover_one_expired() is True
    with client.app.state.session_factory() as session:
        assert session.get(EvaluationJob, job_id).status == "queued"
        assert session.get(EvaluationJobQueue, 1).queued_count == 1


def test_dataset_moved_out_of_job_workspace_fails_before_scoring(client):
    dataset_id = _dataset(client)
    job_id = _submit(client, dataset_id)
    with client.app.state.session_factory() as session:
        from evalops.models import Workspace

        session.add(Workspace(name="moved", slug="moved-dataset"))
        session.commit()

    # Simulate inconsistent legacy/manual database state while leaving all application
    # APIs and normal SQLite foreign-key enforcement enabled for the worker.
    raw = client.app.state.engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("UPDATE datasets SET workspace_id = 2 WHERE id = ?", (dataset_id,))
        raw.commit()
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        raw.close()

    assert _worker(client).run_once() == job_id
    status = client.get(f"/evaluations/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "input_changed"
    assert _counts(client)[0:2] == (0, 0)


def test_inputs_changed_during_scoring_block_finalization(client, monkeypatch):
    dataset_id = _dataset(client)
    job_id = _submit(client, dataset_id)
    case_id = client.get(f"/datasets/{dataset_id}/test-cases").json()[0]["id"]
    original = EvaluationService._score
    changed = False

    def change_case_after_score(case, *args, **kwargs):
        nonlocal changed
        result = original(case, *args, **kwargs)
        if not changed:
            changed = True
            response = client.patch(
                f"/test-cases/{case_id}", json={"expected_output": "changed mid-run"}
            )
            assert response.status_code == 200
        return result

    monkeypatch.setattr(EvaluationService, "_score", staticmethod(change_case_after_score))
    assert _worker(client).run_once() == job_id
    status = client.get(f"/evaluations/jobs/{job_id}").json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "input_changed"
    assert _counts(client)[0:2] == (0, 0)


def test_workspace_mutation_is_rejected_by_database_trigger(client):
    dataset_id = _dataset(client)
    job_id = _submit(client, dataset_id)
    with client.app.state.session_factory() as session:
        from evalops.models import Workspace

        session.add(Workspace(name="other", slug="worker-other"))
        session.commit()
        job = session.get(EvaluationJob, job_id)
        job.workspace_id = 2
        with pytest.raises(IntegrityError, match="evaluation job workspace is immutable"):
            session.commit()
        session.rollback()
        assert session.get(EvaluationJob, job_id).workspace_id == 1


def test_worker_logs_do_not_include_private_exception_content(client, monkeypatch, caplog):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)

    def private_failure(*_args, **_kwargs):
        raise RuntimeError("private-prompt-response-sentinel")

    monkeypatch.setattr(worker, "_execute", private_failure)
    assert worker.run_once() == job_id
    assert "private-prompt-response-sentinel" not in caplog.text
    assert client.get(f"/evaluations/jobs/{job_id}").json()["error"]["code"] == (
        "evaluation_failed"
    )


def test_checkpoint_persists_progress_and_renews_lease(client):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)
    claim = worker.claim_next()
    assert claim is not None
    _, token = claim
    worker._checkpoint(job_id, token, 1)
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        assert job.completed_count == 1
        assert job.heartbeat_at is not None
        assert job.lease_expires_at is not None
        assert job.lease_expires_at > job.heartbeat_at


def test_expired_attempts_exhaust_retries_without_overfilling_queue(client):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)
    claim = worker.claim_next()
    assert claim is not None
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        job.attempt_count = 3
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert worker.recover_one_expired() is True
    result = client.get(f"/evaluations/jobs/{job_id}").json()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "retry_exhausted"
    assert _counts(client)[2] == 0


def test_only_transient_database_failures_are_retried_then_exhausted(client, monkeypatch):
    job_id = _submit(client, _dataset(client))
    worker = _worker(client)

    def transient_failure(*args, **kwargs):
        raise OperationalError("worker", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(worker, "_execute", transient_failure)
    for expected_attempt in (1, 2):
        assert worker.run_once() == job_id
        with client.app.state.session_factory() as session:
            job = session.get(EvaluationJob, job_id)
            assert job.status == "queued"
            assert job.attempt_count == expected_attempt
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
    assert worker.run_once() == job_id
    result = client.get(f"/evaluations/jobs/{job_id}").json()
    assert result["status"] == "failed"
    assert result["error"]["code"] == "retry_exhausted"
    assert _counts(client)[2] == 0


def test_finalization_is_idempotent_and_sync_api_stays_available(client):
    dataset_id = _dataset(client)
    sync = client.post("/evaluations/runs", json=_body(dataset_id))
    assert sync.status_code == 201
    job_id = _submit(client, dataset_id)
    worker = _worker(client)
    assert worker.run_once() == job_id
    with client.app.state.session_factory() as session:
        job = session.get(EvaluationJob, job_id)
        run_id = job.run_id
        assert run_id is not None
        finalized = worker._finalize(job_id, "obsolete-token", None, None, None, None, None, None)
        assert finalized == run_id
    runs, results, queued = _counts(client)
    assert (runs, results, queued) == (2, 2, 0)
