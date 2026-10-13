from fastapi import APIRouter

from evalops.config import Settings


def test_cors_allows_configured_origin(client):
    r = client.options(
        "/datasets",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_unknown_origin(client):
    r = client.options(
        "/datasets",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_wildcard_origin_is_dropped(monkeypatch):
    monkeypatch.setenv("EVALOPS_CORS_ORIGINS", "*,http://localhost:5173")
    assert Settings.from_env().cors_origins == ["http://localhost:5173"]


def test_unhandled_errors_do_not_leak_details(client):
    router = APIRouter()

    @router.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail: SELECT * FROM users")

    client.app.include_router(router)
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "internal_error", "message": "an internal error occurred"}
    }
    assert "secret" not in r.text


def test_validation_errors_do_not_echo_input(client):
    secret = "sk-very-secret-value"
    r = client.post("/datasets", json={"name": "x", "description": 123, "extra": secret})
    assert r.status_code == 422
    assert secret not in r.text
    assert "details" in r.json()["error"]


def test_unknown_route_uses_error_envelope(client):
    r = client.get("/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "http_error"


def test_malformed_json_body(client):
    r = client.post("/datasets", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_request_body_size_limit(db_url):
    from fastapi.testclient import TestClient

    from evalops.main import create_app

    app = create_app(
        Settings(
            database_url=db_url, cors_origins=[], max_body_bytes=200, allowed_hosts=["testserver"]
        )
    )
    with TestClient(app) as c:
        r = c.post("/datasets", json={"name": "x", "description": "y" * 500})
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
        assert c.post("/datasets", json={"name": "ok"}).status_code == 201


def test_oversized_output_rejected(client, seeded):
    body = {
        "dataset_id": seeded["dataset"]["id"],
        "model_name": "m",
        "prompt_version": "p",
        "responses": [{"case_key": "latency-greeting", "output": "x" * 100_001}],
    }
    assert client.post("/evaluations/runs", json=body).status_code == 422


# ---------------------------------------------------------------- trusted hosts


def test_untrusted_host_header_rejected(client):
    r = client.get("/health", headers={"Host": "attacker.example"})
    assert r.status_code == 400
    r = client.post("/datasets", json={"name": "x"}, headers={"Host": "rebind.attacker.example"})
    assert r.status_code == 400
    assert client.get("/datasets").json() == []  # nothing was created


def test_trusted_host_with_port_accepted(client):
    assert client.get("/health", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/health", headers={"Host": "localhost:8000"}).status_code == 200


def test_allowed_hosts_from_env_drop_wildcard(monkeypatch):
    monkeypatch.setenv("EVALOPS_ALLOWED_HOSTS", "*, api.internal ,localhost")
    assert Settings.from_env().allowed_hosts == ["api.internal", "localhost"]
    monkeypatch.delenv("EVALOPS_ALLOWED_HOSTS")
    assert Settings.from_env().allowed_hosts == ["localhost", "127.0.0.1"]


# ---------------------------------------------------------------- regex safety (API level)

EVIL = {"type": "string", "pattern": "^(a|a)*$"}  # exponential backtracking in Python `re`


def test_catastrophic_pattern_cannot_stall_a_run(client):
    import json
    import time

    ds = client.post("/datasets", json={"name": "redos"}).json()
    r = client.post(
        f"/datasets/{ds['id']}/test-cases", json={"key": "k", "input": "q", "json_schema": EVIL}
    )
    assert r.status_code == 201  # the pattern is a valid regex; it is dangerous only at runtime
    start = time.perf_counter()
    run = client.post(
        "/evaluations/runs",
        json={
            "dataset_id": ds["id"],
            "model_name": "m",
            "prompt_version": "p",
            "responses": [{"case_key": "k", "output": json.dumps("a" * 40 + "!")}],
        },
    )
    elapsed = time.perf_counter() - start
    assert run.status_code == 201
    assert elapsed < 3, f"run took {elapsed:.1f}s"
    result = run.json()["results"][0]
    assert result["passed"] is False
    assert "evaluation limit" in result["failures"][0]["message"]


def test_unsafe_or_invalid_schemas_rejected_at_save_time(client):
    ds = client.post("/datasets", json={"name": "schemas"}).json()
    url = f"/datasets/{ds['id']}/test-cases"

    def post(schema):
        return client.post(url, json={"key": "k", "input": "q", "json_schema": schema})

    r = post({"type": "object", "patternProperties": {"^x": {"type": "string"}}})
    assert r.status_code == 422 and "patternProperties" in r.json()["error"]["message"]
    r = post({"type": "string", "pattern": "(unclosed"})
    assert r.status_code == 422 and "regex" in r.json()["error"]["message"]
    r = post({"type": "string", "pattern": "a" * 1001})
    assert r.status_code == 422 and "longer than" in r.json()["error"]["message"]
