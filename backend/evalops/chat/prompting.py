"""Pure functions: retrieval query composition and multi-turn prompt construction.

The existing prompt templates (llm/rag_pipeline.PROMPTS) are reused unchanged so recorded prompt
hashes stay valid; chat adds one fixed instruction about history, versioned separately.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from ..engine.rag import content_tokens
from ..llm.rag_pipeline import PROMPTS, format_passages

# chat-v2: the history note comes BEFORE the template's rules and only when there is history.
# chat-v1 appended it after the rules, and measured citation compliance dropped (docs/chat.md).
CHAT_PROMPT_VERSION = "chat-v2"
HISTORY_NOTE = (
    "This is a multi-turn conversation. Earlier assistant replies are not evidence: answer only "
    "from the policy passages in the latest message, and use earlier turns only to understand "
    "what the customer is referring to."
)

# A question with a reference ("what about…", "it", "that") or with no topic of its own
# ("Why?", "Canada?") depends on the previous user turn, so that turn is added to the retrieval
# query. Questions that name enough topic words of their own ("What is your shipping policy for
# Canada?") stand alone even when short, so an unrelated earlier topic can't skew retrieval.
_REFERENCE_RE = re.compile(
    r"\b(it|its|that|this|these|those|they|them|there|same|what about|how about|and if|instead)\b",
    re.IGNORECASE,
)
MIN_STANDALONE_CONTENT_WORDS = 4
MIN_TOPIC_CONTENT_WORDS = 2


@dataclass(frozen=True)
class Exchange:
    user: str
    assistant: str


def is_follow_up(question: str) -> bool:
    """True if the question likely refers to earlier turns (deterministic heuristic)."""
    words = len(content_tokens(question))
    if words < MIN_TOPIC_CONTENT_WORDS:
        return True
    return bool(_REFERENCE_RE.search(question)) and words < MIN_STANDALONE_CONTENT_WORDS


def retrieval_query(question: str, history: list[Exchange]) -> tuple[str, bool]:
    """Return (query, used_history). Follow-ups are combined with the previous user turn."""
    if history and is_follow_up(question):
        return f"{history[-1].user} {question}", True
    return question, False


def select_history(
    history: list[Exchange], max_exchanges: int, char_budget: int
) -> tuple[list[Exchange], bool]:
    """Keep the most recent exchanges within both limits. Returns (kept, truncated)."""
    kept = list(history[-max_exchanges:]) if max_exchanges > 0 else []
    while kept and sum(len(e.user) + len(e.assistant) for e in kept) > char_budget:
        kept.pop(0)
    return kept, len(kept) < len(history)


def chat_prompt_hash(version: str) -> str:
    payload = {
        "template": PROMPTS[version],
        "history_note": HISTORY_NOTE,
        "chat": CHAT_PROMPT_VERSION,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_chat_messages(
    version: str, history: list[Exchange], question: str, passages: list[dict]
) -> list[dict[str, str]]:
    """System prompt + prior turns as plain text + the current turn with fresh passages.

    Passages are attached only to the latest user message, so earlier answers can never be
    mistaken for evidence by the model or by the live check.
    """
    template = PROMPTS[version]
    # Without history the messages equal the batch pipeline's (build_messages) exactly.
    system = f"{HISTORY_NOTE}\n\n{template['system']}" if history else template["system"]
    messages = [{"role": "system", "content": system}]
    for exchange in history:
        messages.append({"role": "user", "content": exchange.user})
        messages.append({"role": "assistant", "content": exchange.assistant})
    messages.append(
        {
            "role": "user",
            "content": template["user"].format(
                passages=format_passages(passages), question=question
            ),
        }
    )
    return messages
