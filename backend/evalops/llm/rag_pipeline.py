"""Retrieval + prompt construction for the Kestrel policy assistant."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
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
    # rag-v3: written once after analysing rag-v1's failures on this same question set (missing
    # citations, citing unretrieved passages, dropped amounts/conditions, declining when a
    # passage did address the request). Not iterated against the results.
    "rag-v3": {
        "system": (
            "You are the customer support assistant for Kestrel Home Goods. Follow these rules:\n"
            "1. Use ONLY the policy passages provided. Do not invent policies, numbers, prices, "
            "steps, or offers.\n"
            "2. End every sentence with the id of the passage it is based on, in square "
            "brackets, for example [KB-RET-01]. Only cite ids that appear in the passages "
            "provided.\n"
            "3. Reuse the passage's own wording, and include the exact numbers, fees, time limits "
            "and conditions that apply (for example who qualifies and when).\n"
            "4. If a passage explains what can or cannot be done about the customer's request, "
            "answer with that, even if it is not what the customer hoped for.\n"
            "5. Only if no passage addresses the question, reply exactly: "
            f'"{ABSTAIN_SENTENCE}"\n'
            "6. Answer in one to three sentences, without greetings or apologies."
        ),
        "user": "Policy passages:\n{passages}\n\nCustomer question: {question}",
    },
}


# rag-v4: rag-v1's system prompt unchanged (including its refusal sentence) plus two examples
# from an unrelated domain. Written once; not tuned on its results or on the prospective
# questions in examples/rag/prospective/.
PROMPTS["rag-v4"] = {
    "system": PROMPTS["rag-v1"]["system"] + "\n\nExamples (a different organisation):\n"
    "Passages:\n[LIB-1] Library: Books can be borrowed for 21 days.\n"
    "Question: How long can I keep a book?\n"
    "Answer: You can borrow books for 21 days [LIB-1].\n\n"
    "Passages:\n[LIB-1] Library: Books can be borrowed for 21 days.\n"
    "Question: Does the library have a cafe?\n"
    f"Answer: {ABSTAIN_SENTENCE}",
    "user": PROMPTS["rag-v1"]["user"],
}


# rag-v5: rag-v4 with ONE inserted paragraph (between rag-v1's rules and rag-v4's examples) to
# reduce over-refusal while keeping conditional wording. Written once; not tuned on its results
# or on examples/rag/prospective/kestrel_prospective_v2.json.
_V5_PRINCIPLE = (
    "\nIf a passage gives an applicable restriction, escalation procedure, or general-category "
    "policy, answer with it, even when it does not grant the customer's request; include the "
    'relevant conditions and cite the source. Keep conditional wording such as "may", '
    '"might", or "subject to inspection" conditional; never turn it into a guarantee. '
    "Refuse only when no passage addresses the question, and never invent missing policies or "
    "benefits."
)
PROMPTS["rag-v5"] = {
    "system": PROMPTS["rag-v1"]["system"]
    + _V5_PRINCIPLE
    + PROMPTS["rag-v4"]["system"][len(PROMPTS["rag-v1"]["system"]) :],
    "user": PROMPTS["rag-v1"]["user"],
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
    passages: Sequence[Mapping[str, str]]
    k: int = 3

    def __post_init__(self) -> None:
        # Retain one detached snapshot. A process-wide cache must not retain ORM objects after a
        # request session closes.
        self.passages = tuple(
            {
                "passage_id": p.get("passage_id", "") if isinstance(p, Mapping) else p.passage_id,
                "title": p.get("title", "") if isinstance(p, Mapping) else p.title,
                "text": p.get("text", "") if isinstance(p, Mapping) else p.text,
            }
            for p in self.passages
        )
        self.by_id = {p["passage_id"]: p for p in self.passages}
        self.knowledge_base = {p["passage_id"]: p["text"] for p in self.passages}
        self.index = BM25Index(
            {p["passage_id"]: f"{p['title']}. {p['text']}" for p in self.passages}
        )

    def retrieve(self, question: str) -> list[dict]:
        retrieved, _ = self.retrieve_with_scores(question)
        return retrieved

    def retrieve_with_scores(self, question: str) -> tuple[list[dict], dict[str, float]]:
        """Score once and reuse the map for deterministic top-k selection."""
        scores = self.index.score(question)
        ids = self.index.rank(scores, self.k)
        return [self.by_id[pid] for pid in ids], scores

    def describe(self) -> dict:
        return {"retriever": "bm25", "k": self.k, "k1": self.index.k1, "b": self.index.b}
