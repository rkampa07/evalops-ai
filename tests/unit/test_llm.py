"""Model tooling with the deterministic fixture backend (no MLX needed)."""

import json
from pathlib import Path

import pytest

from evalops.llm.backends import FixtureBackend, GenerationSettings, messages_key
from evalops.llm.judge import JUDGE_PROMPTS, judge_claim, judge_info, parse_verdict
from evalops.llm.rag_pipeline import PROMPTS, Retriever, build_messages, prompt_hash

KB = json.loads(
    (Path(__file__).resolve().parents[2] / "examples/rag/kestrel_policy_kb.json").read_text()
)


def test_retriever_is_deterministic_and_matches_recorded_runs():
    retriever = Retriever(KB["passages"], k=3)
    recorded = json.loads(
        (
            Path(__file__).resolve().parents[2] / "examples/rag/responses/qwen2.5-1.5b_rag-v1.json"
        ).read_text()
    )
    for response in recorded["responses"]:
        case = next(c for c in KB["test_cases"] if c["key"] == response["case_key"])
        got = [p["passage_id"] for p in retriever.retrieve(case["input"])]
        assert got == response["retrieved_passage_ids"]


def test_prompt_messages_include_passages_and_question():
    passages = KB["passages"][:2]
    msgs = build_messages("rag-v1", "Can I return it?", passages)
    assert msgs[0]["role"] == "system" and "ONLY" in msgs[0]["content"]
    assert "[KB-RET-01]" in msgs[1]["content"] and "Can I return it?" in msgs[1]["content"]
    assert prompt_hash("rag-v1") != prompt_hash("rag-v2")
    assert len(prompt_hash("rag-v1")) == 64


def test_prompt_hashes_match_recorded_generation_metadata():
    root = Path(__file__).resolve().parents[2] / "examples/rag/responses"
    for version in PROMPTS:
        record = json.loads((root / f"qwen2.5-1.5b_{version}.json").read_text())
        assert record["generation"]["prompt_hash"] == prompt_hash(version)


def test_fixture_backend_replays_by_prompt():
    msgs = build_messages("rag-v1", "q", KB["passages"][:1])
    backend = FixtureBackend("fixture/model", {messages_key(msgs): "recorded answer"})
    assert backend.generate(msgs, GenerationSettings()).text == "recorded answer"
    with pytest.raises(KeyError):
        backend.generate(build_messages("rag-v2", "q", KB["passages"][:1]), GenerationSettings())


@pytest.mark.parametrize(
    ("reply", "verdict"),
    [
        ("SUPPORTED", "supported"),
        ("unsupported.", "unsupported"),
        ("Answer: UNSUPPORTED", "unsupported"),
        ("I think it is fine", "error"),
        ("", "error"),
    ],
)
def test_parse_verdict(reply, verdict):
    assert parse_verdict(reply) == verdict


def test_judge_claim_with_fixture_backend():
    seen = []

    def reply(messages):
        seen.append(messages)
        return "UNSUPPORTED" if "45 days" in messages[1]["content"] else "SUPPORTED"

    backend = FixtureBackend("fixture/judge", reply)
    passages = [p for p in KB["passages"] if p["passage_id"] == "KB-RET-01"]
    assert judge_claim(backend, "Returns within 30 days.", passages)[0] == "supported"
    assert judge_claim(backend, "Returns within 45 days.", passages)[0] == "unsupported"
    assert "[KB-RET-01]" in seen[0][1]["content"]
    info = judge_info(backend)
    assert info["prompt_version"] == "judge-v2" and info["backend"] == "fixture"
    assert set(JUDGE_PROMPTS) == {"judge-v1", "judge-v2"}
