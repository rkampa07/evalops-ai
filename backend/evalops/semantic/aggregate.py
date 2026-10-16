"""Pure, deterministic aggregation of per-unit NLI scores into a claim verdict.

Rules (thresholds are untuned defaults; choose them on the dev split only):
- some unit entails and none relevantly contradicts -> supported (then guards)
- some unit contradicts and none entails   -> contradicted
- units entail AND contradict              -> needs_human_review (shows both)
- neither                                  -> insufficient_evidence
Guards, applied to a supported verdict (things NLI models are known to get wrong):
- detached segment: support comes from one segment of a passage whose other segments state
  conditions, and the whole passage wasn't also judged to support it -> needs_human_review
- quantities: one evidence sentence about the claim must state every claim quantity (same unit,
  value or range, and bound), with no equally related sentence stating a competing value;
  unparsed amounts ("two", "half") need the identical word there. Otherwise -> review.
  Question numbers never count.
- conflict: another retrieved passage, at least as related to the claim, states a different value
  of the same kind of quantity -> needs_human_review (and ties never depend on evidence order)
Before deciding, model contradictions are screened: ones from evidence sharing fewer than
MIN_SHARED_WORDS content words with the claim, or stating the claim's value only under the
opposite, non-exclusive condition, or qualifying shared words differently (a "different concept":
"free shipping" vs "return shipping"), are set aside and recorded (ignored_contradictions).
Set-aside contradictions are retained in diagnostics but do not veto independent support. A
quantified opposite condition alone is insufficient evidence unless the wording explicitly or
implicitly makes the condition exclusive. A remaining contradiction from hedged evidence or a
non-exclusive list of included features becomes insufficient_evidence: it does not establish the
opposite. When nothing is decisive, a related sentence of opposite polarity (a possible missed
exception) sends the claim to review.
- negation (heuristic): a shared word negated on one side only, or polarity that can't be
  established, in the evidence sentences related to the claim -> needs_human_review
- hedges (rule R2): the evidence sentences that relate to the claim are hedged ("usually", "may
  take") and the claim is certain -> insufficient_evidence. Bounds such as "up to" or "within"
  are not hedges; they're checked by the quantity guard.
- polarity: the chat-only can/cannot rules see an opposite policy for the same subject
  -> needs_human_review (a disagreement is never resolved silently)
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from ..engine.rag import content_tokens
from .negation import negation_problems
from .quantities import binding_problems, complementary_only, quantities
from .segments import has_conditions_elsewhere, relevant_sentences
from .verdict import (
    CONTRADICTED,
    CONTRADICTION,
    ENTAILMENT,
    INSUFFICIENT,
    NEEDS_REVIEW,
    SUPPORTED,
    SemanticVerdict,
    UnitScore,
)

AGGREGATION_VERSION = "semantic-aggregate-v12"
# Uncertainty, not bounds or permission: "may take", "may be" are hedges; "customers may request"
# (permission) and "up to 30 days" (a bound) are not.
EVIDENCE_HEDGE_RE = re.compile(
    r"\b(usually|typically|generally|normally|often|in most cases|might|could|"
    r"may (?:be|take|vary|need|require|apply|not|change|differ))\b",
    re.I,
)
CLAIM_HEDGE_RE = re.compile(
    r"\b(usually|typically|generally|normally|often|in most cases|might|could|may|"
    r"possibly|likely|can take)\b",
    re.I,
)


@dataclass(frozen=True)
class AggregationConfig:
    entail_threshold: float = 0.5  # untuned default
    contradict_threshold: float = 0.5  # untuned default
    include_question: bool = True  # R1: a second, context-only pass (never "supported" from it)
    detached_guard: bool = True
    number_guard: bool = True
    negation_guard: bool = True
    hedge_guard: bool = True
    polarity_guard: bool = True
    version: str = AGGREGATION_VERSION

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


def _quote(u: UnitScore) -> str:
    return f'{u.passage_id} says "{u.unit}"'


def _strongest(units: list[UnitScore], key: str, claim: str = "") -> UnitScore:
    """Highest score; ties go to the unit sharing the most words with the claim, then to a segment
    over a whole passage, then by passage id and text. Never by input order, so reordering the
    evidence can't change the choice."""
    claim_tokens = set(content_tokens(claim))
    return min(
        units,
        key=lambda u: (
            -u.scores[key],
            -len(claim_tokens & set(content_tokens(u.unit))),
            u.scope != "segment",
            u.passage_id,
            u.unit,
        ),
    )


