"""Deterministic retrieval-augmented-generation (RAG) evidence checks.

Pure functions, no I/O, no models. They answer:

- Retrieval: did the application retrieve the passages that answer the question?
  (recall@k, reciprocal rank)
- Citations: do cited passage IDs exist, and were they part of the retrieved context?
- Groundedness: is each claim in the answer supported by the retrieved passages?
- Abstention: did the assistant decline questions the knowledge base cannot answer, and
  answer the ones it can?

The claim-support check is a *lexical heuristic*: content-word coverage plus a strict rule
that every number in a claim must appear in the supporting passage. It catches altered facts
(wrong day counts, fees, limits) and off-topic invention, but it does not understand
paraphrase or negation. Its agreement with human labels is measured, not assumed; see
docs/rag-evaluation.md. Model-based judgments are produced separately (evalops/llm) and are
always reported as estimates.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .metrics import normalize_text

LEXICAL_SUPPORT_THRESHOLD = 0.6

_STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing down during each
    every few for from further had has have having he her here hers him his how i if in
    into is it its itself just me more most my no nor not of off on once only or other our
    ours out over own same she should so some such than that the their theirs them then
    there these they this those through to too under until up very was we were what when
    where which while who whom why will with within would you your yours yourself please
    may might must shall us let get got""".split()  # noqa: SIM905
)

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
}  # fmt: skip

_ABSTENTION_MARKERS = tuple(
    normalize_text(m)
    for m in (
        "don't have that information",
        "do not have that information",
        "don't have information about",
        "do not have information about",
        "policies don't cover",
        "policies do not cover",
        "not covered by our policies",
        "not covered in our policies",
        "cannot find that information",
        "can't find that information",
        "not able to find that information",
    )
)

_FILLER_PREFIXES = tuple(
    normalize_text(p)
    for p in (
        "hope this helps",
        "i hope this helps",
        "let me know",
        "feel free",
        "is there anything else",
        "thank you",
        "thanks",
        "happy to help",
        "you're welcome",
    )
)

_CITATION_GROUP_RE = re.compile(r"\[([^\[\]]+)\]")
_CITATION_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
# Numbered-list markers, removed BEFORE sentence splitting so "1." is never a claim: at a line
# start, or inline after ":" or sentence-final punctuation ("steps: 1. Do X. 2. Do Y.").
# "The fee is 5. Refunds…" is not a marker (no punctuation before "5").
_LINE_LIST_MARKER_RE = re.compile(r"(?m)^[ \t]*\d{1,2}[.)][ \t]+")
_INLINE_LIST_MARKER_RE = re.compile(r"(?<=[:.!?])[ \t]+\d{1,2}[.)][ \t]+(?=\S)")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------- tokens


def _stem(token: str) -> str:
    if token.isdigit():
        return token
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            if len(token) >= 3 and token[-1] == token[-2] and token[-1] not in "aeiouls":
                token = token[:-1]  # shipping -> shipp -> ship
            return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def content_tokens(text: str) -> list[str]:
    """Lowercased, stemmed tokens without stopwords or bare numbers (numbers are checked
    separately by `extract_numbers`)."""
    tokens = _TOKEN_RE.findall(normalize_text(text).replace("'", ""))
    return [_stem(t) for t in tokens if t not in _STOPWORDS and not t.isdigit()]


def extract_numbers(text: str) -> set[str]:
    """Numbers as canonical strings: digits ("$9.95" -> "9.95", "1,000" -> "1000") and
    number words ("thirty" -> "30")."""
    found = set()
    for raw in _NUMBER_RE.findall(text):
        value = raw.replace(",", "").rstrip(".")
        if "." in value:
            value = value.rstrip("0").rstrip(".") if value.split(".")[1].strip("0") == "" else value
        found.add(value)
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in _NUMBER_WORDS:
            found.add(str(_NUMBER_WORDS[word]))
    return found


# ---------------------------------------------------------------- retrieval


