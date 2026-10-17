from __future__ import annotations

from evalops.api.deps import get_authenticated_principal
from evalops.auth import AuthenticatedPrincipal
from evalops.models import (
    ChatMessage,
    Dataset,
    EvaluationJob,
    EvaluationRun,
    Passage,
    TestCase,
    User,
    Workspace,
    WorkspaceMembership,
)
from evalops.workers.evaluation import EvaluationJobWorker


def _identity(client, *, email: str, workspace_name: str, role: str):
    with client.app.state.session_factory() as session:
        user = User(issuer="https://issuer.example", subject=email, email=email)
        workspace = Workspace(name=workspace_name, slug=workspace_name.lower())
        session.add_all([user, workspace])
        session.flush()
        session.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role=role))
        session.commit()
        user_id, workspace_id = user.id, workspace.id

    def principal():
        return AuthenticatedPrincipal(
            user_id=user_id, issuer="https://issuer.example", subject=email, mode="oidc"
        )

    client.app.dependency_overrides[get_authenticated_principal] = principal
    return workspace_id


def _dataset(client, name: str) -> int:
    response = client.post("/datasets", json={"name": name, "description": ""})
    assert response.status_code == 201, response.text
    dataset_id = response.json()["id"]
    response = client.post(
        f"/datasets/{dataset_id}/test-cases",
        json={"key": "case", "input": "question", "expected_output": "answer"},
    )
    assert response.status_code == 201, response.text
    response = client.post(
        f"/datasets/{dataset_id}/passages",
        json={"passages": [{"passage_id": "KB-ONE", "text": "answer"}]},
    )
    assert response.status_code == 201, response.text
    return dataset_id


def _run(client, dataset_id: int):
    return client.post(
        "/evaluations/runs",
        json={
            "dataset_id": dataset_id,
            "model_name": "fixture",
            "prompt_version": "v1",
            "responses": [{"case_key": "case", "output": "answer"}],
        },
    )


def test_workspace_queries_roles_nested_ids_and_pagination(client):
    ws_a = _identity(client, email="a@example.test", workspace_name="alpha", role="editor")
    with client.app.state.session_factory() as session:
        user_a = session.query(User).filter_by(subject="a@example.test").one()
        session.add(WorkspaceMembership(user_id=user_a.id, workspace_id=1, role="owner"))
        session.commit()
    assert client.get("/datasets", headers={"X-Workspace-ID": "1"}).status_code == 404
    ds_a = _dataset(client, "alpha-data")
    run_a = _run(client, ds_a)
    assert run_a.status_code == 201, run_a.text
    run_a_id = run_a.json()["id"]

    ws_b = _identity(client, email="b@example.test", workspace_name="beta", role="viewer")
    with client.app.state.session_factory() as session:
        ds_b = Dataset(name="beta-data", description="", workspace_id=ws_b)
        session.add(ds_b)
        session.flush()
        session.add(
            TestCase(
                dataset_id=ds_b.id,
                key="case",
                input="question",
                expected_output="answer",
                content_hash="b" * 64,
            )
        )
        session.add(Passage(dataset_id=ds_b.id, passage_id="KB-ONE", text="answer"))
        session.commit()
        ds_b_id = ds_b.id

    assert client.get("/datasets?limit=10").json()["items"][0]["id"] == ds_b_id
    assert client.get(f"/datasets/{ds_a}").status_code == 404
    assert client.get(f"/datasets/{ds_a}/test-cases").status_code == 404
    assert client.get(f"/datasets/{ds_a}/passages").status_code == 404
    assert (
        client.patch(f"/datasets/{ds_a}/passages/KB-ONE", json={"text": "hidden"}).status_code
        == 403
    )
    with client.app.state.session_factory() as session:
        case_a_id = session.query(TestCase).filter_by(dataset_id=ds_a).one().id
    assert client.get(f"/test-cases/{case_a_id}").status_code == 404
    assert (
        client.patch(f"/test-cases/{case_a_id}", json={"expected_output": "hidden"}).status_code
        == 403
    )

    # A viewer may read its own workspace runs but not another workspace's IDs.
    assert client.get(f"/evaluations/runs/{run_a_id}").status_code == 404
    page = client.get("/datasets?limit=1&offset=0").json()
    assert page["total"] == 1 and [item["id"] for item in page["items"]] == [ds_b_id]
    assert (
        client.post("/datasets", json={"name": "viewer-write", "description": ""}).status_code
        == 403
    )
    assert ws_a != ws_b


