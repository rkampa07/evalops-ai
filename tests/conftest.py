from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evalops.config import Settings
from evalops.main import create_app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def load_example(relative: str) -> dict:
    return json.loads((EXAMPLES / relative).read_text())


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def client(db_url: str):
    settings = Settings(
        database_url=db_url,
        cors_origins=["http://localhost:5173"],
        auto_migrate=True,
        # TestClient sends Host: testserver
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
    )
    app = create_app(settings)
    # raise_server_exceptions=False so the 500 handler is exercised like in production
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def seeded(client: TestClient) -> dict:
    """Load the Kestrel dataset plus baseline / regressed / fixed runs via the public API."""
    ds_fixture = load_example("datasets/kestrel_support_v1.json")
    ds = client.post(
        "/datasets", json={"name": ds_fixture["name"], "description": ds_fixture["description"]}
    ).json()
    r = client.post(
        f"/datasets/{ds['id']}/test-cases/bulk", json={"test_cases": ds_fixture["test_cases"]}
    )
    assert r.status_code == 201, r.text

    runs = {}
    for name in ("baseline_prod_v1.3", "candidate_v1.4_regressed", "candidate_v1.5_fixed"):
        body = load_example(f"responses/{name}.json") | {"dataset_id": ds["id"]}
        resp = client.post("/evaluations/runs", json=body)
        assert resp.status_code == 201, resp.text
        runs[name] = resp.json()
    return {"dataset": ds, "runs": runs}
