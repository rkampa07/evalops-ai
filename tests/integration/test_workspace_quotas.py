from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from evalops.auth import SESSION_COOKIE, _csrf_token_for, _digest
from evalops.config import Settings
from evalops.main import create_app
from evalops.models import (
    AuthSession,
    EvaluationJob,
    EvaluationJobQueue,
    User,
    Workspace,
    WorkspaceMembership,
)
from evalops.workers.evaluation import EvaluationJobWorker


@contextmanager
def _isolated_client(tmp_path, **overrides):
    values = {
        "database_url": f"sqlite:///{tmp_path / 'quota.db'}",
        "auto_migrate": True,
        "allowed_hosts": ["testserver", "localhost"],
        "cors_origins": ["http://localhost:5173"],
        "environment": "development",
        "auth_mode": "oidc",
        "auth_cookie_secure": False,
        "oidc_issuer": "https://issuer.example",
        "oidc_client_id": "client",
        "oidc_redirect_uri": "https://app.example/auth/callback",
    }
    values.update(overrides)
    app = create_app(Settings(**values))
    token = "quota-test-session-token"
    csrf = _csrf_token_for(token)
    with TestClient(app, client=("127.0.0.1", 54000), raise_server_exceptions=False) as client:
        with app.state.session_factory() as session:
            user = User(issuer="https://issuer.example", subject="quota-user")
            alpha = Workspace(name="Quota Alpha", slug="quota-alpha")
            beta = Workspace(name="Quota Beta", slug="quota-beta")
            session.add_all([user, alpha, beta])
            session.flush()
            session.add_all(
                [
                    WorkspaceMembership(user_id=user.id, workspace_id=alpha.id, role="owner"),
                    WorkspaceMembership(user_id=user.id, workspace_id=beta.id, role="owner"),
                    AuthSession(
                        token_hash=_digest(token),
                        user_id=user.id,
                        csrf_token_hash=_digest(csrf),
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    ),
                ]
            )
            alpha_id, beta_id = alpha.id, beta.id
            session.commit()
        client.cookies.set(SESSION_COOKIE, token)
        yield client, csrf, alpha_id, beta_id, app
    app.state.engine.dispose()


def _headers(csrf: str, workspace_id: int, key: str | None = None) -> dict[str, str]:
    result = {"X-CSRF-Token": csrf, "X-Workspace-ID": str(workspace_id)}
    if key:
        result["Idempotency-Key"] = key
    return result


def _dataset(client, csrf, workspace_id: int, name: str, *, with_case: bool = True) -> int:
    response = client.post("/datasets", json={"name": name}, headers=_headers(csrf, workspace_id))
    assert response.status_code == 201, response.text
    dataset_id = response.json()["id"]
    if with_case:
        case = client.post(
            f"/datasets/{dataset_id}/test-cases",
            json={"key": "case", "input": "question", "expected_output": "answer"},
            headers=_headers(csrf, workspace_id),
        )
        assert case.status_code == 201, case.text
    return dataset_id


def _job(client, csrf, workspace_id: int, dataset_id: int, key: str):
    return client.post(
        "/evaluations/jobs",
        json={
            "dataset_id": dataset_id,
            "model_name": "quota-test",
            "prompt_version": "v1",
            "responses": [{"case_key": "case", "output": "answer"}],
        },
        headers=_headers(csrf, workspace_id, key),
    )


def test_dataset_case_passage_quotas_are_atomic_and_workspace_scoped(tmp_path):
    with _isolated_client(
        tmp_path,
        max_datasets_per_workspace=1,
        max_test_cases_per_dataset=2,
        max_passages_per_workspace=2,
    ) as (client, csrf, alpha, beta, _):
        ds_a = _dataset(client, csrf, alpha, "quota-dataset-a", with_case=False)
        ds_b = _dataset(client, csrf, beta, "quota-dataset-b", with_case=False)

        denied = client.post(
            "/datasets", json={"name": "quota-overflow"}, headers=_headers(csrf, alpha)
        )
        assert denied.status_code == 429
        assert denied.headers["Retry-After"] == "60"
        assert (
            client.post(
                f"/datasets/{ds_a}/test-cases/bulk",
                json={
                    "test_cases": [
                        {"key": "one", "input": "q1", "expected_output": "a"},
                        {"key": "two", "input": "q2", "expected_output": "a"},
                    ]
                },
                headers=_headers(csrf, alpha),
            ).status_code
            == 201
        )
        cases_over = client.post(
            f"/datasets/{ds_a}/test-cases",
            json={"key": "three", "input": "q3", "expected_output": "a"},
            headers=_headers(csrf, alpha),
        )
        assert cases_over.status_code == 429

        passages = client.post(
            f"/datasets/{ds_a}/passages",
            json={
                "passages": [
                    {"passage_id": "KB-ONE", "text": "one"},
                    {"passage_id": "KB-TWO", "text": "two"},
                ]
            },
            headers=_headers(csrf, alpha),
        )
        assert passages.status_code == 201
        other_workspace = client.post(
            f"/datasets/{ds_b}/passages",
            json={"passages": [{"passage_id": "KB-THREE", "text": "three"}]},
            headers=_headers(csrf, beta),
        )
        assert other_workspace.status_code == 201


