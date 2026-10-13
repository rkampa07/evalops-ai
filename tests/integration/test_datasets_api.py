CASE = {"key": "c1", "input": "Q?", "expected_output": "A.", "tags": ["policy"]}


def make_dataset(client, name="ds"):
    r = client.post("/datasets", json={"name": name, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["database"] == "ok"


def test_dataset_crud(client):
    ds = make_dataset(client)
    assert ds["test_case_count"] == 0 and ds["run_count"] == 0
    assert client.get("/datasets").json()[0]["name"] == "ds"
    assert client.get(f"/datasets/{ds['id']}").status_code == 200

    r = client.patch(f"/datasets/{ds['id']}", json={"description": "updated"})
    assert (
        r.status_code == 200 and r.json()["description"] == "updated" and r.json()["name"] == "ds"
    )

    assert client.delete(f"/datasets/{ds['id']}").status_code == 204
    assert client.get(f"/datasets/{ds['id']}").status_code == 404


def test_dataset_name_conflicts(client):
    make_dataset(client, "a")
    other = make_dataset(client, "b")
    r = client.post("/datasets", json={"name": "a"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert client.patch(f"/datasets/{other['id']}", json={"name": "a"}).status_code == 409


def test_dataset_validation(client):
    r = client.post("/datasets", json={"name": "   "})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert client.post("/datasets", json={"name": "x", "unknown": 1}).status_code == 422
    assert client.post("/datasets", json={"name": "x" * 201}).status_code == 422


def test_test_case_crud(client):
    ds = make_dataset(client)
    r = client.post(f"/datasets/{ds['id']}/test-cases", json=CASE)
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["key"] == "c1" and case["required_terms"] == []

    assert len(client.get(f"/datasets/{ds['id']}/test-cases").json()) == 1
    assert client.get(f"/datasets/{ds['id']}").json()["test_case_count"] == 1

    r = client.patch(
        f"/test-cases/{case['id']}", json={"required_terms": ["A"], "max_latency_ms": 500}
    )
    assert r.status_code == 200 and r.json()["required_terms"] == ["A"]
    assert r.json()["expected_output"] == "A."

    assert client.delete(f"/test-cases/{case['id']}").status_code == 204
    assert client.get(f"/test-cases/{case['id']}").status_code == 404


def test_test_case_requires_a_check(client):
    ds = make_dataset(client)
    r = client.post(f"/datasets/{ds['id']}/test-cases", json={"key": "c", "input": "Q?"})
    assert r.status_code == 422 and "at least one check" in r.json()["error"]["message"]


def test_patch_cannot_remove_last_check(client):
    ds = make_dataset(client)
    case = client.post(f"/datasets/{ds['id']}/test-cases", json=CASE).json()
    r = client.patch(f"/test-cases/{case['id']}", json={"expected_output": None})
    assert r.status_code == 422 and "at least one check" in r.json()["error"]["message"]


def test_test_case_validation_rules(client):
    ds = make_dataset(client)
    url = f"/datasets/{ds['id']}/test-cases"
    assert client.post(url, json=CASE | {"key": "bad key!"}).status_code == 422
    assert client.post(url, json=CASE | {"json_schema": {"type": "nope"}}).status_code == 422
    assert client.post(url, json=CASE | {"max_latency_ms": -1}).status_code == 422
    r = client.post(url, json=CASE | {"required_terms": ["x"], "forbidden_terms": ["X"]})
    assert r.status_code == 422 and "both required and forbidden" in r.json()["error"]["message"]


def test_duplicate_case_keys(client):
    ds = make_dataset(client)
    url = f"/datasets/{ds['id']}/test-cases"
    client.post(url, json=CASE)
    assert client.post(url, json=CASE).status_code == 409
    r = client.post(f"{url}/bulk", json={"test_cases": [CASE | {"key": "x"}, CASE | {"key": "x"}]})
    assert r.status_code == 422


def test_bulk_create_is_atomic(client):
    ds = make_dataset(client)
    url = f"/datasets/{ds['id']}/test-cases"
    client.post(url, json=CASE)
    r = client.post(f"{url}/bulk", json={"test_cases": [CASE | {"key": "new"}, CASE]})
    assert r.status_code == 409
    keys = [c["key"] for c in client.get(url).json()]
    assert keys == ["c1"]  # "new" was not partially inserted


def test_terms_and_tags_deduplicated(client):
    ds = make_dataset(client)
    r = client.post(
        f"/datasets/{ds['id']}/test-cases",
        json=CASE | {"required_terms": ["a", "a", "b"], "tags": ["t", "t"]},
    )
    assert r.json()["required_terms"] == ["a", "b"] and r.json()["tags"] == ["t"]


def test_not_found_paths(client):
    assert client.get("/datasets/999").status_code == 404
    assert client.get("/datasets/999/test-cases").status_code == 404
    assert client.post("/datasets/999/test-cases", json=CASE).status_code == 404
    assert client.patch("/test-cases/999", json={"input": "x"}).status_code == 404
