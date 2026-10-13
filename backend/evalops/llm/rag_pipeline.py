"""Retrieval + prompt construction for the Kestrel policy assistant."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..engine.rag import BM25Index

ABSTAIN_SENTENCE = "I don't have that information in our policies."

# Prompt versions under test. v1 is the production baseline. v2 is a realistic product change:
# "make the assistant friendlier and stop refusing customers". It removes the instruction to
# answer only from the passages and the explicit refusal sentence.
PROMPTS: dict[str, dict[str, str]] = {
    "rag-v1": {
        "system": (
            "You are the customer support assistant for Kestrel Home Goods. Answer the "
            "customer's question using ONLY the policy passages provided. After each sentence "
            "that uses a passage, cite its id in square brackets, for example [KB-RET-01]. "
            "Do not invent policies, numbers, prices, or offers. If the passages do not "
            f'contain the answer, reply exactly: "{ABSTAIN_SENTENCE}"'
        ),
        "user": "Policy passages:\n{passages}\n\nCustomer question: {question}",
    },
    "rag-v2": {
        "system": (
            "You are Kestrel Home Goods' friendly support assistant. Keep answers short and "
            "warm, and always try to give the customer a helpful answer. Use the policy "
            "passages below and cite them like [KB-RET-01] when you use them."
        ),
        "user": "Policy passages:\n{passages}\n\nCustomer question: {question}",
    },
}


def prompt_hash(version: str) -> str:
    return hashlib.sha256(json.dumps(PROMPTS[version], sort_keys=True).encode()).hexdigest()


def format_passages(passages: list[dict]) -> str:
    return "\n".join(f"[{p['passage_id']}] {p['title']}: {p['text']}" for p in passages)


def build_messages(version: str, question: str, passages: list[dict]) -> list[dict[str, str]]:
    template = PROMPTS[version]
    return [
        {"role": "system", "content": template["system"]},
        {
            "role": "user",
            "content": template["user"].format(
                passages=format_passages(passages), question=question
            ),
        },
    ]


@dataclass
class Retriever:
    passages: list[dict]
    k: int = 3

    def __post_init__(self) -> None:
        self.by_id = {p["passage_id"]: p for p in self.passages}
        self.index = BM25Index(
            {p["passage_id"]: f"{p['title']}. {p['text']}" for p in self.passages}
        )

    def retrieve(self, question: str) -> list[dict]:
        return [self.by_id[pid] for pid in self.index.search(question, self.k)]

    def describe(self) -> dict:
        return {"retriever": "bm25", "k": self.k, "k1": self.index.k1, "b": self.index.b}
