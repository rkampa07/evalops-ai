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

    app = create_app(Settings(database_url=db_url, cors_origins=[], max_body_bytes=200))
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