class BM25Index:
    """Okapi BM25 over a fixed passage set. Ties are broken by passage id for determinism."""

    def __init__(self, passages: Mapping[str, str], k1: float = 1.5, b: float = 0.75):
        if not passages:
            raise ValueError("BM25Index needs at least one passage")
        self.k1, self.b = k1, b
        self.ids = sorted(passages)
        self.docs = {pid: Counter(self._terms(passages[pid])) for pid in self.ids}
        self.lengths = {pid: sum(c.values()) for pid, c in self.docs.items()}
        # Number of indexed token occurrences. Also used by the bounded chat index cache to
        # avoid retaining an index whose Python Counter structures would be too large.
        self.term_count = sum(self.lengths.values())
        self.avg_len = sum(self.lengths.values()) / len(self.ids)
        df = Counter(term for c in self.docs.values() for term in c)
        n = len(self.ids)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    @staticmethod
    def _terms(text: str) -> list[str]:
        tokens = _TOKEN_RE.findall(normalize_text(text).replace("'", ""))
        return [_stem(t) for t in tokens if t not in _STOPWORDS]

    def score(self, query: str) -> dict[str, float]:
        terms = self._terms(query)
        scores = {}
        for pid in self.ids:
            tf, length, s = self.docs[pid], self.lengths[pid], 0.0
            for t in terms:
                if t in tf:
                    f = tf[t]
                    denom = f + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                    s += self.idf[t] * f * (self.k1 + 1) / denom
            scores[pid] = s
        return scores

    def search(self, query: str, k: int) -> list[str]:
        scores = self.score(query)
        return self.rank(scores, k)

    def rank(self, scores: Mapping[str, float], k: int) -> list[str]:
        """Return the same deterministic top-k order from an already-computed score map."""
        ranked = sorted(self.ids, key=lambda pid: (-scores[pid], pid))
        return ranked[:k]


def retrieval_metrics(
    retrieved: list[str], relevant: list[str], k: int | None = None
) -> dict[str, Any]:
    if not relevant:
        raise ValueError("retrieval metrics need at least one relevant passage id")
    k = len(retrieved) if k is None else k
    top = retrieved[:k]
    relevant_set = set(relevant)
    recall = len(set(top) & relevant_set) / len(relevant_set)
    rank = next((i for i, pid in enumerate(top, start=1) if pid in relevant_set), None)
    return {
        "retrieval_recall": round(recall, 6),
        "reciprocal_rank": round(1 / rank, 6) if rank else 0.0,
        "retrieval_k": k,
    }


# ---------------------------------------------------------------- citations and claims


def parse_citations(text: str) -> list[str]:
    """Citation ids in order of first appearance, e.g. "[KB-RET-01]" or "[KB-A-1, KB-B-2]"."""
    found: list[str] = []
    for group in _CITATION_GROUP_RE.findall(text):
        for token in re.split(r"[,;]", group):
            token = token.strip()
            if _CITATION_ID_RE.match(token) and token not in found:
                found.append(token)
    return found


