"""Claim-level support judge using a local model. Its verdicts are ESTIMATES.

The judge's agreement with labelled data is measured by backend/scripts/rag_judge.py
(`calibrate`), and reported in docs/rag-evaluation.md. Nothing here is ground truth.
"""

from __future__ import annotations

import hashlib
import json
import re

from .backends import Backend, GenerationSettings

# judge-v1: first attempt. Measured on the draft labels it rejected almost everything.
# judge-v2: developed ONLY on the constructed label subset; the generated subset is held out.
# Its few-shot examples use an unrelated fictional domain, so no evaluation item leaks in.
JUDGE_PROMPTS = {
    "judge-v1": {
        "system": (
            "You check whether a claim is fully supported by the given policy passages. "
            "A claim is SUPPORTED only if every fact in it (numbers, time limits, prices, "
            "conditions, and offers) is stated in or directly implied by the passages. "
            "Otherwise it is UNSUPPORTED. Reply with exactly one word: SUPPORTED or UNSUPPORTED."
        ),
        "user": "Passages:\n{passages}\n\nClaim: {claim}\n\nVerdict:",
    },
    "judge-v2": {
        "system": (
            "You compare a claim with policy passages. The claim may use different words than "
            "the passages; judge its meaning, not its wording.\n"
            "Answer SUPPORTED if the passages say the same thing as the claim.\n"
            "Answer UNSUPPORTED if the claim contradicts the passages, changes a number, time "
            "limit or price, or adds a fact, offer or step that the passages do not mention.\n"
            "Reply with one word: SUPPORTED or UNSUPPORTED.\n\n"
            "Examples (unrelated library policy):\n"
            "Passage: Books can be borrowed for 21 days. Late returns cost $0.25 per day.\n"
            "Claim: You can keep a borrowed book for three weeks. -> SUPPORTED\n"
            "Claim: Books can be borrowed for 30 days. -> UNSUPPORTED\n"
            "Claim: Late returns are free for members. -> UNSUPPORTED\n"
            "Claim: Each late day costs 25 cents. -> SUPPORTED"
        ),
        "user": "Passages:\n{passages}\n\nClaim: {claim}\n\nAnswer:",
    },
}
JUDGE_PROMPT_VERSION = "judge-v2"
JUDGE_SETTINGS = GenerationSettings(max_tokens=4, temperature=0.0, seed=0)


def judge_prompt_hash(version: str = JUDGE_PROMPT_VERSION) -> str:
    return hashlib.sha256(json.dumps(JUDGE_PROMPTS[version], sort_keys=True).encode()).hexdigest()


def parse_verdict(text: str) -> str:
    """'supported', 'unsupported', or 'error' if the reply is not one of the two words."""
    match = re.search(r"\b(UNSUPPORTED|SUPPORTED)\b", text.upper())
    if not match:
        return "error"
    return "unsupported" if match.group(1) == "UNSUPPORTED" else "supported"


def judge_claim(
    backend: Backend, claim: str, passages: list[dict], version: str = JUDGE_PROMPT_VERSION
) -> tuple[str, str, float]:
    """Return (verdict, raw reply, latency_ms)."""
    prompt = JUDGE_PROMPTS[version]
    text = "\n".join(f"[{p['passage_id']}] {p['text']}" for p in passages) or "(no passages)"
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"].format(passages=text, claim=claim)},
    ]
    out = backend.generate(messages, JUDGE_SETTINGS)
    return parse_verdict(out.text), out.text[:200], out.latency_ms


def judge_info(backend: Backend, version: str = JUDGE_PROMPT_VERSION) -> dict:
    return {
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "backend": backend.name,
        "prompt_version": version,
        "prompt_hash": judge_prompt_hash(version),
        "settings": {
            "temperature": JUDGE_SETTINGS.temperature,
            "max_tokens": JUDGE_SETTINGS.max_tokens,
            "seed": JUDGE_SETTINGS.seed,
        },
    }
