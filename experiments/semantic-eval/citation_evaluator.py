"""Standalone deterministic citation-quality heuristic (citation-evaluator-v4).

This module is independent of the frozen semantic claim evaluator. It consumes only claim text,
optional question context, citation IDs, retrieved passages, a static KB ID registry, and an
operational ``citation_expected`` flag. It never consumes gold labels, reviewer data, deciding
passages, or claim-verdict predictions.

Five scored outcomes follow HUMAN_REVIEW.md: correct, wrong_passage, unrelated, not_retrieved,
and partial. ``not_applicable``, ``missing_citation``, ``invalid_citation``, and ``unresolved`` are
separate statuses and must not be folded into the five-class metrics. Report their counts and
coverage separately; only the five scored statuses enter five-class confusion matrices or accuracy.

Rules are deliberately conservative, lexical, and auditable; they are not semantic entailment.
Question text is only used to resolve simple pronouns by adding its content words to the claim
matching context. This can overstate relevance. Compound claims are split on explicit conjunctions
and punctuation; coordinated phrases are an approximation. Multiple citations are each returned
with their own result. The aggregate is ``correct`` when their union covers every detected claim
component, ``partial`` when it covers some but not all, and ``unresolved`` when that coverage is
ambiguous. For a single-part claim, a relevant citation that is clearly outmatched by another
retrieved passage is ``wrong_passage``; a citation with no informative claim overlap is
``unrelated``. Weak topical overlap without a clear competitor is ``unresolved``. An invalid ID
prevents a five-class aggregate result, while every supplied ID still has its own per-citation
record. Mixed or weak signals abstain rather than force a class.

Version ``citation-evaluator-v4`` identifies the rule set. Any rule/input change should bump this
version and record source hashes and input/config digest with future predictions. This file does
not run benchmark evaluation or write results.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

VERSION = "citation-evaluator-v4"


class CitationStatus(StrEnum):
    CORRECT = "correct"
    WRONG_PASSAGE = "wrong_passage"
    UNRELATED = "unrelated"
    NOT_RETRIEVED = "not_retrieved"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    MISSING_CITATION = "missing_citation"
    INVALID_CITATION = "invalid_citation"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class CitationInput:
    claim_text: str
    citation_ids: tuple[str, ...]
    retrieved_passages: Mapping[str, str]
    knowledge_base_ids: frozenset[str]
    question_text: str | None = None
    citation_expected: bool = True


@dataclass(frozen=True)
class CitationDecision:
    citation_id: str | None
    status: CitationStatus
    reason_code: str
    reason: str
    component_coverage: tuple[float, ...] = ()


@dataclass(frozen=True)
class CitationPrediction:
    version: str
    status: CitationStatus
    reason_code: str
    reason: str
    per_citation: tuple[CitationDecision, ...]


_STOP = frozenset(
    "a an the and or but while whereas with for to of in on at by from within after before"
    " is are was were be been being can could may might must should will would do does did"
    " have has had this that these those it they them their you your we our i me my as into"
    " about what when where who how which long take takes took does not no yes usually most only includes include"
    " claim says said answer policy question get youll you'll not never cannot cant can't wont won't"
    " full complete just still every all".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_SCOPE_HEADS = frozenset({"item", "product", "order", "claim", "customer", "good"})
_BROAD_HEADS = frozenset({"item", "product", "good"})
_PREPOSITIONS = frozenset({"for", "of", "with", "in", "on", "to", "from", "by"})
_GENERIC_TOPIC_TERMS = frozenset({
    "account", "available", "customer", "help", "item", "membership", "order",
    "policy", "product", "service", "support",
})
_CONDITION_STOP = _STOP | frozenset({
    "all", "any", "except", "excluding", "unless", "only", "eligible", "qualifying",
    "marked", "when", "where", "whose", "who", "which", "that", "then", "than",
})
_CONDITION_MARKERS = re.compile(
    r"\b(?:except(?:\s+for)?|excluding|unless|only|eligible\s+for|qualifying|marked|"
    r"with|among|for|to|in|within|from|across|outside)\s+"
    r"(?:the|a|an|all|any)?\s*([^,.;!?]+)", re.I
)
_SUBJECT_PREDICATE = re.compile(
    r"^\s*(?:all|some|a|an|the)?\s*"
    r"([a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,3})\s+"
    r"(?:are|is|can|could|may|might|must|will|would|receive|receives|take|takes|"
    r"arrive|arrives|cost|costs|apply|applies|qualify|qualifies|cover|covers|"
    r"include|includes|ship|ships|return|returns|become|becomes)\b", re.I
)
_THRESHOLD_CONDITION = re.compile(
    r"\b(over|above|under|below|at\s+least|at\s+most|no\s+more\s+than|"
    r"less\s+than|more\s+than)\s+\$?([0-9]+(?:\.[0-9]+)?)\b", re.I
)


def _tokens(text: str) -> frozenset[str]:
    normalized = (_normalize_token(t) for t in _TOKEN_RE.findall(text.lower()))
    return frozenset(t for t in normalized if t and t not in _STOP and len(t) > 1 and not t.isdigit())


def _normalize_token(token: str) -> str | None:
    """Normalize simple inflections and units; do not infer semantic equivalence."""
    if token.isdigit():
        return None
    if token in {"day", "days", "week", "weeks", "month", "months", "hour", "hours"}:
        return "time_window"
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("ing") and len(token) > 5:
        stem = token[:-3]
        return stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] else stem
    if token.endswith("ed") and len(token) > 4:
        stem = token[:-2]
        return stem[:-1] if len(stem) > 2 and stem[-1] == stem[-2] else stem
    if token.endswith(("ches", "shes", "xes", "zes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")) and len(token) > 3:
        return token[:-1]
    return token


def _starts_independent_clause(text: str) -> bool:
    """Recognize clear new-clause starts without splitting coordinated noun phrases."""
    right = text.strip().lower()
    if re.match(r"^(?:you|we|they|it|he|she|there|i)(?:'ll|'re|'ve)?\s+", right):
        return True
    if re.match(r"^[a-z-]+\s+(?:can|could|may|might|must|will|would|is|are|was|were|has|have|does|do)\b", right):
        return True
    # A determiner-led subject may begin a new proposition if it proceeds directly to a predicate.
    # A prepositional modifier such as "taxes for Canadian orders are..." is not a clause boundary.
    match = re.match(
        r"^(?:a|an|the|my|your|their|these|those)\s+(.{1,48}?)\s+"
        r"(?:can|could|may|might|must|will|would|is|are|was|were|has|have|does|do)\b",
        right,
    )
    return bool(match and not (_PREPOSITIONS & set(_TOKEN_RE.findall(match.group(1)))))


def _split_claim(claim: str) -> tuple[str, ...]:
    """Split explicit clause boundaries; split 'and' only before a clear new clause."""
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r";|\b(?:but|whereas|while)\b|\band\b", claim, re.I):
        connector = match.group().lower()
        should_split = connector != "and" or _starts_independent_clause(claim[match.end():])
        if should_split:
            part = claim[cursor:match.start()].strip(" ,.")
            if part:
                parts.append(part)
            cursor = match.end()
    tail = claim[cursor:].strip(" ,.")
    if tail:
        parts.append(tail)
    return tuple(parts) or (claim,)


def _components(claim: str) -> tuple[frozenset[str], ...]:
    parts = _split_claim(claim)
    tokens = tuple(_tokens(part) for part in parts)
    return tuple(part for part in tokens if part) or (frozenset(),)


def _reference_context(claim: str, question: str | None) -> frozenset[str]:
    """Use question terms only when a simple referring expression appears in the claim."""
    if not question or not re.search(r"\b(?:it|they|them|their|this|that|these|those|its)\b", claim, re.I):
        return frozenset()
    return _tokens(question)


def _coverage(component: frozenset[str], passage: str) -> float:
    if not component:
        return 0.0
    return len(component & _tokens(passage)) / len(component)


def _narrower_passage_scope(claim: str, passage: str) -> bool:
    """Flag a citation that adds a restrictive premodifier to a generic claim subject."""
    claim_tokens = _TOKEN_RE.findall(claim.lower())
    passage_tokens = _TOKEN_RE.findall(passage.lower())
    for index, token in enumerate(claim_tokens):
        normalized = _normalize_token(token)
        if normalized not in _SCOPE_HEADS or index + 1 >= len(claim_tokens):
            continue
        if _normalize_token(claim_tokens[index + 1]) not in {"mark", "marked", "final"}:
            continue
        for p_index, p_token in enumerate(passage_tokens):
            if _normalize_token(p_token) != normalized or p_index == 0:
                continue
            modifier = _normalize_token(passage_tokens[p_index - 1])
            claim_modifier = _normalize_token(claim_tokens[index - 1]) if index else None
            if modifier and modifier not in _STOP and modifier != claim_modifier:
                return True
    return False


def _broader_passage_scope(claim: str, passage: str) -> bool:
    """Flag a generic-set passage offered for a specific object under the same policy action."""
    claim_tokens = _tokens(claim)
    passage_tokens = _tokens(passage)
    if not (_BROAD_HEADS & passage_tokens) or not (
        claim_tokens & {"return", "refund", "cover", "ship"} & passage_tokens
    ):
        return False
    match = re.search(
        r"\b(?:a|an|the)?\s*([a-z][a-z-]+)\s+(?:can|could|may|must|will|is|are)\s+"
        r"(?:be\s+)?(?:returned|refunded|covered|shipped|return|refund|cover|ship)\b",
        claim,
        re.I,
    )
    if not match:
        return False
    subject = _normalize_token(match.group(1).lower())
    return bool(subject and subject not in _SCOPE_HEADS and subject not in passage_tokens)


def _missing_asserted_facet(claim: str, passage: str) -> bool:
    """Avoid calling a citation complete when an explicitly asserted facet is absent."""
    claim_tokens = _tokens(claim)
    passage_tokens = _tokens(passage)
    facet_patterns = (
        r"\b(?:includes?|including|covers?|covering)\s+(?:a|an|the|full|complete)?\s*"
        r"([a-z-]+(?:\s+[a-z-]+){0,2})",
        r"\beligible\s+for\s+(?:a|an|the)?\s*([a-z-]+(?:\s+[a-z-]+){0,1})",
    )
    for pattern in facet_patterns:
        match = re.search(pattern, claim, re.I)
        if match:
            targets = _tokens(match.group(1)) - {"furniture", "service", "delivery"}
            if targets and not (targets & passage_tokens):
                return True
    staffing_claim = re.search(r"\b(?:staffed|staffing|run|operated)\b", claim, re.I)
    if staffing_claim and not (claim_tokens & {"staff", "agent", "human", "people"} & passage_tokens):
        return True
    return False


def _specific_alternative(claim: str, cited: str, alternatives: Sequence[str]) -> bool:
    """Find a retrieved passage with a distinctive claim phrase absent from the citation."""
    def phrase_tokens(text: str) -> list[str]:
        result = []
        for token in _TOKEN_RE.findall(text.lower()):
            if token in _STOP:
                continue
            normalized = _normalize_token(token)
            result.append(normalized if normalized is not None else token)
        return result

    raw = [t for t in phrase_tokens(claim) if t not in _STOP]
    cited_raw = phrase_tokens(cited)
    for size in (3, 2):
        phrases = {tuple(raw[i:i + size]) for i in range(len(raw) - size + 1)}
        phrases = {phrase for phrase in phrases if sum(not t.isdigit() for t in phrase) >= 2}
        for phrase in phrases:
            for alternative in alternatives:
                alt_raw = phrase_tokens(alternative)
                found_alt = any(tuple(alt_raw[i:i + size]) == phrase for i in range(len(alt_raw) - size + 1))
                found_cited = any(tuple(cited_raw[i:i + size]) == phrase for i in range(len(cited_raw) - size + 1))
                if found_alt and not found_cited:
                    return True
    return False


def _meaningfully_related(claim: str, cited: str) -> bool:
    """Require a shared policy issue term, not only generic topic or entity overlap."""
    shared_terms = _tokens(claim) & _tokens(cited)
    return bool(shared_terms - _GENERIC_TOPIC_TERMS)


def _material_conditions(text: str) -> frozenset[str]:
    """Extract explicit scope/eligibility anchors, excluding ordinary policy values."""
    conditions: set[str] = set()
    for match in _CONDITION_MARKERS.finditer(text):
        phrase = match.group(1)
        # Numeric durations and amounts are policy values; thresholds are handled separately.
        if re.match(r"\s*(?:\$?\d|\d+\s*(?:-|to)\s*\d+)", phrase, re.I):
            continue
        conditions.update(_tokens(phrase) - _GENERIC_TOPIC_TERMS - {"time_window", "business"})
    subject = _SUBJECT_PREDICATE.search(text)
    if subject:
        conditions.update(_tokens(subject.group(1)) - _GENERIC_TOPIC_TERMS)
    for match in _THRESHOLD_CONDITION.finditer(text):
        comparator = " ".join(match.group(1).lower().split())
        amount = match.group(2).replace(",", "")
        conditions.add(f"threshold:{comparator}:{amount}")
    return frozenset(conditions)


def _alternative_matches_material_condition(claim: str, cited: str, alternatives: Sequence[str]) -> bool:
    """Check that an alternative matches an explicit claim condition missing from the citation."""
    claimed = _material_conditions(claim)
    if not claimed:
        return False
    cited_conditions = _material_conditions(cited)
    for alternative in alternatives:
        alternative_conditions = _material_conditions(alternative)
        if (claimed & alternative_conditions) - cited_conditions:
            return True
    return False


def _decision(
    citation_id: str | None,
    status: CitationStatus,
    code: str,
    reason: str,
    coverage: Sequence[float] = (),
) -> CitationDecision:
    return CitationDecision(citation_id, status, code, reason, tuple(round(x, 3) for x in coverage))


def predict_citation_quality(inputs: CitationInput) -> CitationPrediction:
    """Predict citation quality without using any gold or claim-classification fields."""
    cites = tuple(dict.fromkeys(inputs.citation_ids))
    if not cites:
        status = CitationStatus.MISSING_CITATION if inputs.citation_expected else CitationStatus.NOT_APPLICABLE
        code = "expected_citation_missing" if inputs.citation_expected else "citation_not_applicable"
        return CitationPrediction(VERSION, status, code, "No citation IDs were supplied.", ())

    components = _components(inputs.claim_text)
    reference_terms = _reference_context(inputs.claim_text, inputs.question_text)
    # Add question terms only for a detected simple pronoun reference. The question itself is
    # never treated as evidence that a citation is appropriate.
    components = tuple(c | reference_terms for c in components)
    per_citation: list[CitationDecision] = []
    retrieved_cites: list[str] = []

    for cid in cites:
        if cid not in inputs.knowledge_base_ids:
            per_citation.append(_decision(cid, CitationStatus.INVALID_CITATION,
                                          "unknown_passage_id", "ID is absent from the KB registry."))
        elif cid not in inputs.retrieved_passages:
            per_citation.append(_decision(cid, CitationStatus.NOT_RETRIEVED,
                                          "known_passage_not_retrieved",
                                          "Passage exists in the KB registry but was not retrieved."))
        else:
            retrieved_cites.append(cid)

    if not retrieved_cites:
        statuses = {d.status for d in per_citation}
        if CitationStatus.INVALID_CITATION in statuses:
            status, code, reason = (CitationStatus.INVALID_CITATION, "citation_id_invalid",
                                    "No cited passage is available as a retrieved KB passage.")
        else:
            status, code, reason = (CitationStatus.NOT_RETRIEVED, "no_cited_passage_retrieved",
                                    "All known cited passages were absent from retrieval.")
        return CitationPrediction(VERSION, status, code, reason, tuple(per_citation))

    all_retrieved_scores = {
        pid: tuple(_coverage(part, text) for part in components)
        for pid, text in inputs.retrieved_passages.items()
    }
    cite_scores = {pid: all_retrieved_scores[pid] for pid in retrieved_cites}
    union_coverage = tuple(max(scores[i] for scores in cite_scores.values()) for i in range(len(components)))
    # At least 1/3 of informative terms is a weak relevance signal; 2/3 is a strong coverage
    # signal. A high competing passage must exceed the cited passage by a clear fixed margin.
    relevant = {pid: scores for pid, scores in cite_scores.items()
                if any(score >= 1 / 3 for score in scores)}
    strong_component = [any(scores[i] >= 2 / 3 for scores in cite_scores.values())
                        for i in range(len(components))]

    for cid in retrieved_cites:
        scores = cite_scores[cid]
        if len(components) > 1:
            covered = [value >= 2 / 3 for value in scores]
            if any(covered) and not all(covered):
                d = _decision(cid, CitationStatus.PARTIAL, "compound_claim_partial_coverage",
                              "This passage lexically covers some detected claim parts, but not all.", scores)
            elif all(covered):
                d = _decision(cid, CitationStatus.CORRECT, "compound_claim_covered",
                              "This passage lexically covers every detected claim part.", scores)
            elif any(value >= 1 / 3 for value in scores):
                d = _decision(cid, CitationStatus.UNRESOLVED, "compound_coverage_ambiguous",
                              "Some overlap exists, but no claim part has strong coverage.", scores)
            else:
                d = _decision(cid, CitationStatus.UNRESOLVED, "compound_relevance_weak",
                              "Lexical evidence cannot safely distinguish unrelated from wrong passage.", scores)
        else:
            own = scores[0]
            alternatives = [inputs.retrieved_passages[pid] for pid in inputs.retrieved_passages if pid != cid]
            cited_text = inputs.retrieved_passages[cid]
            if _narrower_passage_scope(inputs.claim_text, cited_text) or _broader_passage_scope(
                inputs.claim_text, cited_text
            ):
                d = _decision(cid, CitationStatus.PARTIAL, "policy_scope_partial",
                              "The passage covers a narrower or broader policy scope than the specific claim.", scores)
            elif _missing_asserted_facet(inputs.claim_text, cited_text):
                d = _decision(cid, CitationStatus.UNRESOLVED, "asserted_facet_not_addressed",
                              "The claim asserts a specific policy facet that the citation does not address.", scores)
            elif (
                _meaningfully_related(inputs.claim_text, cited_text)
                and _specific_alternative(inputs.claim_text, cited_text, alternatives)
                and (
                    own < 0.5
                    or _alternative_matches_material_condition(
                        inputs.claim_text, cited_text, alternatives
                    )
                )
            ):
                d = _decision(cid, CitationStatus.WRONG_PASSAGE, "specific_retrieved_passage_matches",
                              "The cited passage is related, but another retrieved passage matches a more specific claim phrase.", scores)
            elif own >= 0.5:
                d = _decision(cid, CitationStatus.CORRECT, "claim_relevance_sufficient",
                              "The cited passage strongly overlaps the claim's informative terms.", scores)
            elif own == 0 and len(components[0]) >= 3:
                d = _decision(cid, CitationStatus.UNRELATED, "no_claim_term_overlap",
                              "The cited passage has no informative-term overlap with the claim.", scores)
            else:
                d = _decision(cid, CitationStatus.UNRESOLVED, "relevance_ambiguous",
                              "Lexical overlap alone does not establish an appropriate citation class.", scores)
        per_citation.append(d)

    # Aggregate all citations, rather than selecting a preferred one. Invalid IDs remain visible
    # in per_citation and make the aggregate invalid; otherwise use union coverage for compounds.
    if any(d.status == CitationStatus.INVALID_CITATION for d in per_citation):
        overall = _decision(None, CitationStatus.INVALID_CITATION, "citation_set_contains_invalid_id",
                            "At least one supplied citation ID is not in the KB registry.")
    elif len(components) > 1 and any(strong_component) and not all(strong_component):
        overall = _decision(None, CitationStatus.PARTIAL, "citation_set_partial_compound_coverage",
                            "Across all cited retrieved passages, only some detected claim parts have strong coverage.", union_coverage)
    elif len(components) > 1 and all(strong_component):
        overall = _decision(None, CitationStatus.CORRECT, "citation_set_covers_compound_claim",
                            "The union of cited retrieved passages strongly covers every detected claim part.", union_coverage)
    elif len(components) == 1:
        if any(d.status == CitationStatus.CORRECT for d in per_citation):
            overall = _decision(None, CitationStatus.CORRECT, "citation_set_has_appropriate_passage",
                                "At least one cited retrieved passage strongly covers the claim.", union_coverage)
        elif any(d.status == CitationStatus.PARTIAL for d in per_citation):
            overall = _decision(None, CitationStatus.PARTIAL, "citation_set_partial_scope",
                                "The cited passage set covers only a narrower policy scope or part of the claim.", union_coverage)
        elif any(d.status == CitationStatus.WRONG_PASSAGE for d in per_citation):
            overall = _decision(None, CitationStatus.WRONG_PASSAGE, "citation_set_wrong_passage",
                                "Cited passages are outmatched by a substantially better retrieved passage.", union_coverage)
        elif all(d.status == CitationStatus.UNRELATED for d in per_citation if d.citation_id in retrieved_cites):
            overall = _decision(None, CitationStatus.UNRELATED, "citation_set_unrelated",
                                "None of the retrieved cited passages materially matches the claim.", union_coverage)
        else:
            overall = _decision(None, CitationStatus.UNRESOLVED, "citation_set_ambiguous",
                                "The cited passage set does not support a defensible single class.", union_coverage)
    else:
        overall = _decision(None, CitationStatus.UNRESOLVED, "compound_coverage_unresolved",
                            "The cited-passage union does not establish coverage of every claim part.", union_coverage)

    per_citation.sort(key=lambda decision: cites.index(decision.citation_id))
    return CitationPrediction(VERSION, overall.status, overall.reason_code, overall.reason,
                              tuple(per_citation))