def _strip_citations(text: str) -> str:
    def replace(match: re.Match) -> str:
        tokens = [t.strip() for t in re.split(r"[,;]", match.group(1))]
        return "" if all(_CITATION_ID_RE.match(t) for t in tokens) else match.group(0)

    text = _CITATION_GROUP_RE.sub(replace, text)
    text = re.sub(r"\s+([.,!?;:])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def has_meaningful_content(text: str) -> bool:
    """True if anything besides citation markers, punctuation and whitespace remains.

    Independent of claim extraction, so short answers ("30 days [KB-RET-01].") and non-Latin
    answers count as content even when they yield no checkable claim.
    """
    return any(ch.isalnum() for ch in _strip_citations(text))


def is_abstention(text: str) -> bool:
    norm = normalize_text(text)
    return any(marker in norm for marker in _ABSTENTION_MARKERS)


@dataclass(frozen=True)
class Claim:
    text: str
    citations: list[str] = field(default_factory=list)


def split_claims(text: str) -> list[Claim]:
    """Split an answer into checkable claims (sentences / bullet items).

    Dropped: questions, courtesy filler ("Hope this helps."), abstention sentences, and
    fragments with fewer than two content words unless they contain a number ("30 days.",
    "$9.95."), whose numbers are then checked against the evidence.
    """
    text = _LINE_LIST_MARKER_RE.sub("", text)
    text = _INLINE_LIST_MARKER_RE.sub("\n", text)  # each list item becomes its own segment
    claims = []
    for raw in _SENTENCE_SPLIT_RE.split(text):
        sentence = _BULLET_RE.sub("", raw).strip()
        if not sentence:
            continue
        citations = parse_citations(sentence)
        clean = _strip_citations(sentence)
        norm = normalize_text(clean)
        if (
            not norm
            or clean.rstrip().endswith("?")
            or norm.startswith(_FILLER_PREFIXES)
            or is_abstention(clean)
            or (len(content_tokens(clean)) < 2 and not extract_numbers(clean))
        ):
            continue
        claims.append(Claim(clean, citations))
    return claims


# ---------------------------------------------------------------- support


@dataclass(frozen=True)
class SupportResult:
    supported: bool
    passage_id: str | None
    coverage: float
    missing_numbers: list[str]


def lexical_support(claim: str, passages: Mapping[str, str]) -> SupportResult:
    """Best supporting passage for a claim under the lexical heuristic."""
    tokens = set(content_tokens(claim))
    numbers = extract_numbers(claim)
    best: tuple[tuple, SupportResult] | None = None
    for pid in sorted(passages):
        text = passages[pid]
        p_tokens = set(content_tokens(text))
        coverage = len(tokens & p_tokens) / len(tokens) if tokens else 1.0
        missing = sorted(numbers - extract_numbers(text))
        supported = coverage >= LEXICAL_SUPPORT_THRESHOLD and not missing
        result = SupportResult(supported, pid, round(coverage, 6), missing)
        rank = (supported, coverage, -len(missing))
        if best is None or rank > best[0]:
            best = (rank, result)
    if best is None:
        return SupportResult(False, None, 0.0, sorted(numbers))
    return best[1]


# ---------------------------------------------------------------- full evaluation


def evaluate_rag(
    output: str,
    *,
    relevant_ids: list[str],
    expect_abstain: bool,
    retrieved_ids: list[str] | None,
    knowledge_base: Mapping[str, str],
    min_groundedness: float,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Evaluate one RAG answer. Returns (metrics, failures)."""
    if retrieved_ids is not None:
        unknown = sorted(set(retrieved_ids) - set(knowledge_base))
        if unknown:
            raise ValueError(f"unknown passage ids in retrieved_passage_ids: {unknown}")

    metrics: dict[str, Any] = {"rag": True}
    failures: list[dict[str, str]] = []

    if relevant_ids and retrieved_ids is not None:
        metrics |= retrieval_metrics(retrieved_ids, relevant_ids)
    metrics["retrieved_passage_ids"] = retrieved_ids

    abstained = is_abstention(output)
    metrics["abstained"] = abstained
    metrics["abstention_correct"] = abstained == expect_abstain
    if expect_abstain and not abstained:
        failures.append(
            {
                "check": "abstention",
                "message": "answered a question the knowledge base does not cover",
            }
        )
    elif abstained and not expect_abstain:
        failures.append({"check": "abstention", "message": "declined an answerable question"})

    citations = parse_citations(output)
    nonexistent = [c for c in citations if c not in knowledge_base]
    not_retrieved = [
        c for c in citations
        if c in knowledge_base and retrieved_ids is not None and c not in retrieved_ids
    ]  # fmt: skip
    metrics |= {
        "citations": citations,
        "citations_total": len(citations),
        "citations_valid": len(citations) - len(nonexistent) - len(not_retrieved),
        "citations_nonexistent": nonexistent,
        "citations_not_retrieved": not_retrieved,
    }
    if not abstained:
        if nonexistent:
            failures.append(
                {
                    "check": "citations",
                    "message": f"cites passages that do not exist: {nonexistent}",
                }
            )
        if not_retrieved:
            failures.append(
                {
                    "check": "citations",
                    "message": f"cites passages that were not in the retrieved context: "
                    f"{not_retrieved}",
                }
            )
        if not citations and not expect_abstain:
            failures.append({"check": "citations", "message": "answer has no citations"})

    # Evidence = the passages the model was given; without retrieval info, its valid citations.
    evidence_ids = retrieved_ids if retrieved_ids is not None else [
        c for c in citations if c in knowledge_base
    ]  # fmt: skip
    evidence = {pid: knowledge_base[pid] for pid in evidence_ids}

    claim_rows = []
    pairs_total = pairs_supporting = 0
    for claim in split_claims(output):
        support = lexical_support(claim.text, evidence)
        for cid in claim.citations:
            if cid in evidence:
                pairs_total += 1
                pairs_supporting += lexical_support(claim.text, {cid: evidence[cid]}).supported
        claim_rows.append(
            {
                "text": claim.text,
                "citations": claim.citations,
                "supported": support.supported,
                "passage_id": support.passage_id,
                "coverage": support.coverage,
                "missing_numbers": support.missing_numbers,
            }
        )
    supported = sum(c["supported"] for c in claim_rows)
    groundedness = round(supported / len(claim_rows), 6) if claim_rows else None
    unsupported = [c["text"] for c in claim_rows if not c["supported"]]
    metrics |= {
        "claims": claim_rows,
        "claims_total": len(claim_rows),
        "claims_supported": supported,
        "groundedness": groundedness,
        "unsupported_claims": unsupported,
        "citation_pairs_total": pairs_total,
        "citation_pairs_supporting": pairs_supporting,
    }
    if not abstained and not has_meaningful_content(output):
        # Empty, punctuation-only or citation-only output must not pass by having nothing to fail.
        failures.append(
            {
                "check": "content",
                "message": "answer has no content besides citation markers or punctuation",
            }
        )
    if groundedness is not None and groundedness < min_groundedness:
        failures.append(
            {
                "check": "groundedness",
                "message": f"lexical groundedness {groundedness:.2f} < {min_groundedness:.2f}; "
                f"unsupported claims: {unsupported}",
            }
        )
    return metrics, failures
