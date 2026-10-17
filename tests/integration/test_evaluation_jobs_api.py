from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from evalops.models import EvaluationJob, EvaluationJobQueue


def _dataset(client, *, passage: bool = True) -> int:
    response = client.post("/datasets", json={"name": "job-dataset", "description": ""})
    assert response.status_code == 201, response.text
    dataset_id = response.json()["id"]
    cases = client.post(
        f"/datasets/{dataset_id}/test-cases/bulk",
        json={
            "test_cases": [
                {
                    "key": "case-1",
                    "input": "Question",
                    "expected_output": "answer",
                    "required_terms": ["answer"],
                    "relevant_passage_ids": ["KB-ONE"] if passage else [],
                }
            ]
        },
    )
    assert cases.status_code == 201, cases.text
    if passage:
        response = client.post(
            f"/datasets/{dataset_id}/passages",
            json={"passages": [{"passage_id": "KB-ONE", "text": "answer"}]},
        )
        assert response.status_code == 201, response.text
    return dataset_id


def _body(dataset_id: int) -> dict:
    return {
        "dataset_id": dataset_id,
        "model_name": "recorded-model",
        "prompt_version": "v1",
        "config": {"min_token_f1": 0.7},
        "responses": [
            {
                "case_key": "case-1",
                "output": "answer",
                "retrieved_passage_ids": ["KB-ONE"],
            }
        ],
    }


def _submit(client, body: dict, key: str):
    return client.post("/evaluations/jobs", json=body, headers={"Idempotency-Key": key})


def test_job_submission_status_and_idempotent_replay(client):
    dataset_id = _dataset(client)
    body = _body(dataset_id)

    response = _submit(client, body, "job-key-1")
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["status"] == "queued"
    assert created["status_url"] == f"/evaluations/jobs/{created['job_id']}"
    assert created["progress"] == {"completed": 0, "total": 1, "fraction": 0.0}
    assert created["run_id"] is None
    assert created["error"] is None

    replay = _submit(client, body, "job-key-1")
    assert replay.status_code == 200
    assert replay.json()["job_id"] == created["job_id"]
    assert replay.json()["replayed"] is True

    status = client.get(created["status_url"])
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


def test_job_requires_key_and_rejects_key_reuse_for_different_payload(client):
    dataset_id = _dataset(client)
    missing = client.post("/evaluations/jobs", json=_body(dataset_id))
    assert missing.status_code == 422

    assert _submit(client, _body(dataset_id), "job-key-conflict").status_code == 202
    changed = _body(dataset_id) | {"label": "different request"}
    conflict = _submit(client, changed, "job-key-conflict")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"


def test_job_validates_dataset_and_response_case_keys(client):
    missing = _submit(client, _body(987654), "missing-dataset")
    assert missing.status_code == 404

    dataset_id = _dataset(client)
    wrong_case = _body(dataset_id)
    wrong_case["responses"][0]["case_key"] = "not-in-dataset"
    invalid = _submit(client, wrong_case, "unknown-case")
    assert invalid.status_code == 422


def test_scoring_fingerprint_is_stable_and_changes_with_scoring_inputs(client):
    dataset_id = _dataset(client)
    original = _submit(client, _body(dataset_id), "fingerprint-one").json()
    same = _submit(client, _body(dataset_id), "fingerprint-two").json()
    assert len(original["scoring_input_fingerprint"]) == 64
    assert len(original["knowledge_base_hash"]) == 64
    with client.app.state.session_factory() as session:
        first = session.get(EvaluationJob, original["job_id"])
        second = session.get(EvaluationJob, same["job_id"])
        assert first.scoring_input_fingerprint == second.scoring_input_fingerprint
        original_fingerprint = first.scoring_input_fingerprint

    case_id = client.get(f"/datasets/{dataset_id}/test-cases").json()[0]["id"]
    changed = client.patch(f"/test-cases/{case_id}", json={"expected_output": "different"})
    assert changed.status_code == 200, changed.text
    after_case_edit = _submit(client, _body(dataset_id), "fingerprint-three").json()
    with client.app.state.session_factory() as session:
        changed_fingerprint = session.get(
            EvaluationJob, after_case_edit["job_id"]
        ).scoring_input_fingerprint
    assert changed_fingerprint != original_fingerprint

    changed_passage = client.patch(
        f"/datasets/{dataset_id}/passages/KB-ONE", json={"text": "new evidence"}
    )
    assert changed_passage.status_code == 200, changed_passage.text
    after_kb_edit = _submit(client, _body(dataset_id), "fingerprint-four").json()
    with client.app.state.session_factory() as session:
        kb_fingerprint = session.get(
            EvaluationJob, after_kb_edit["job_id"]
        ).scoring_input_fingerprint
    assert kb_fingerprint != changed_fingerprint

    changed_config = _body(dataset_id)
    changed_config["config"] = {"min_token_f1": 0.9}
    after_config_edit = _submit(client, changed_config, "fingerprint-five").json()
    with client.app.state.session_factory() as session:
        config_fingerprint = session.get(
            EvaluationJob, after_config_edit["job_id"]
        ).scoring_input_fingerprint
    assert config_fingerprint != kb_fingerprint