def _cross_passage_conflicts(claim: str, e: UnitScore, evidence: Mapping[str, str]) -> list[str]:
    """Another retrieved passage, at least as related to the claim, states a different value of a
    quantity the claim uses ("1-year" vs "2-year" warranty) -> conflicting evidence."""
    claim_qs = quantities(claim)
    if not claim_qs:
        return []
    claim_tokens = set(content_tokens(claim))
    own = max(
        (len(claim_tokens & set(content_tokens(s))) for s in relevant_sentences(claim, e.unit)),
        default=0,
    )
    found = []
    for pid in sorted(evidence):
        if pid == e.passage_id:
            continue
        for sentence in relevant_sentences(claim, evidence[pid]):
            claim_regions, sentence_regions = _regions(claim), _regions(sentence)
            # A value for one explicit geographic scope cannot conflict with a value for another.
            if claim_regions and sentence_regions and claim_regions.isdisjoint(sentence_regions):
                continue
            # Different product/service qualifiers and complementary numeric conditions describe
            # separate cases, so their quantities are not competing values for this claim.
            if _different_concept(claim, sentence) or complementary_only(claim, sentence):
                continue
            if len(claim_tokens & set(content_tokens(sentence))) < own:
                continue
            stated = quantities(sentence)
            for q in claim_qs:
                same_kind = [x for x in stated if x.unit == q.unit]
                if same_kind and not any((x.low, x.high) == (q.low, q.high) for x in same_kind):
                    values = ", ".join(x.text for x in same_kind)
                    found.append(f'{pid} says "{sentence.strip()}" ({values} vs {q.text})')
    return found


# For softening a CONTRADICTION only: any modal of possibility counts as a hedge, except
# permission phrasing ("customers may request"). Broader than EVIDENCE_HEDGE_RE on purpose: here
# a false match can only turn "contradicted" into needs_human_review, never into "supported".
_CONTRADICTION_HEDGE_RE = re.compile(
    EVIDENCE_HEDGE_RE.pattern
    + r"|\b(?:may|might|could)\b(?!\s+(?:request|ask|return|exchange|cancel|choose|use|contact|"
    r"bring|keep|apply)\b)",
    re.I,
)
# A model contradiction must come from evidence that is about the claim: at least this many
# shared content words. A fixed minimum chosen up front, not fitted to any benchmark example.
MIN_SHARED_WORDS = 2
# Open lists ("includes", "such as") without exclusive wording don't rule other items out (R5).
_OPEN_LIST_RE = re.compile(r"\b(includes?|including|such as|for example|e\.g\.)", re.I)
_EXCLUSIVE_RE = re.compile(
    r"\b(only|exclusively|solely|no other|nothing else|not|never|cannot|can't|excludes?|except)\b",
    re.I,
)


# A claim about what something includes; only such claims can be softened by an open list.
_INCLUSION_CLAIM_RE = re.compile(r"\b(includes?|including|comes? with|offers?|covers?)\b", re.I)
_WORD_RE = re.compile(r"[A-Za-z0-9$][A-Za-z0-9$.'-]*|[,;:!?()]")
_GENERIC_POLICY_ACTORS = frozenset(content_tokens("support agent assistant staff chat"))
_REGION_RE = re.compile(
    r"\b(?:to|in|within|from|across)\s+(?:the\s+)?"
    r"((?:contiguous\s+)?(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)*)"
)


def _regions(text: str) -> set[str]:
    """Return explicit place names used as policy scope."""
    return {
        " ".join(w.lower() for w in match.group(1).split() if w.lower() != "contiguous")
        for match in _REGION_RE.finditer(text)
    }


def _modifiers(text: str) -> dict[str, set[str]]:
    """{content stem: stems of the word(s) qualifying it}, from immediately preceding words in
    the same phrase ("return shipping" -> shipping: {return}) or hyphenated parts. Numbers and
    function words don't qualify; punctuation breaks a phrase."""
    out: dict[str, set[str]] = {}
    prev: list[str] = []
    for token in _WORD_RE.findall(text):
        if not token[0].isalnum() and token[0] != "$":
            prev = []
            continue
        stems = content_tokens(token)
        for k, stem in enumerate(stems):
            out.setdefault(stem, set()).update(stems[:k] if k else prev)
        prev = [] if any(ch.isdigit() for ch in token) else stems
    return out


