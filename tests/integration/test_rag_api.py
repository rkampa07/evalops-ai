"""RAG evaluation through the public API, using deterministic hand-written responses."""

import pytest

PASSAGES = [
    {"passage_id": "KB-RET-01", "title": "Returns", "text": "Most items can be returned within "
     "30 days of delivery for a full refund."},
    {"passage_id": "KB-RET-03", "title": "Return fees", "text": "Return shipping is free for "
     "orders over $75. For orders of $75 or less, a $9.95 fee is deducted from the refund."},
    {"passage_id": "KB-SHP-02", "title": "Shipping", "text": "We ship to Canada only. Canadian "
     "orders usually arrive in 7 to 12 business days."},
]  # fmt: skip
CASES = [
    {"key": "return-window", "input": "How long do I have to return an item?",
     "relevant_passage_ids": ["KB-RET-01"], "tags": ["rag"]},
    {"key": "return-fee", "input": "Do I pay return shipping on a $60 order?",
     "relevant_passage_ids": ["KB-RET-03"], "tags": ["rag"]},
    {"key": "gift-wrap", "input": "Do you offer gift wrapping?", "expect_abstain": True,
     "tags": ["rag"]},
]  # fmt: skip
RETRIEVED = {
    "return-window": ["KB-RET-01", "KB-RET-03", "KB-SHP-02"],
    "return-fee": ["KB-RET-01", "KB-RET-03", "KB-SHP-02"],
    "gift-wrap": ["KB-SHP-02", "KB-RET-01", "KB-RET-03"],
}
GOOD = {
    "return-window": "You can return most items within 30 days of delivery [KB-RET-01].",
    "return-fee": "Orders of $75 or less have a $9.95 return fee deducted from the refund "
    "[KB-RET-03].",
    "gift-wrap": "I don't have that information in our policies.",
}
BAD = {
    "return-window": "You can return items within 60 days of delivery [KB-RET-01].",
    "return-fee": "Return shipping is always free [KB-RET-09].",
    "gift-wrap": "Yes, we offer free gift wrapping on every order.",
}
JUDGE = {
    "model_id": "test/judge",
    "model_revision": "abc",
    "backend": "fixture",
    "prompt_version": "judge-v1",
    "prompt_hash": "h1",
    "settings": {"temperature": 0},
}


@pytest.fixture()
def rag_ds(client):
    ds = client.post("/datasets", json={"name": "rag"}).json()
    r = client.post(f"/datasets/{ds['id']}/passages", json={"passages": PASSAGES})
    assert r.status_code == 201, r.text
    r = client.post(f"/datasets/{ds['id']}/test-cases/bulk", json={"test_cases": CASES})
    assert r.status_code == 201, r.text
    return ds


