from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event


def _seed(client: TestClient, size: int = 10) -> tuple[int, list[str]]:
    response = client.post("/datasets", json={"name": "paged-main", "description": "page test"})
    assert response.status_code == 201
    dataset_id = response.json()["id"]
    for index in range(1, 8):
        response = client.post(
            "/datasets", json={"name": f"paged-extra-{index:02}", "description": "page test"}
        )
        assert response.status_code == 201

    case_keys = [f"case-{index:03}" for index in range(size)]
    cases = [
        {"key": key, "input": f"question {index}", "expected_output": "answer"}
        for index, key in enumerate(case_keys)
    ]
    response = client.post(f"/datasets/{dataset_id}/test-cases/bulk", json={"test_cases": cases})
    assert response.status_code == 201, response.text

    passages = [
        {"passage_id": f"P-{index:03}", "title": f"Passage {index}", "text": f"Evidence {index}."}
        for index in range(size)
    ]
    response = client.post(f"/datasets/{dataset_id}/passages", json={"passages": passages})
    assert response.status_code == 201, response.text

    for index in range(8):
        response = client.post(
            "/chat/sessions",
            json={
                "dataset_id": dataset_id,
                "prompt_version": "rag-v1",
                "title": f"session-{index:02}",
            },
        )
        assert response.status_code == 201, response.text

    response = client.post(
        "/evaluations/runs",
        json={
            "dataset_id": dataset_id,
            "model_name": "synthetic",
            "prompt_version": "pagination-test",
            "responses": [
                {"case_key": key, "output": "answer" if index % 2 == 0 else "wrong"}
                for index, key in enumerate(case_keys)
            ],
        },
    )
    assert response.status_code == 201, response.text
    return dataset_id, case_keys


def _assert_all_pages(
    client: TestClient,
    url: str,
    expected_total: int,
    item_key: Callable[[dict], object],
    full_response: list[dict],
) -> None:
    expected = [item_key(item) for item in full_response]
    seen = []
    for offset in range(0, expected_total, 3):
        response = client.get(url, params={"limit": 3, "offset": offset})
        assert response.status_code == 200, response.text
        page = response.json()
        assert client.get(url, params={"limit": 3, "offset": offset}).json() == page
        assert page["total"] == expected_total
        assert page["limit"] == 3
        assert page["offset"] == offset
        assert len(page["items"]) <= 3
        seen.extend(item_key(item) for item in page["items"])
    assert seen == expected
    assert len(seen) == len(set(seen))

    empty = client.get(url, params={"limit": 3, "offset": expected_total + 5}).json()
    assert empty["items"] == []
    assert empty["total"] == expected_total


def test_paginated_lists_are_opt_in_and_cover_all_pages(client: TestClient) -> None:
    dataset_id, case_keys = _seed(client)

    # Omitted pagination preserves each existing list response as a complete JSON array.
    datasets = client.get("/datasets").json()
    assert isinstance(datasets, list)
    _assert_all_pages(client, "/datasets", len(datasets), lambda row: row["id"], datasets)

    cases_url = f"/datasets/{dataset_id}/test-cases"
    cases = client.get(cases_url).json()
    assert isinstance(cases, list)
    assert [row["key"] for row in cases] == case_keys
    _assert_all_pages(client, cases_url, len(cases), lambda row: row["id"], cases)

    passages_url = f"/datasets/{dataset_id}/passages"
    passages = client.get(passages_url).json()
    assert isinstance(passages, list)
    _assert_all_pages(client, passages_url, len(passages), lambda row: row["passage_id"], passages)

    sessions = client.get("/chat/sessions").json()
    assert isinstance(sessions, list)
    _assert_all_pages(client, "/chat/sessions", len(sessions), lambda row: row["id"], sessions)

    run = client.get("/evaluations/runs").json()["items"][0]
    result_url = f"/evaluations/runs/{run['id']}/results"
    results = client.get(result_url, params={"limit": 200}).json()
    assert results["total"] == len(case_keys)
    assert [row["case_key"] for row in results["items"]] == case_keys
    _assert_all_pages(
        client,
        result_url,
        len(case_keys),
        lambda row: row["case_key"],
        results["items"],
    )

    # The legacy run-detail endpoint still returns its complete results field.
    detail = client.get(f"/evaluations/runs/{run['id']}").json()
    assert len(detail["results"]) == len(case_keys)
    summary = client.get(f"/evaluations/runs/{run['id']}/summary").json()
    assert "results" not in summary

    failures = client.get(result_url, params={"limit": 3, "failed_only": "true"}).json()
    assert failures["total"] == len(case_keys) // 2
    assert [row["case_key"] for row in failures["items"]] == case_keys[1::2][:3]


def test_empty_paginated_collections_return_empty_items_and_zero_total(client: TestClient) -> None:
    for url in ("/datasets", "/chat/sessions"):
        page = client.get(url, params={"limit": 3}).json()
        assert page["items"] == []
        assert page["total"] == 0

    dataset = client.post("/datasets", json={"name": "empty-pages"}).json()
    for suffix in ("test-cases", "passages"):
        page = client.get(f"/datasets/{dataset['id']}/{suffix}", params={"limit": 3}).json()
        assert page["items"] == []
        assert page["total"] == 0


@pytest.mark.parametrize(
    "url",
    [
        "/datasets",
        "/chat/sessions",
        "/datasets/1/test-cases",
        "/datasets/1/passages",
        "/evaluations/runs/1/results",
    ],
)
@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_invalid_pagination_parameters_return_422(
    client: TestClient, url: str, params: dict[str, int]
) -> None:
    assert client.get(url, params=params).status_code == 422


def test_paged_endpoints_apply_limit_in_sql_with_bounded_query_counts(client: TestClient) -> None:
    dataset_id, _ = _seed(client)
    run_id = client.get("/evaluations/runs").json()["items"][0]["id"]
    engine = client.app.state.engine

    cases = [
        (f"/datasets/{dataset_id}/test-cases?limit=3&offset=0", 3, "test_cases"),
        (f"/datasets/{dataset_id}/passages?limit=3&offset=0", 3, "passages"),
        ("/chat/sessions?limit=3&offset=0", 3, "chat_sessions"),
        (f"/evaluations/runs/{run_id}/results?limit=3&offset=0", 3, "evaluation_results"),
    ]
    for path, expected_queries, table in cases:
        statements: list[str] = []

        def capture(
            _conn,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
            statements=statements,
        ):
            statements.append(statement.lower())

        event.listen(engine, "before_cursor_execute", capture)
        try:
            response = client.get(path)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        assert response.status_code == 200
        assert len(response.json()["items"]) <= 3
        domain_statements = [
            statement for statement in statements if "api_rate_limit_buckets" not in statement
        ]
        assert len(statements) - len(domain_statements) == 2
        assert len(domain_statements) == expected_queries
        assert any(table in statement and "limit" in statement for statement in domain_statements)

    # Dataset page performs one row query, three scoped aggregates, and one total count.
    statements = []

    def capture_dataset(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_dataset)
    try:
        response = client.get("/datasets?limit=3&offset=0")
    finally:
        event.remove(engine, "before_cursor_execute", capture_dataset)
    assert response.status_code == 200
    domain_statements = [
        statement for statement in statements if "api_rate_limit_buckets" not in statement
    ]
    assert len(statements) - len(domain_statements) == 2
    assert len(domain_statements) == 5
    assert dataset_id > 0