# Never qualifiers: modality, negation, hedges, quantifiers ("always run", "may request").
_NOT_QUALIFIERS = frozenset(
    content_tokens(
        "may might can cannot could must will would should not no non never always often usually "
        "also only just still all every each any some most more less few fewer per very"
    )
)

# Shared policy subjects whose modifiers can restrict a claim's scope.
_SCOPE_HEADS = frozenset(content_tokens("item product"))

# Small, general policy-subject alias sets prevent low lexical overlap from hiding a direct
# exception (for example, pet/animal or smoking/tobacco wording).
_CONCEPT_ALIASES = tuple(
    frozenset(content_tokens(group))
    for group in ("pet animal dog canine", "smoke smoking tobacco cigarette")
)
_COST_POLARITY = (
    frozenset(content_tokens("free complimentary waived")),
    frozenset(content_tokens("fee cost paid payment")),
)


_LY_NOUNS = frozenset({"family", "supply", "assembly", "reply", "ally", "jelly", "belly", "rally"})


def _different_concept(claim: str, text: str) -> list[str]:
    """Shared words that BOTH sides qualify, each with a word the other text doesn't contain
    ("free shipping" vs "return shipping"): they may name different things despite sharing a
    word. Modals, negations, hedges, quantifiers and -ly words never count as qualifiers."""
    a, b = _modifiers(claim), _modifiers(text)
    claim_words, text_words = set(content_tokens(claim)), set(content_tokens(text))

    def own(quals: set[str], other_words: set[str]) -> set[str]:
        return {
            q
            for q in quals - _NOT_QUALIFIERS - other_words
            if not (q.endswith("ly") and q not in _LY_NOUNS)
        }

    return sorted(w for w in set(a) & set(b) if own(a[w], text_words) and own(b[w], claim_words))


def _narrower_scope(claim: str, evidence: str) -> list[str]:
    """Find restrictive modifiers on shared policy subjects that the claim omits."""
    claim_modifiers, evidence_modifiers = _modifiers(claim), _modifiers(evidence)
    shared_heads = _SCOPE_HEADS & set(content_tokens(claim)) & set(content_tokens(evidence))
    problems = []
    for head in sorted(shared_heads):
        extra = evidence_modifiers.get(head, set()) - claim_modifiers.get(head, set())
        extra -= _NOT_QUALIFIERS
        if extra:
            problems.append(
                f"{head} is restricted by {', '.join(sorted(extra))} in the evidence "
                "but not in the claim"
            )
    return problems


def _has_alias_match(claim: str, evidence: str) -> bool:
    claim_words, evidence_words = set(content_tokens(claim)), set(content_tokens(evidence))
    return any(claim_words & group and evidence_words & group for group in _CONCEPT_ALIASES)


def _has_cost_polarity_pair(claim: str, evidence: str) -> bool:
    claim_words, evidence_words = set(content_tokens(claim)), set(content_tokens(evidence))
    free, paid = _COST_POLARITY
    return bool((claim_words & free and evidence_words & paid)
                or (claim_words & paid and evidence_words & free))


def screen_contradictions(claim: str, contradicting: list[UnitScore]) -> tuple[list, list[dict]]:
    """Separate contradictions the evaluator can act on from ones it sets aside (and records):
    - "unrelated": fewer than MIN_SHARED_WORDS content words shared with the claim;
    - "complementary": states the claim's value only under the opposite, non-exclusive condition.
    Both are diagnoses, not proof (synonyms, implicit exclusivity and mixed conditions defeat
    them). The caller records them for audit, but they do not veto separate decisive support."""
    claim_tokens = set(content_tokens(claim))
    kept, ignored = [], []
    for u in contradicting:
        shared = len(claim_tokens & set(content_tokens(u.unit)))
        if shared < MIN_SHARED_WORDS and (
            _has_alias_match(claim, u.unit) or _has_cost_polarity_pair(claim, u.unit)
        ):
            kept.append(u)
            continue
        concept = _different_concept(claim, u.unit) if shared >= MIN_SHARED_WORDS else []
        if shared < MIN_SHARED_WORDS:
            kind, why = "unrelated", f"few shared words with the claim ({shared})"
        elif concept:
            kind, why = (
                "different_concept",
                f"shared words qualified differently ({', '.join(concept)})",
            )
        elif complementary_only(claim, u.unit):
            kind, why = "complementary", "states the opposite, non-exclusive condition"
        else:
            kept.append(u)
            continue
        ignored.append({"passage_id": u.passage_id, "scope": u.scope, "unit": u.unit,
                        "contradiction": u.scores[CONTRADICTION], "kind": kind,
                        "why": why})  # fmt: skip
    return kept, ignored