def test_comparison_gate_jobs_and_legacy_are_workspace_scoped(client):
    _identity(client, email="writer@example.test", workspace_name="writer", role="editor")
    dataset_id = _dataset(client, "writer-data")
    first = _run(client, dataset_id).json()
    second = _run(client, dataset_id).json()
    job = client.post(
        "/evaluations/jobs",
        headers={"Idempotency-Key": "same-key"},
        json={
            "dataset_id": dataset_id,
            "model_name": "fixture",
            "prompt_version": "v1",
            "responses": [{"case_key": "case", "output": "answer"}],
        },
    )
    assert job.status_code == 202
    job_id = job.json()["job_id"]

    _identity(client, email="reader@example.test", workspace_name="reader", role="editor")
    assert client.get(f"/evaluations/jobs/{job_id}").status_code == 404
    assert client.post(f"/evaluations/jobs/{job_id}/cancel").status_code == 404
    for path in (
        f"/evaluations/runs/{first['id']}",
        f"/evaluations/runs/{first['id']}/summary",
        f"/evaluations/runs/{first['id']}/results",
        f"/evaluations/runs/{first['id']}/report",
    ):
        assert client.get(path).status_code == 404
    assert client.delete(f"/evaluations/runs/{first['id']}").status_code == 404
    other_workspace = _identity(
        client, email="other-writer@example.test", workspace_name="other-writer", role="editor"
    )
    other_dataset = _dataset(client, "other-writer-data")
    assert (
        client.patch(f"/datasets/{dataset_id}/passages/KB-ONE", json={"text": "hidden"}).status_code
        == 404
    )
    other_job = client.post(
        "/evaluations/jobs",
        headers={"Idempotency-Key": "same-key"},
        json={
            "dataset_id": other_dataset,
            "model_name": "fixture",
            "prompt_version": "v1",
            "responses": [{"case_key": "case", "output": "answer"}],
        },
    )
    assert other_job.status_code == 202
    assert other_workspace != 1
    assert (
        client.get(
            f"/evaluations/compare?baseline_run_id={first['id']}&candidate_run_id={second['id']}"
        ).status_code
        == 404
    )
    gate = client.post(
        "/evaluations/gates",
        json={"candidate_run_id": first["id"], "gates": {"min_pass_rate": 0.5}},
    )
    assert gate.status_code == 404
    # Reserved legacy data is never a membership target for an OIDC identity.
    assert client.get("/datasets/1").status_code == 404


def test_chat_histories_are_private_to_creator(client):
    _identity(client, email="chat-a@example.test", workspace_name="chat-a", role="editor")
    dataset_id = _dataset(client, "chat-data")
    session = client.post(
        "/chat/sessions", json={"dataset_id": dataset_id, "prompt_version": "rag-v1"}
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["id"]
    with client.app.state.session_factory() as db:
        db.add_all(
            [
                ChatMessage(session_id=session_id, role="user", content="question"),
                ChatMessage(session_id=session_id, role="assistant", content="answer"),
            ]
        )
        db.commit()
        assistant_id = (
            db.query(ChatMessage).filter_by(session_id=session_id, role="assistant").one().id
        )

    _identity(client, email="chat-b@example.test", workspace_name="chat-b", role="editor")
    assert client.get("/chat/sessions").json() == []
    assert client.get(f"/chat/sessions/{session_id}").status_code == 404
    assert client.delete(f"/chat/sessions/{session_id}").status_code == 404
    assert (
        client.post(
            f"/chat/messages/{assistant_id}/test-case",
            json={"key": "captured", "relevant_passage_ids": ["KB-ONE"]},
        ).status_code
        == 404
    )


def test_workspace_selector_is_membership_checked_and_idempotency_is_workspace_scoped(client):
    first_workspace = _identity(
        client, email="multi@example.test", workspace_name="multi-a", role="editor"
    )
    first_dataset = _dataset(client, "multi-a-data")
    payload = {
        "dataset_id": first_dataset,
        "model_name": "fixture",
        "prompt_version": "v1",
        "responses": [{"case_key": "case", "output": "answer"}],
    }
    first = client.post("/evaluations/jobs", headers={"Idempotency-Key": "shared"}, json=payload)
    assert first.status_code == 202

    with client.app.state.session_factory() as session:
        user = session.query(User).filter_by(subject="multi@example.test").one()
        second_workspace = Workspace(name="multi-b", slug="multi-b")
        session.add(second_workspace)
        session.flush()
        session.add(
            WorkspaceMembership(user_id=user.id, workspace_id=second_workspace.id, role="editor")
        )
        second_dataset = Dataset(
            name="multi-b-data", description="", workspace_id=second_workspace.id
        )
        session.add(second_dataset)
        session.flush()
        session.add(
            TestCase(
                dataset_id=second_dataset.id,
                key="case",
                input="question",
                expected_output="answer",
                content_hash="c" * 64,
            )
        )
        second_dataset_id = second_dataset.id
        session.commit()

    denied = client.get("/datasets", headers={"X-Workspace-ID": str(second_workspace.id + 100)})
    assert denied.status_code == 404
    second = client.post(
        "/evaluations/jobs",
        headers={
            "Idempotency-Key": "shared",
            "X-Workspace-ID": str(second_workspace.id),
        },
        json=payload | {"dataset_id": second_dataset_id},
    )
    assert second.status_code == 202, second.text
    assert second.json()["job_id"] != first.json()["job_id"]
    assert first_workspace != second_workspace.id
    worker = EvaluationJobWorker(client.app.state.session_factory)
    worker.run_once()
    worker.run_once()
    with client.app.state.session_factory() as session:
        jobs = [
            session.get(EvaluationJob, first.json()["job_id"]),
            session.get(EvaluationJob, second.json()["job_id"]),
        ]
        assert [job.status for job in jobs] == ["succeeded", "succeeded"]
        assert [job.workspace_id for job in jobs] == [first_workspace, second_workspace.id]
        runs = [session.get(EvaluationRun, job.run_id) for job in jobs]
        assert [run.workspace_id for run in runs] == [first_workspace, second_workspace.id]
