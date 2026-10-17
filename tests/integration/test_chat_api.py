"""Live chat API with fixture inference (no MLX)."""

import json
import logging
from pathlib import Path

import pytest

from evalops.chat.runtime import ChatRuntime, fixture_reply
from evalops.llm.backends import FixtureBackend
from evalops.llm.rag_pipeline import Retriever

KB = json.loads(
    (Path(__file__).resolve().parents[2] / "examples/rag/kestrel_policy_kb.json").read_text()
)


def use_backend(client, reply=fixture_reply):
    client.app.state.chat_runtime = ChatRuntime(
        "fixture", "test/scripted", backend=FixtureBackend("test/scripted", reply)
    )


@pytest.fixture()
def kb_dataset(client):
    ds = client.post("/datasets", json={"name": "chat-kb"}).json()
    assert (
        client.post(f"/datasets/{ds['id']}/passages", json={"passages": KB["passages"]}).status_code
        == 201
    )
    return ds


@pytest.fixture()
def chat(client, kb_dataset):
    use_backend(client)
    r = client.post("/chat/sessions", json={"dataset_id": kb_dataset["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def send(client, chat, text):
    r = client.post(f"/chat/sessions/{chat['id']}/messages", json={"content": text})
    assert r.status_code == 201, r.text
    return r.json()


def test_chat_is_disabled_by_default_and_persists_nothing(client, kb_dataset):
    assert client.get("/chat/status").json()["enabled"] is False
    chat = client.post("/chat/sessions", json={"dataset_id": kb_dataset["id"]}).json()
    r = client.post(f"/chat/sessions/{chat['id']}/messages", json={"content": "Hello?"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "chat_disabled"
    assert client.get(f"/chat/sessions/{chat['id']}").json()["messages"] == []


def test_session_validation(client, kb_dataset):
    no_kb = client.post("/datasets", json={"name": "empty"}).json()
    assert client.post("/chat/sessions", json={"dataset_id": no_kb["id"]}).status_code == 422
    r = client.post("/chat/sessions", json={"dataset_id": kb_dataset["id"], "prompt_version": "x"})
    assert r.status_code == 422
    assert client.post("/chat/sessions", json={"dataset_id": 999}).status_code == 404


def test_full_turn_returns_a_complete_execution_trace(client, chat):
    turn = send(client, chat, "How long do I have to return an item?")
    reply = turn["assistant_message"]
    t = reply["trace"]
    assert reply["content"].startswith("Most items can be returned within 30 days")
    r = t["retrieval"]
    assert r["query"] == "How long do I have to return an item?" and r["retriever"] == "bm25"
    assert len(r["passages"]) == 3 and {"passage_id", "text", "bm25_score"} <= set(r["passages"][0])
    assert r["passages"][0]["bm25_score"] >= r["passages"][1]["bm25_score"]
    assert t["prompt"]["messages"][-1]["role"] == "user" and len(t["prompt"]["hash"]) == 64
    assert t["model"]["model_id"] == "test/scripted" and t["settings"]["max_tokens"] == 256
    assert "latency_ms" in t["generation"]
    check = t["live_check"]
    assert "Not verified semantic correctness" in check["label"]
    assert check["methodology_version"] == "2.0.0" and check["passed_lexical_checks"] is True
    assert check["abstention"].startswith("not graded")
    assert t["judge"]["status"].startswith("not run")
    assert turn["session"]["title"].startswith("How long do I have")
    assert turn["session"]["message_count"] == 2


def test_cached_retrieval_preserves_trace_and_reuses_index(client, chat):
    question = "How long do I have to return an item?"
    first = send(client, chat, question)["assistant_message"]["trace"]["retrieval"]
    cache = client.app.state.retriever_cache
    hits_before = cache.stats()["hits"]
    second = send(client, chat, question)["assistant_message"]["trace"]["retrieval"]

    passages = KB["passages"]
    reference = Retriever(passages, k=3)
    reference_rows, reference_scores = reference.retrieve_with_scores(question)
    expected = [
        p | {"bm25_score": round(reference_scores[p["passage_id"]], 4)} for p in reference_rows
    ]
    assert first["passages"] == expected
    assert second["passages"] == expected
    assert first["query"] == second["query"] == question
    assert cache.stats()["hits"] == hits_before + 1


def test_cached_index_is_invalidated_after_passage_update(client, chat, kb_dataset):
    question = "How long do I have to return an item?"
    send(client, chat, question)
    cache = client.app.state.retriever_cache
    misses_before = cache.stats()["misses"]
    invalidations_before = cache.stats()["invalidations"]

    update = client.patch(
        f"/datasets/{kb_dataset['id']}/passages/KB-RET-01",
        json={"title": "Special revised returns policy"},
    )
    assert update.status_code == 200, update.text
    send(client, chat, question)

    assert cache.stats()["misses"] == misses_before + 1
    assert cache.stats()["invalidations"] == invalidations_before + 1
    assert cache.stats()["entries"] == 1


def test_follow_up_uses_previous_turn_and_history(client, chat):
    send(client, chat, "How long does shipping to Canada take?")
    t = send(client, chat, "What about the US?")["assistant_message"]["trace"]
    assert t["retrieval"]["used_previous_turn"] is True
    assert t["retrieval"]["query"] == "How long does shipping to Canada take? What about the US?"
    roles = [m["role"] for m in t["prompt"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert t["prompt"]["history_exchanges_included"] == 1


def test_earlier_answers_are_never_evidence(client, chat):
    replies = iter(["Most items can be returned within 30 days [KB-RET-01].",
                    "Most items can be returned within 30 days [KB-RET-01]."])  # fmt: skip
    use_backend(client, lambda messages: next(replies))
    first = send(client, chat, "How long do I have to return an item?")["assistant_message"]
    assert first["trace"]["live_check"]["passed_lexical_checks"] is True
    # A turn whose retrieval does not include KB-RET-01 cannot reuse the earlier answer.
    second = send(client, chat, "Do you ship white-glove furniture deliveries?")[
        "assistant_message"
    ]
    check = second["trace"]["live_check"]
    retrieved = [p["passage_id"] for p in second["trace"]["retrieval"]["passages"]]
    assert "KB-RET-01" not in retrieved
    assert check["passed_lexical_checks"] is False
    assert {f["check"] for f in check["failures"]} >= {"citations"}
    assert check["citations_not_retrieved"] == ["KB-RET-01"]


def test_history_is_bounded(client, chat):
    for i in range(5):
        turn = send(client, chat, f"Question number {i} about furniture warranty coverage details?")
    prompt = turn["assistant_message"]["trace"]["prompt"]
    assert prompt["history_exchanges_included"] == 3 and prompt["history_truncated"] is True
    assert len(prompt["messages"]) == 1 + 3 * 2 + 1


def test_busy_returns_429_and_persists_nothing(client, chat):
    runtime = client.app.state.chat_runtime
    runtime._lock.acquire()
    try:
        r = client.post(f"/chat/sessions/{chat['id']}/messages", json={"content": "Hi there?"})
        assert r.status_code == 429 and r.json()["error"]["code"] == "busy"
        assert client.get("/chat/status").json()["busy"] is True
    finally:
        runtime._lock.release()
    assert client.get(f"/chat/sessions/{chat['id']}").json()["messages"] == []


def test_generation_error_persists_nothing_and_does_not_leak(client, chat):
    def boom(messages):
        raise RuntimeError("secret internal detail")

    use_backend(client, boom)
    r = client.post(f"/chat/sessions/{chat['id']}/messages", json={"content": "Hi there?"})
    assert r.status_code == 500 and "secret" not in r.text
    assert client.get(f"/chat/sessions/{chat['id']}").json()["messages"] == []


def test_chat_is_separate_from_runs_and_gates(client, chat):
    send(client, chat, "How long do I have to return an item?")
    assert client.get("/evaluations/runs").json()["total"] == 0


def test_message_length_limit(client, chat):
    r = client.post(f"/chat/sessions/{chat['id']}/messages", json={"content": "x" * 2001})
    assert r.status_code == 422


def test_save_test_case_requires_human_labels(client, chat, kb_dataset):
    reply = send(client, chat, "How long do I have to return an item?")["assistant_message"]
    url = f"/chat/messages/{reply['id']}/test-case"
    assert client.post(url, json={"key": "c1"}).status_code == 422  # no gold, not should-decline
    bad = {"key": "c1", "relevant_passage_ids": ["KB-NOPE-1"]}
    assert client.post(url, json=bad).status_code == 422
    both = {"key": "c1", "relevant_passage_ids": ["KB-RET-01"], "expect_abstain": True}
    assert client.post(url, json=both).status_code == 422

    version_before = client.get(f"/datasets/{kb_dataset['id']}").json()["version"]
    r = client.post(url, json={"key": "chat-return-window", "relevant_passage_ids": ["KB-RET-01"]})
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["input"] == "How long do I have to return an item?"
    assert case["expected_output"] is None  # the model's answer is never copied in
    assert case["relevant_passage_ids"] == ["KB-RET-01"] and "chat-capture" in case["tags"]
    target = client.get(f"/datasets/{case['dataset_id']}").json()
    assert target["name"] == "chat-kb-chat-captures" and target["passage_count"] == 20
    assert client.get(f"/datasets/{kb_dataset['id']}").json()["version"] == version_before
    saved = client.get(f"/chat/sessions/{chat['id']}").json()["messages"][1]
    assert saved["saved_test_case_id"] == case["id"]


def test_save_should_decline_case_with_standalone_input(client, chat):
    reply = send(client, chat, "Do you offer gift wrapping?")["assistant_message"]
    r = client.post(
        f"/chat/messages/{reply['id']}/test-case",
        json={"key": "chat-gift-wrap", "expect_abstain": True, "input": "Do you gift wrap?"},
    )
    assert r.status_code == 201 and r.json()["expect_abstain"] is True
    assert r.json()["input"] == "Do you gift wrap?"
    user_msg = client.get(f"/chat/sessions/{chat['id']}").json()["messages"][0]
    r = client.post(
        f"/chat/messages/{user_msg['id']}/test-case", json={"key": "x", "expect_abstain": True}
    )
    assert r.status_code == 404  # only assistant replies can be saved


def test_sessions_list_and_delete(client, chat):
    send(client, chat, "How long do I have to return an item?")
    assert [s["id"] for s in client.get("/chat/sessions").json()] == [chat["id"]]
    assert client.delete(f"/chat/sessions/{chat['id']}").status_code == 204
    assert client.get(f"/chat/sessions/{chat['id']}").status_code == 404


def test_message_text_and_prompts_are_not_logged(client, chat, caplog):
    secret = "my card number is 4111 1111 1111 1111"
    with caplog.at_level(logging.DEBUG):
        send(client, chat, f"Can I pay here? {secret}")
    assert secret not in caplog.text and "Policy passages" not in caplog.text


def test_diagnostics_are_separate_and_backfilled_on_read(client, chat):
    use_backend(client, lambda m: "Yes, custom-made furniture can be returned [KB-RET-02].")
    turn = send(client, chat, "Can I return a custom-made sofa?")
    trace = turn["assistant_message"]["trace"]
    assert trace["live_check"]["passed_lexical_checks"] is True  # 2.0.0 check: unchanged
    kinds = [w["kind"] for w in trace["diagnostics"]["warnings"]]
    assert kinds == ["policy_polarity"]
    assert turn["assistant_message"]["content"].startswith("Yes")  # raw output untouched

    # A reply stored before diagnostics existed gets them computed on read, not written back.
    from evalops.models import ChatMessage

    with client.app.state.session_factory() as s:
        message = s.get(ChatMessage, turn["assistant_message"]["id"])
        message.trace = {k: v for k, v in message.trace.items() if k != "diagnostics"}
        s.commit()
    shown = client.get(f"/chat/sessions/{chat['id']}").json()["messages"][-1]["trace"]
    assert shown["diagnostics"]["computed_on_read"] is True
    assert [w["kind"] for w in shown["diagnostics"]["warnings"]] == ["policy_polarity"]
    with client.app.state.session_factory() as s:
        assert "diagnostics" not in s.get(ChatMessage, turn["assistant_message"]["id"]).trace