def decisive(units: list[UnitScore], config: AggregationConfig) -> tuple[list, list]:
    entailing = [
        u
        for u in units
        if u.relation == ENTAILMENT and u.scores[ENTAILMENT] >= config.entail_threshold
    ]
    contradicting = [
        u
        for u in units
        if u.relation == CONTRADICTION and u.scores[CONTRADICTION] >= config.contradict_threshold
    ]
    return entailing, contradicting


def aggregate(
    claim: str,
    units: list[UnitScore],
    evidence: Mapping[str, str],
    config: AggregationConfig,
) -> SemanticVerdict:
    entailing, contradicting = decisive(units, config)
    contradicting, ignored = screen_contradictions(claim, contradicting)
    scores = [{"passage_id": u.passage_id, "scope": u.scope, **u.scores} for u in units]
    verdict = SemanticVerdict(
        label=INSUFFICIENT,
        reason="",
        config=asdict(config),
        uncalibrated_scores=scores,
        ignored_contradictions=ignored,
    )

    if entailing and contradicting:
        e, c = (
            _strongest(entailing, ENTAILMENT, claim),
            _strongest(contradicting, CONTRADICTION, claim),
        )
        verdict.label = NEEDS_REVIEW
        verdict.reason = (
            f"Conflicting evidence: {_quote(e)}, which the model judged to support the claim, "
            f"but {_quote(c)}, which it judged to contradict it (for example a general rule "
            "and an exception). A person should decide."
        )
        verdict.deciding = [_deciding(e, "supports"), _deciding(c, "contradicts")]
        return verdict
    if contradicting:
        c = _strongest(contradicting, CONTRADICTION, claim)
        verdict.label = CONTRADICTED
        verdict.reason = f"{_quote(c)}, which the model judged to contradict the claim."
        verdict.deciding = [_deciding(c, "contradicts")]
        # A contradiction score alone doesn't show the claim is explicitly ruled out (R2, R5).
        related = relevant_sentences(claim, c.unit) or [c.unit]
        hedged = [x for x in related if _CONTRADICTION_HEDGE_RE.search(x)]
        # Only a claim about what is included can be softened by an open list; a price, fee or
        # other quantity the list sentence contradicts stays contradicted.
        open_list = [
            x
            for x in related
            if _OPEN_LIST_RE.search(x)
            and not _EXCLUSIVE_RE.search(x)
            and _INCLUSION_CLAIM_RE.search(claim)
            and not quantities(claim)
        ]
        if hedged:
            verdict.label = INSUFFICIENT
            verdict.guards.append("hedged_contradiction")
            verdict.reason += (
                f' But that evidence is hedged ("{hedged[0].strip()}"): it may not rule the '
                "claim out (rule R2)."
            )
        elif open_list:
            verdict.label = INSUFFICIENT
            verdict.guards.append("open_list")
            verdict.reason += (
                f' But that evidence is a non-exclusive list ("{open_list[0].strip()}"): it '
                "doesn't say other items are excluded (rule R5)."
            )
        return verdict
    if not entailing:
        verdict.reason = (
            "No retrieved evidence was judged to support or contradict the claim."
            if units
            else "No evidence was retrieved."
        )
        complementary = [i for i in ignored if i["kind"] != "unrelated"]
        if complementary:
            # Explicitly exclusive conditions are retained as contradictions by screening. A
            # non-exclusive opposite condition does not settle this claim.
            listed = "; ".join(f'{i["passage_id"]} ("{i["unit"].strip()}")' for i in complementary)
            verdict.reason += (
                " The only model contradictions were screened as opposite, non-exclusive "
                f"conditions or different concepts ({listed}); they do not settle this claim."
            )
            if any(i["kind"] == "complementary" for i in complementary):
                verdict.guards.append("complementary_condition")
            return verdict
        exception = _possible_exception(claim, evidence) if config.negation_guard else None
        if exception:
            verdict.label = NEEDS_REVIEW
            verdict.guards.append("possible_exception")
            verdict.reason += (
                f" But a related policy sentence states the opposite polarity: {exception} "
                "The model may have missed an exception (for example an item inside a list). "
                "A person should decide."
            )
        return verdict

    e = _strongest(entailing, ENTAILMENT, claim)
    verdict.label = SUPPORTED
    verdict.reason = f"{_quote(e)}, which the model judged to support the claim."
    verdict.deciding = [_deciding(e, "supports")]

    def downgrade(label: str, guard: str, why: str) -> SemanticVerdict:
        verdict.label = label
        verdict.guards.append(guard)
        verdict.reason += " " + why
        return verdict

    if config.detached_guard and e.scope == "segment":
        passage = evidence[e.passage_id]
        whole_entails = any(
            u.passage_id == e.passage_id and u.scope == "passage" for u in entailing
        )
        # A refund claim explicitly restricted to already-returned items is downstream of
        # return-eligibility conditions; those conditions do not qualify where the refund goes.
        downstream_refund = bool(
            re.search(r"\brefunds?\b", claim, re.I)
            and re.search(r"\breturned\s+(?:items?|products?)\b", claim, re.I)
        )
        if has_conditions_elsewhere(passage, e.unit) and not whole_entails and not downstream_refund:
            return downgrade(
                NEEDS_REVIEW,
                "detached",
                f"But {e.passage_id} states conditions in another sentence, and the whole passage "
                "wasn't judged to support the claim.",
            )
    if config.number_guard:
        problems = binding_problems(claim, e.unit)
        if problems:
            return downgrade(
                NEEDS_REVIEW,
                "number",
                f"But the claim's quantities can't be verified against their conditions: "
                f"{'; '.join(problems)}.",
            )
    scope_problems = _narrower_scope(claim, e.unit)
    if scope_problems:
        return downgrade(
            INSUFFICIENT,
            "scope",
            "The supporting evidence is narrower than the claim: "
            + "; ".join(scope_problems)
            + ".",
        )
    if config.number_guard:
        conflicts = _cross_passage_conflicts(claim, e, evidence)
        if conflicts:
            return downgrade(
                NEEDS_REVIEW,
                "conflict",
                "But other retrieved evidence about the same thing states a different value: "
                f"{'; '.join(conflicts)}.",
            )
    if config.negation_guard:
        problems = negation_problems(claim, e.unit)
        if problems:
            return downgrade(
                NEEDS_REVIEW,
                "negation",
                f"But the claim's polarity doesn't match the evidence (heuristic check): "
                f"{'; '.join(problems)}.",
            )
    if config.hedge_guard:
        hedged = [s for s in relevant_sentences(claim, e.unit) if EVIDENCE_HEDGE_RE.search(s)]
        if hedged and not CLAIM_HEDGE_RE.search(claim):
            return downgrade(
                INSUFFICIENT,
                "hedge",
                f'But the relevant evidence is hedged ("{hedged[0]}") and the claim states it as '
                "certain (rule R2).",
            )
    if config.polarity_guard:
        conflict = _polarity_conflict(claim, evidence)
        if conflict:
            return downgrade(
                NEEDS_REVIEW,
                "polarity",
                f"But the can/cannot rules found an opposite policy: {conflict}",
            )
    return verdict