def test_workspace_job_queue_quota_idempotency_and_cancel_release(tmp_path):
    with _isolated_client(tmp_path, max_queued_jobs_per_workspace=1) as (
        client,
        csrf,
        alpha,
        beta,
        app,
    ):
        ds_a = _dataset(client, csrf, alpha, "queue-alpha")
        ds_b = _dataset(client, csrf, beta, "queue-beta")
        first = _job(client, csrf, alpha, ds_a, "queue-alpha-1")
        assert first.status_code == 202
        replay = _job(client, csrf, alpha, ds_a, "queue-alpha-1")
        assert replay.status_code == 200 and replay.json()["job_id"] == first.json()["job_id"]
        full = _job(client, csrf, alpha, ds_a, "queue-alpha-2")
        assert full.status_code == 429 and "Retry-After" in full.headers
        assert _job(client, csrf, beta, ds_b, "queue-beta-1").status_code == 202

        cancelled = client.post(
            f"/evaluations/jobs/{first.json()['job_id']}/cancel",
            headers=_headers(csrf, alpha),
        )
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert _job(client, csrf, alpha, ds_a, "queue-alpha-3").status_code == 202
        with app.state.session_factory() as session:
            assert session.get(EvaluationJobQueue, 1).queued_count == 2


def test_concurrent_job_submissions_cannot_overrun_workspace_queue(tmp_path):
    with _isolated_client(tmp_path, max_queued_jobs_per_workspace=1) as (
        client,
        csrf,
        alpha,
        _,
        app,
    ):
        dataset_id = _dataset(client, csrf, alpha, "concurrent-quota")
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda key: _job(client, csrf, alpha, dataset_id, key),
                    ["concurrent-one", "concurrent-two"],
                )
            )
        assert sorted(response.status_code for response in responses) == [202, 429]
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(EvaluationJob)) == 1
            assert session.get(EvaluationJobQueue, 1).queued_count == 1


def test_worker_claim_skips_workspace_at_active_limit_and_preserves_fairness(tmp_path):
    with _isolated_client(tmp_path) as (client, csrf, alpha, beta, app):
        ds_a = _dataset(client, csrf, alpha, "fair-alpha")
        ds_b = _dataset(client, csrf, beta, "fair-beta")
        a1 = _job(client, csrf, alpha, ds_a, "fair-a1").json()["job_id"]
        a2 = _job(client, csrf, alpha, ds_a, "fair-a2").json()["job_id"]
        b1 = _job(client, csrf, beta, ds_b, "fair-b1").json()["job_id"]
        worker = EvaluationJobWorker(app.state.session_factory)
        first = worker.claim_next()
        assert first is not None and first[0] == a1
        second = worker.claim_next()
        assert second is not None and second[0] == b1
        assert worker.claim_next() is None

        # A requested cancellation retains its active slot until the worker observes it.
        client.post(f"/evaluations/jobs/{a1}/cancel", headers=_headers(csrf, alpha))
        client.post(f"/evaluations/jobs/{b1}/cancel", headers=_headers(csrf, beta))
        with app.state.session_factory() as session:
            job = session.get(EvaluationJob, a2)
            assert job.status == "queued"
            assert session.get(EvaluationJob, a1).cancellation_requested_at is not None


def test_expired_job_recovery_restores_queue_quota_once(tmp_path):
    with _isolated_client(tmp_path, max_queued_jobs_per_workspace=1) as (
        client,
        csrf,
        alpha,
        _,
        app,
    ):
        dataset_id = _dataset(client, csrf, alpha, "recovery-quota")
        job_id = _job(client, csrf, alpha, dataset_id, "recovery-1").json()["job_id"]
        worker = EvaluationJobWorker(app.state.session_factory)
        claim = worker.claim_next()
        assert claim is not None and claim[0] == job_id
        with app.state.session_factory() as session:
            job = session.get(EvaluationJob, job_id)
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()
        assert worker.recover_one_expired() is True
        with app.state.session_factory() as session:
            assert session.get(EvaluationJob, job_id).status == "queued"
            assert session.get(EvaluationJobQueue, 1).queued_count == 1
        assert _job(client, csrf, alpha, dataset_id, "recovery-2").status_code == 429


def test_persistent_rate_limit_returns_retry_after(tmp_path):
    with _isolated_client(tmp_path, api_read_requests_per_minute=2) as (client, csrf, alpha, _, _):
        assert client.get("/datasets", headers=_headers(csrf, alpha)).status_code == 200
        assert client.get("/datasets", headers=_headers(csrf, alpha)).status_code == 200
        limited = client.get("/datasets", headers=_headers(csrf, alpha))
        assert limited.status_code == 429
        assert 1 <= int(limited.headers["Retry-After"]) <= 60


def test_write_and_job_rate_limits_are_separate(tmp_path):
    with _isolated_client(tmp_path, api_write_requests_per_minute=1) as (client, csrf, alpha, _, _):
        assert (
            client.post(
                "/datasets", json={"name": "write-rate-one"}, headers=_headers(csrf, alpha)
            ).status_code
            == 201
        )
        limited = client.post(
            "/datasets", json={"name": "write-rate-two"}, headers=_headers(csrf, alpha)
        )
        assert limited.status_code == 429 and "Retry-After" in limited.headers

    with _isolated_client(tmp_path / "job-rate", api_job_submissions_per_minute=1) as (
        client,
        csrf,
        alpha,
        _,
        app,
    ):
        dataset_id = _dataset(client, csrf, alpha, "job-rate-dataset")
        first = _job(client, csrf, alpha, dataset_id, "job-rate-key")
        assert first.status_code == 202
        limited_replay = _job(client, csrf, alpha, dataset_id, "job-rate-key")
        assert limited_replay.status_code == 429
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(EvaluationJob)) == 1
            assert session.get(EvaluationJobQueue, 1).queued_count == 1
