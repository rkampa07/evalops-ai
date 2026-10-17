FRONTEND_ORIGIN = "http://localhost:5173"


def _dataset(client) -> int:
    response = client.post("/datasets", json={"name": "cors-job-dataset", "description": ""})
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
                }
            ]
        },
    )
    assert cases.status_code == 201, cases.text
    return dataset_id


def _run_body(dataset_id: int) -> dict:
    return {
        "dataset_id": dataset_id,
        "model_name": "cors-test-model",
        "prompt_version": "v1",
        "responses": [{"case_key": "case-1", "output": "answer"}],
    }


def test_evaluation_job_preflight_allows_idempotency_key_from_frontend(client):
    response = client.options(
        "/evaluations/jobs",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "content-type,idempotency-key,x-csrf-token,x-workspace-id"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = {
        header.strip().lower()
        for header in response.headers["access-control-allow-headers"].split(",")
    }
    assert "content-type" in allowed_headers
    assert "idempotency-key" in allowed_headers
    assert "x-csrf-token" in allowed_headers
    assert "x-workspace-id" in allowed_headers
    assert response.headers["access-control-allow-credentials"] == "true"


def test_evaluation_job_post_is_exposed_to_allowed_frontend_origin(client):
    dataset_id = _dataset(client)
    response = client.post(
        "/evaluations/jobs",
        json=_run_body(dataset_id),
        headers={"Origin": FRONTEND_ORIGIN, "Idempotency-Key": "cors-job-key"},
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_disallowed_origin_does_not_receive_cors_access(client):
    response = client.options(
        "/evaluations/jobs",
        headers={
            "Origin": "https://unapproved.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,idempotency-key",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_existing_routes_keep_their_explicit_cors_behavior(client):
    health = client.get("/health", headers={"Origin": FRONTEND_ORIGIN})
    assert health.status_code == 200
    assert health.headers["access-control-allow-origin"] == FRONTEND_ORIGIN

    run_preflight = client.options(
        "/evaluations/runs",
        headers={
            "Origin": FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert run_preflight.status_code == 200
    assert run_preflight.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
