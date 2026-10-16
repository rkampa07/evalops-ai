"""Negation-mismatch guard: a heuristic safety net, not semantic understanding.

Purpose: stop an NLI model's wrong "entailment" from approving a claim whose polarity is the
opposite of the evidence it relies on ("a $9.95 fee is deducted" vs "nothing is deducted").

How it works (deliberately narrow):
- Only the evidence sentences most related to the claim are compared (the same helper as the
  hedge and quantity guards). Negation elsewhere in the passage is ignored.
- Text is split into clauses (at ; : "but"/"however"/"while", and at "and" only when a new clause
  starts; not at list commas). A word the claim and the
  evidence share is "negated" when its clause contains a negator: explicit (not, no, never,
  cannot, nothing, without, non-...) or an implicit "no charge" word (waived, free, complimentary,
  exempt). So "No fee is charged" and "The fee is waived" agree.
- If any shared word is negated on one side and not the other -> mismatch -> review.
- If polarity can't be established reliably (a clause with two negators, or a negated claim
  clause that shares no word with the evidence) -> review.

Known limits: English only; word lists, not syntax; antonyms without a negator ("allowed" vs
"prohibited", "refundable" vs "final sale") aren't detected; a negation can be read as applying
to the whole clause when it applies to one word; it only ever turns "supported" into review, never
the reverse.
"""

from __future__ import annotations

import re

from ..engine.rag import content_tokens
from .segments import relevant_sentences

_EXPLICIT = re.compile(
    r"\b(not|no|never|none|nothing|nobody|nowhere|neither|nor|without|cannot|"
    r"can't|cant|won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|shouldn't|mustn't|"
    r"no longer)\b|\bnon-?\w+",
    re.I,
)
# "No charge" meaning without a negator word. Only these; antonyms are out of scope.
_IMPLICIT = re.compile(r"\b(waived|free|complimentary|exempt|at no cost|at no charge)\b", re.I)
# Clause boundaries: ; : and contrast words. "and" only when a new clause starts ("..., and you
# do not need to", "... and is free"), not inside a list ("gift cards, and clearance items").
# Commas alone don't split: they separate list items and leading conditions.
_CLAUSE_SPLIT = re.compile(
    r"\s*(?:[;:]|,?\s*\b(?:but|however|while|whereas)\b|,?\s*\band\b(?=\s+(?:you|we|it|they|"
    r"he|she|i|is|are|was|were|will|can|cannot|may|must|has|have|does|do|takes?|costs?)\b))\s*",
    re.I,
)


def _normalise(text: str) -> str:
    return text.replace("’", "'")


def _clauses(text: str) -> list[str]:
    return [c for c in _CLAUSE_SPLIT.split(_normalise(text)) if c and c.strip()]


# Quantity bounds that contain "no"/"not" but aren't negation ("no more than 7 days").
_BOUND_PHRASES = re.compile(
    r"\b(?:no|not)\s+(?:more|less|fewer|later|earlier|longer|sooner)\s+than\b", re.I
)


def _negators(clause: str) -> int:
    clause = _BOUND_PHRASES.sub(" ", clause)
    return len(_EXPLICIT.findall(clause)) + len(_IMPLICIT.findall(clause))


def _negated_words(text: str, shared: set[str]) -> tuple[dict[str, bool], list[str]]:
    """{shared word: negated?} and reasons the polarity is unreliable."""
    state: dict[str, bool] = {}
    unreliable = []
    for clause in _clauses(text):
        n = _negators(clause)
        words = set(content_tokens(clause)) & shared
        if n > 1:
            unreliable.append(f'double or stacked negation in "{clause.strip()}"')
        for w in words:
            # A word in several clauses counts as negated if any of its clauses is negated.
            state[w] = state.get(w, False) or n % 2 == 1
    return state, unreliable


def negation_problems(claim: str, unit: str) -> list[str]:
    """[] if the claim's polarity agrees with the evidence related to it. Clause by clause: each
    ';'-separated part of the claim is compared with the evidence clauses most related to that
    part, so a whole claim is never compared with half a sentence, nor a claim clause with an
    unrelated clause ("We ship to Canada" vs "...; we do not ship to other countries")."""
    parts = [p for p in re.split(r"\s*;\s*", claim) if p.strip()] or [claim]
    problems: list[str] = []
    for part in parts:
        problems += _part_problems(part, unit)
    return list(dict.fromkeys(problems))


def _part_problems(claim: str, unit: str) -> list[str]:
    claim_has_negation = any(_negators(c) for c in _clauses(claim))
    sentences = relevant_sentences(claim, unit)
    if not sentences:
        if not claim_has_negation:
            return []
        return [f'the negation in "{claim.strip()}" has no counterpart in the evidence']
    problems = []
    for sentence in sentences:
        shared = set(content_tokens(claim)) & set(content_tokens(sentence))
        claim_state, claim_unreliable = _negated_words(claim, shared)
        evidence_state, evidence_unreliable = _negated_words(sentence, shared)
        problems += claim_unreliable + evidence_unreliable
        for clause in _clauses(claim):
            if _negators(clause) and not set(content_tokens(clause)) & shared:
                problems.append(
                    f'the negation in "{clause.strip()}" has no counterpart in the evidence'
                )
        flipped = sorted(w for w in shared if claim_state.get(w) != evidence_state.get(w))
        if flipped:
            word = flipped[0]
            claim_side = "negative" if claim_state.get(word) else "affirmative"
            evidence_side = "negative" if evidence_state.get(word) else "affirmative"
            problems.append(
                f'polarity differs for "{", ".join(flipped)}": the claim is {claim_side} where '
                f'the evidence ("{sentence.strip()}") is {evidence_side}'
            )
    return problems
