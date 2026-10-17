import json
from pathlib import Path

import pytest

from evalops.chat.prompting import (
    HISTORY_NOTE,
    Exchange,
    build_chat_messages,
    is_follow_up,
    retrieval_query,
    select_history,
)
from evalops.chat.runtime import ChatRuntime, fixture_reply
from evalops.errors import BusyError, ChatDisabledError
from evalops.llm.backends import FixtureBackend, GenerationSettings
from evalops.llm.rag_pipeline import PROMPTS, build_messages, prompt_hash

PREVIOUS = Exchange("How long does shipping to Canada take?", "7 to 12 business days [KB-SHP-02].")
PASSAGES = [{"passage_id": "KB-SHP-01", "title": "Domestic", "text": "US orders take 3 to 7 days."}]


@pytest.mark.parametrize(
    "question",
    ["What about the US?", "How much does it cost?", "And if I live there?", "Is that free?",
     "Canada?", "What about Vancouver?", "Does that apply to sale items?", "Why?"],
)  # fmt: skip
def test_ambiguous_follow_ups_use_the_previous_turn(question):
    assert is_follow_up(question)
    query, used = retrieval_query(question, [PREVIOUS])
    assert used and query.startswith(PREVIOUS.user) and query.endswith(question)


@pytest.mark.parametrize(
    "question",
    ["Can I return a mattress after opening the packaging?",
     # Live-chat regression: this inherited an earlier custom-sofa question, and the returns
     # passages outranked the shipping passage.
     "What is your shipping policy for Canada?",
     "Can I return a custom-made sofa within 30 days?",
     "Shipping to Vancouver?",
     "What is the return window for items that are on sale?"],
)  # fmt: skip
def test_questions_naming_their_own_topic_are_not_combined(question):
    assert not is_follow_up(question)
    sofa = Exchange("Can I return a custom-made sofa within 30 days?", "No [KB-RET-02].")
    assert retrieval_query(question, [sofa]) == (question, False)


def test_follow_up_without_history_is_used_as_is():
    assert retrieval_query("What about it?", []) == ("What about it?", False)


def test_history_is_bounded_by_exchanges_and_characters():
    history = [Exchange(f"q{i}", f"a{i}") for i in range(6)]
    kept, truncated = select_history(history, max_exchanges=3, char_budget=10_000)
    assert [e.user for e in kept] == ["q3", "q4", "q5"] and truncated
    long = [Exchange("x" * 400, "y" * 400), Exchange("short", "reply")]
    kept, truncated = select_history(long, max_exchanges=3, char_budget=100)
    assert [e.user for e in kept] == ["short"] and truncated
    assert select_history([], 3, 100) == ([], False)


def test_messages_put_passages_only_in_the_latest_turn():
    messages = build_chat_messages("rag-v1", [PREVIOUS], "What about the US?", PASSAGES)
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    # The note precedes the template so the citation rules stay last (chat-v2).
    assert messages[0]["content"] == f"{HISTORY_NOTE}\n\n{PROMPTS['rag-v1']['system']}"
    assert messages[1]["content"] == PREVIOUS.user and messages[2]["content"] == PREVIOUS.assistant
    assert (
        "[KB-SHP-01]" in messages[-1]["content"] and "What about the US?" in messages[-1]["content"]
    )
    assert "[KB-SHP-01]" not in messages[1]["content"] + messages[2]["content"]


@pytest.mark.parametrize("version", ["rag-v1", "rag-v3", "rag-v5"])
def test_first_turn_matches_the_batch_prompt_exactly(version):
    question = "How long does shipping take?"
    assert build_chat_messages(version, [], question, PASSAGES) == build_messages(
        version, question, PASSAGES
    )


def test_recorded_prompt_hashes_are_unchanged():
    responses = Path(__file__).resolve().parents[2] / "examples/rag/responses"
    for version in ("rag-v1", "rag-v3", "rag-v5"):
        record = json.loads((responses / f"qwen2.5-1.5b_{version}.json").read_text())
        assert record["generation"]["prompt_hash"] == prompt_hash(version)


def test_fixture_reply_quotes_and_cites_top_passage():
    messages = build_chat_messages("rag-v1", [], "Shipping?", PASSAGES)
    assert fixture_reply(messages) == "US orders take 3 to 7 days. [KB-SHP-01]"


def test_runtime_disabled_and_busy():
    with pytest.raises(ChatDisabledError):
        ChatRuntime("off", "m").generate([], GenerationSettings())
    runtime = ChatRuntime("fixture", "m", backend=FixtureBackend("f", lambda m: "ok"))
    runtime._lock.acquire()
    try:
        with pytest.raises(BusyError):
            runtime.generate([{"role": "user", "content": "x"}], GenerationSettings())
        assert runtime.status()["busy"] is True
    finally:
        runtime._lock.release()
    generation, info = runtime.generate([{"role": "user", "content": "x"}], GenerationSettings())
    assert generation.text == "ok" and info["backend"] == "fixture"


def test_mlx_is_not_imported_until_first_generation():
    import subprocess
    import sys

    code = (
        "import sys; from evalops.main import create_app; from evalops.config import Settings; "
        "app = create_app(Settings(chat_backend='mlx')); "
        "print(app.state.chat_runtime.status()['loaded'], "
        "any(m.startswith(('mlx', 'mlx_lm')) for m in sys.modules))"
    )
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=root / "backend", capture_output=True, text=True,
        check=True,
    ).stdout.split()  # fmt: skip
    assert out == ["False", "False"]  # app with chat=mlx starts without importing MLX


def test_status_names_the_backend_that_will_answer():
    assert ChatRuntime("off", "m").status()["model_id"] is None
    assert ChatRuntime("fixture", "m").status()["model_id"] == "fixture/echo-top-passage"
    mlx = ChatRuntime("mlx", "some/model").status()
    assert mlx["model_id"] == "some/model" and mlx["loaded"] is False