def submit(client, ds_id, outputs, prompt="v1", config=None):
    body = {
        "dataset_id": ds_id,
        "model_name": "local/test-model",
        "prompt_version": prompt,
        "config": config or {},
        "generation": {"model_id": "local/test-model", "temperature": 0},
        "responses": [
            {
                "case_key": k,
                "output": v,
                "latency_ms": 500,
                "retrieved_passage_ids": RETRIEVED[k],
                "generation": {"completion_tokens": 20},
            }
            for k, v in outputs.items()
        ],
    }
    r = client.post("/evaluations/runs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def grades_for(run, verdict_fn):
    return {
        "judge": JUDGE,
        "grades": [
            {
                "case_key": res["case_key"],
                "claims": [
                    {"claim": c["text"], "verdict": verdict_fn(res["case_key"], c)}
                    for c in res["metrics"].get("claims", [])
                ],
            }
            for res in run["results"]
        ],
    }


# ---------------------------------------------------------------- knowledge base


def test_passages_crud_and_versioning(client, rag_ds):
    ds_id = rag_ds["id"]
    v = client.get(f"/datasets/{ds_id}").json()["version"]
    assert client.get(f"/datasets/{ds_id}").json()["passage_count"] == 3
    assert [p["passage_id"] for p in client.get(f"/datasets/{ds_id}/passages").json()] == [
        "KB-RET-01",
        "KB-RET-03",
        "KB-SHP-02",
    ]
    r = client.patch(f"/datasets/{ds_id}/passages/KB-SHP-02", json={"title": "Intl"})
    assert r.status_code == 200 and client.get(f"/datasets/{ds_id}").json()["version"] == v
    client.patch(f"/datasets/{ds_id}/passages/KB-SHP-02", json={"text": "No shipping."})
    assert client.get(f"/datasets/{ds_id}").json()["version"] == v + 1
    assert client.delete(f"/datasets/{ds_id}/passages/KB-SHP-02").status_code == 204
    assert client.get(f"/datasets/{ds_id}").json()["version"] == v + 2
    assert client.delete(f"/datasets/{ds_id}/passages/KB-SHP-02").status_code == 404


def test_passage_validation(client, rag_ds):
    url = f"/datasets/{rag_ds['id']}/passages"
    assert client.post(url, json={"passages": [PASSAGES[0]]}).status_code == 409
    bad_id = {"passage_id": "not an id", "text": "x"}
    assert client.post(url, json={"passages": [bad_id]}).status_code == 422
    dup = {"passage_id": "KB-NEW-01", "text": "x"}
    assert client.post(url, json={"passages": [dup, dup]}).status_code == 422


def test_rag_case_validation(client, rag_ds):
    url = f"/datasets/{rag_ds['id']}/test-cases"
    both = {"key": "x", "input": "q", "expect_abstain": True, "relevant_passage_ids": ["KB-RET-01"]}
    r = client.post(url, json=both)
    assert r.status_code == 422 and "abstention" in r.json()["error"]["message"]
    # RAG-only cases are valid checks on their own
    assert (
        client.post(url, json={"key": "y", "input": "q", "expect_abstain": True}).status_code == 201
    )


# ---------------------------------------------------------------- runs


def test_grounded_run_passes_with_rag_metrics(client, rag_ds):
    run = submit(client, rag_ds["id"], GOOD)
    s = run["summary"]
    assert s["passed_cases"] == 3, [r["failures"] for r in run["results"]]
    assert s["rag_cases"] == 3
    assert s["retrieval_recall_at_k"] == 1.0
    assert s["mrr"] == 0.75  # ranks 1 and 2
    assert s["citation_validity_rate"] == 1.0
    assert s["groundedness"] == 1.0 and s["unsupported_claim_count"] == 0
    assert s["abstention_accuracy"] == 1.0
    assert run["knowledge_base_hash"] and len(run["knowledge_base_hash"]) == 64
    assert run["generation"] == {"model_id": "local/test-model", "temperature": 0}
    res = {r["case_key"]: r for r in run["results"]}
    assert res["return-fee"]["retrieved_passage_ids"] == RETRIEVED["return-fee"]
    assert res["return-fee"]["generation"] == {"completion_tokens": 20}
    assert run["methodology_version"] == "2.0.0"


def test_ungrounded_run_fails_specific_checks(client, rag_ds):
    run = submit(client, rag_ds["id"], BAD)
    res = {r["case_key"]: {f["check"] for f in r["failures"]} for r in run["results"]}
    assert res["return-window"] == {"groundedness"}  # 60 days not in the passage
    assert "citations" in res["return-fee"]  # cites a passage that does not exist
    assert "abstention" in res["gift-wrap"]
    s = run["summary"]
    assert s["passed_cases"] == 0
    assert s["abstention_accuracy"] == pytest.approx(2 / 3)
    assert s["unsupported_claim_count"] >= 2


def test_rag_input_validation(client, rag_ds):
    body = {
        "dataset_id": rag_ds["id"],
        "model_name": "m",
        "prompt_version": "p",
        "responses": [
            {"case_key": "return-window", "output": "x", "retrieved_passage_ids": ["KB-NOPE-1"]}
        ],
    }
    r = client.post("/evaluations/runs", json=body)
    assert r.status_code == 422 and "unknown passage" in r.json()["error"]["message"]

    empty = client.post("/datasets", json={"name": "no-kb"}).json()
    client.post(
        f"/datasets/{empty['id']}/test-cases",
        json={"key": "k", "input": "q", "relevant_passage_ids": ["KB-RET-01"]},
    )
    body["dataset_id"] = empty["id"]
    body["responses"] = [{"case_key": "k", "output": "x"}]
    r = client.post("/evaluations/runs", json=body)
    assert r.status_code == 422 and "no knowledge-base passages" in r.json()["error"]["message"]


def test_oversized_generation_metadata_rejected(client, rag_ds):
    body = {
        "dataset_id": rag_ds["id"],
        "model_name": "m",
        "prompt_version": "p",
        "generation": {"blob": "x" * 20_000},
        "responses": [{"case_key": "gift-wrap", "output": "x"}],
    }
    assert client.post("/evaluations/runs", json=body).status_code == 422


# ---------------------------------------------------------------- model-based grades


def test_model_grades_are_stored_separately_as_estimates(client, rag_ds):
    run = submit(client, rag_ds["id"], BAD)
    before = run["summary"]
    r = client.put(
        f"/evaluations/runs/{run['id']}/model-grades",
        json=grades_for(run, lambda key, c: "unsupported"),
    )
    assert r.status_code == 200, r.text
    graded = r.json()
    mg = graded["model_graded"]
    assert mg["label"] == "estimated" and mg["judge"]["model_id"] == "test/judge"
    assert mg["estimated_groundedness"] == 0.0
    assert graded["summary"] == before  # deterministic summary untouched
    res = next(x for x in graded["results"] if x["case_key"] == "return-window")
    assert res["model_grades"]["label"] == "estimated"
    assert res["passed"] is False  # verdicts never change pass/fail


def test_model_grades_must_match_extracted_claims(client, rag_ds):
    run = submit(client, rag_ds["id"], GOOD)
    body = grades_for(run, lambda key, c: "supported")
    body["grades"][0]["claims"] = [{"claim": "something else", "verdict": "supported"}]
    r = client.put(f"/evaluations/runs/{run['id']}/model-grades", json=body)
    assert r.status_code == 422 and "do not match" in r.json()["error"]["message"]
    body = {"judge": JUDGE, "grades": [{"case_key": "nope", "claims": []}]}
    assert client.put(f"/evaluations/runs/{run['id']}/model-grades", json=body).status_code == 422


def test_estimated_gate_fails_closed_without_grades(client, rag_ds):
    run = submit(client, rag_ds["id"], GOOD)
    gate = {"candidate_run_id": run["id"], "gates": {"min_estimated_groundedness": 0.8}}
    g = client.post("/evaluations/gates", json=gate).json()
    assert g["decision"] == "fail" and "no model-based grades" in g["gates"][0]["message"]
    client.put(
        f"/evaluations/runs/{run['id']}/model-grades",
        json=grades_for(run, lambda key, c: "supported"),
    )
    assert client.post("/evaluations/gates", json=gate).json()["decision"] == "pass"


def test_rag_gates_and_default_report_policy(client, rag_ds):
    good, bad = submit(client, rag_ds["id"], GOOD, "v1"), submit(client, rag_ds["id"], BAD, "v2")
    gates = {"min_groundedness": 0.8, "min_citation_validity": 1.0, "min_abstention_accuracy": 1.0}
    assert (
        client.post(
            "/evaluations/gates", json={"candidate_run_id": good["id"], "gates": gates}
        ).json()["decision"]
        == "pass"
    )
    rep = client.get(
        f"/evaluations/runs/{bad['id']}/report", params={"baseline_run_id": good["id"]}
    ).json()
    assert rep["readiness"] == "not_ready"
    assert {"min_groundedness", "min_citation_validity", "min_abstention_accuracy"} <= set(
        rep["gate_policy"]
    )
    assert any("lexical heuristic" in n for n in rep["notes"])


def test_non_rag_report_policy_unchanged(client):
    ds = client.post("/datasets", json={"name": "plain"}).json()
    client.post(f"/datasets/{ds['id']}/test-cases", json={"key": "k", "input": "q",
                                                         "required_terms": ["x"]})  # fmt: skip
    run = client.post(
        "/evaluations/runs",
        json={
            "dataset_id": ds["id"],
            "model_name": "m",
            "prompt_version": "p",
            "responses": [{"case_key": "k", "output": "x"}],
        },  # fmt: skip
    ).json()
    rep = client.get(f"/evaluations/runs/{run['id']}/report").json()
    assert "min_groundedness" not in rep["gate_policy"]
    assert run["summary"]["rag_cases"] == 0 and run["knowledge_base_hash"] is None


# ---------------------------------------------------------------- comparison


def test_compare_detects_rag_regression(client, rag_ds):
    good, bad = submit(client, rag_ds["id"], GOOD, "v1"), submit(client, rag_ds["id"], BAD, "v2")
    c = client.get(
        "/evaluations/compare",
        params={"baseline_run_id": good["id"], "candidate_run_id": bad["id"]},
    ).json()
    assert c["compatible"] and c["has_regression"]
    statuses = {d["metric"]: d["status"] for d in c["metric_deltas"]}
    assert statuses["groundedness"] == "regressed"
    assert statuses["citation_validity_rate"] == "regressed"
    assert statuses["abstention_accuracy"] == "regressed"
    assert c["estimated_metric_deltas"] == []
    assert "need model-based grades" in c["estimated_note"]


def test_estimated_deltas_only_with_the_same_judge(client, rag_ds):
    good, bad = submit(client, rag_ds["id"], GOOD, "v1"), submit(client, rag_ds["id"], BAD, "v2")
    client.put(f"/evaluations/runs/{good['id']}/model-grades",
               json=grades_for(good, lambda k, c: "supported"))  # fmt: skip
    other_judge = grades_for(bad, lambda k, c: "unsupported") | {
        "judge": JUDGE | {"prompt_hash": "h2"}
    }
    client.put(f"/evaluations/runs/{bad['id']}/model-grades", json=other_judge)
    params = {"baseline_run_id": good["id"], "candidate_run_id": bad["id"]}
    c = client.get("/evaluations/compare", params=params).json()
    assert c["estimated_metric_deltas"] == [] and "different judges" in c["estimated_note"]

    client.put(f"/evaluations/runs/{bad['id']}/model-grades",
               json=grades_for(bad, lambda k, c: "unsupported"))  # fmt: skip
    c = client.get("/evaluations/compare", params=params).json()
    (d,) = c["estimated_metric_deltas"]
    assert d["metric"] == "estimated_groundedness" and d["status"] == "regressed"
    assert "not ground truth" in c["estimated_note"]


def test_changed_knowledge_base_makes_runs_incomparable(client, rag_ds):
    before = submit(client, rag_ds["id"], GOOD, "v1")
    client.patch(
        f"/datasets/{rag_ds['id']}/passages/KB-SHP-02", json={"text": "We ship worldwide."}
    )
    after = submit(client, rag_ds["id"], GOOD, "v1")
    c = client.get(
        "/evaluations/compare",
        params={"baseline_run_id": before["id"], "candidate_run_id": after["id"]},
    ).json()
    assert c["compatible"] is False and c["has_regression"] is False
    assert any(i["kind"] == "knowledge_base" for i in c["compatibility_issues"])
