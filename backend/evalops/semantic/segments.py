"""Evidence segmentation that keeps conditions attached.

A sentence that depends on the previous one ("After that, contact support", "It does not cover
...", "However, ...") is merged with it, so a unit never loses its referent or condition. The
whole passage is also a unit whenever it has more than one segment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..engine.rag import content_tokens

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_DEPENDENT_RE = re.compile(
    r"^(after that|however|except|unless|otherwise|in that case|then|this|these|that|those|it|"
    r"they|such|once that|if so|if not)\b",
    re.I,
)
# Words that make a sentence a condition or exception on the rest of its passage.
CONDITION_RE = re.compile(
    r"\b(must|only|unless|except|not|cannot|can't|never|if|once|after|before|provided|as long as|"
    r"excluding|no longer)\b",
    re.I,
)


def segments(text: str) -> list[str]:
    merged: list[str] = []
    for sentence in (s for s in _SENTENCE_RE.split(text.strip()) if s):
        if merged and _DEPENDENT_RE.match(sentence):
            merged[-1] = f"{merged[-1]} {sentence}"
        else:
            merged.append(sentence)
    return merged


def evidence_units(evidence: Mapping[str, str]) -> list[tuple[str, str, str]]:
    """(passage_id, text, scope) with scope "segment" or "passage"."""
    units = []
    for pid, text in evidence.items():
        segs = segments(text)
        units += [(pid, s, "segment") for s in segs]
        if len(segs) > 1:
            units.append((pid, text.strip(), "passage"))
    return units


def has_conditions_elsewhere(passage: str, segment: str) -> bool:
    """True if another segment of the passage states a condition or exception."""
    return any(s != segment and CONDITION_RE.search(s) for s in segments(passage))


def relevant_sentences(claim: str, unit: str, clauses: bool = True) -> list[str]:
    """Sentences (or, with clauses=True, ';' clauses) of the unit sharing the most content words
    with the claim (ties kept). Empty if nothing overlaps. Polarity checks use whole sentences
    (clauses=False) so a whole claim is never compared with half a sentence."""
    claim_tokens = set(content_tokens(claim))
    boundary = r"(?<=[.!?;])\s+" if clauses else r"(?<=[.!?])\s+"
    sentences = [s for s in re.split(boundary, unit) if s]
    overlap = [(len(claim_tokens & set(content_tokens(s))), s) for s in sentences]
    best = max((n for n, _ in overlap), default=0)
    return [s for n, s in overlap if n == best and n > 0]