def _possible_exception(claim: str, evidence: Mapping[str, str]) -> str | None:
    """A related sentence (>= MIN_SHARED_WORDS shared words) whose polarity is the reverse of
    the claim's, e.g. an item listed under "cannot be returned". Uses the generic negation
    check; nothing here is specific to an item or a policy."""
    claim_tokens = set(content_tokens(claim))
    for pid in sorted(evidence):
        for sentence in relevant_sentences(claim, evidence[pid], clauses=False):
            if len(claim_tokens & set(content_tokens(sentence))) < MIN_SHARED_WORDS:
                continue
            # Generic actors can overlap across unrelated policy properties ("support agents"
            # staff chat and handle compensation). Require a shared property term as well.
            specific_shared = (claim_tokens & set(content_tokens(sentence))) - _GENERIC_POLICY_ACTORS
            if not specific_shared:
                continue
            if any(p.startswith("polarity differs") for p in negation_problems(claim, sentence)):
                return f'{pid} says "{sentence.strip()}".'
    return None


def _deciding(u: UnitScore, relation: str) -> dict:
    return {"passage_id": u.passage_id, "unit": u.unit, "scope": u.scope, "relation": relation}


def _polarity_conflict(claim: str, evidence: Mapping[str, str]) -> str | None:
    from ..chat.diagnostics import diagnose  # chat-only rules, reused read-only

    for w in diagnose(claim, evidence, evidence)["warnings"]:
        if w["kind"] == "policy_polarity":
            return w["reason"]
    return None