def test_queued_job_cancellation_releases_capacity_and_history_is_terminal(client):
    dataset_id = _dataset(client)
    created = _submit(client, _body(dataset_id), "cancel-me").json()
    cancelled = client.post(f"/evaluations/jobs/{created['job_id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["finished_at"] is not None
    assert cancelled.json()["cancellation_requested_at"] is not None
    with client.app.state.session_factory() as session:
        assert session.get(EvaluationJobQueue, 1).queued_count == 0

    repeated = client.post(f"/evaluations/jobs/{created['job_id']}/cancel")
    assert repeated.status_code == 409
    assert client.get(f"/evaluations/jobs/{created['job_id']}").json()["status"] == "cancelled"

    with client.app.state.session_factory() as session:
        now = datetime.now(UTC)
        running = EvaluationJob(
            status="running",
            idempotency_key="running-cancel",
            request_hash="a" * 64,
            request_payload=_body(dataset_id),
            dataset_id=dataset_id,
            dataset_version=1,
            scoring_input_fingerprint="b" * 64,
            knowledge_base_hash="c" * 64,
            completed_count=0,
            total_count=1,
            created_at=now,
            updated_at=now,
            attempt_count=1,
            lease_owner="worker-test",
            lease_expires_at=now + timedelta(minutes=2),
        )
        session.add(running)
        session.commit()
        running_id = running.id
    requested = client.post(f"/evaluations/jobs/{running_id}/cancel")
    assert requested.status_code == 200
    assert requested.json()["status"] == "running"
    assert requested.json()["cancellation_requested_at"] is not None


def test_unknown_job_status_and_cancel_return_not_found(client):
    assert client.get("/evaluations/jobs/999").status_code == 404
    assert client.post("/evaluations/jobs/999/cancel").status_code == 404


def test_concurrent_submission_cannot_exceed_queue_limit(client):
    # Raise per-user/workspace guards only for this test of the pre-existing global 100-job cap.
    client.app.state.settings = replace(
        client.app.state.settings,
        api_job_submissions_per_minute=150,
        max_queued_jobs_per_workspace=100,
    )
    dataset_id = _dataset(client)
    body = _body(dataset_id)
    for index in range(99):
        response = _submit(client, body, f"queued-{index}")
        assert response.status_code == 202, response.text

    def submit(index: int) -> int:
        return _submit(client, body, f"concurrent-{index}").status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(submit, range(2)))
    assert sorted(statuses) == [202, 429]
    with client.app.state.session_factory() as session:
        assert session.get(EvaluationJobQueue, 1).queued_count == 100
        assert len(session.scalars(select(EvaluationJob)).all()) == 100


def test_synchronous_run_creation_remains_unchanged(client):
    dataset_id = _dataset(client)
    response = client.post("/evaluations/runs", json=_body(dataset_id))
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "completed"
    assert len(run["results"]) == 1
    with client.app.state.session_factory() as session:
        assert session.scalars(select(EvaluationJob)).all() == []
        assert session.get(EvaluationJobQueue, 1).queued_count == 0
