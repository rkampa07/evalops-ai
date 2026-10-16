"""Chat-only diagnostics: rule-based warnings about policy polarity and citations.

Separate from methodology 2.0.0 (engine/rag.py): nothing here changes pass/fail, scores, runs or
release gates. The rules are deliberately narrow. A polarity is assigned only to explicit
constructions ("can be returned", "cannot be returned", "non-refundable", "eligible for a
refund", "does not cover"), never to a bare keyword, and a contradiction is reported only when
the claim and the passage share at least two specific subject words. Weaker matches are reported
as "needs human review", not as contradictions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ..engine.rag import content_tokens, extract_numbers, is_abstention, split_claims

DIAGNOSTICS_VERSION = "chat-diagnostics-v1"
LABEL = (
    "Chat diagnostics (heuristic): rule-based warnings about can/cannot policy claims and "
    "citations. Not verified semantic correctness; not part of methodology 2.0.0 or any gate."
)

POTENTIAL_CONTRADICTION = "potential_contradiction"
CITATION_CONCERN = "citation_concern"
NEEDS_HUMAN_REVIEW = "needs_human_review"

# Action groups whose polarity (allowed vs not allowed) the rules understand.
_VERBS = {
    "return or refund": r"(?:return(?:s|ed|ing)?|refund(?:s|ed|ing)?|exchang(?:e|es|ed|ing))",
    "cancel": r"(?:cancel(?:s|led|ed|ling|ing)?)",
    "warranty coverage": r"(?:cover(?:s|ed|ing)?)",
}
_ABLE = {
    "return or refund": r"(?:returnable|refundable|exchangeable)",
    "cancel": r"(?:cancell?able)",
    "warranty coverage": r"(?!x)x",  # never matches
}
_NEG_AUX = (
    r"(?:cannot|can not|could not|may not|must not|will not|won't|can't|is not|are not|isn't|"
    r"aren't|does not|do not|doesn't|don't|not|never)"
)
_AFF_AUX = r"(?:can|may|will|could|are able to|is able to)"
_FILLER = r"(?:(?:be|been|get|also|still|always)\s+)?(?:\w+\s+(?:or|and)\s+)?"
# "you do not need to return the damaged item" is not a prohibition.
_NOT_A_RULE = re.compile(r"\b(?:not|no|n't)\s+(?:need|have)\s+to\b|\bno need to\b")
_GENERIC = frozenset(
    content_tokens(
        "item items product products order orders purchase purchased thing customer customers "
        "policy policies kestrel day days business delivery full original payment method "
        "eligible yes however please note warranty request receive get"
    )
)
_ACTION_TOKENS = frozenset(
    content_tokens("return returns returned refund refunds exchange cancel cancelled cover")
)
MATCH_THRESHOLD = 1 / 3  # below this coverage a passage "doesn't match" a claim
UNRELATED_MARGIN = 0.3  # another retrieved passage matches this much better than the cited one


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("’", "'"))


def polarity(sentence: str) -> dict[str, str]:
    """{action group: "allowed" | "not allowed"} for explicit constructions only.

    A sentence with both readings for the same group (e.g. "most items can be returned, but
    custom items cannot be returned") gets no polarity for that group: it is too mixed to judge.
    """
    text = _norm(sentence)
    found: dict[str, str] = {}
    for group, verb in _VERBS.items():
        able = _ABLE[group]
        neg = bool(
            re.search(rf"\b{_NEG_AUX}\s+{_FILLER}{verb}\b", text)
            or re.search(rf"\bnon-?{able}\b", text)
            or re.search(rf"\b(?:not|in)\s*eligible for (?:an? )?(?:full |partial )?{verb}", text)
            or re.search(rf"\bno {verb}\b", text)
            or re.search(rf"\b(?:is|are) not {able}\b", text)
        )
        aff = bool(
            re.search(rf"(?<!not )\b{_AFF_AUX}\s+{_FILLER}{verb}\b", text)
            or re.search(
                rf"\b{_AFF_AUX}\s+(?:request|get|receive|ask for)\s+(?:an? )?(?:full )?{verb}",
                text,
            )
            or re.search(
                rf"(?<!not )(?<!in)\beligible for (?:an? )?(?:full |partial )?{verb}", text
            )
            or re.search(rf"(?<!non-)(?<!non)(?<!not )\b{able}\b", text)
            or re.search(rf"\bhave \d+ days to {verb}\b", text)
        )
        if group == "warranty coverage" and not aff:
            # "covers" / "is covered" / "covering" are affirmative without a modal.
            aff = bool(re.search(r"(?<!not )\b(?:covers|covering|(?:is|are) covered)\b", text))
        if _NOT_A_RULE.search(text):
            neg = False
        if neg != aff:
            found[group] = "not allowed" if neg else "allowed"
    return found


_STANCE_WORDS = {"warranty coverage": {"allowed": "covered", "not allowed": "not covered"}}


def _stance(group: str, stance: str) -> str:
    return _STANCE_WORDS.get(group, {}).get(stance, stance)


def _words(text: str, stems: set[str]) -> str:
    """The claim's own words behind a set of stems, for readable reasons."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text) if set(content_tokens(w)) & stems]
    return ", ".join(dict.fromkeys(w.lower() for w in words)) or ", ".join(sorted(stems))


def _subject(text: str) -> set[str]:
    return set(content_tokens(text)) - _GENERIC - _ACTION_TOKENS


def _coverage(claim: str, passage: str) -> float:
    tokens = set(content_tokens(claim))
    return len(tokens & set(content_tokens(passage))) / len(tokens) if tokens else 1.0


def _sentences(passage: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", passage.strip()) if s]


def _attach_trailing_citations(output: str) -> str:
    """'Sentence. [KB-X]' -> 'Sentence [KB-X].' so a citation after the period belongs to the
    sentence before it. Diagnostics only; methodology 2.0.0 claim splitting is unchanged."""
    return re.sub(
        r"([.!?])\s*((?:\[[^\]\n]{1,80}\]\s*)+)",
        lambda m: f" {m.group(2).strip()}{m.group(1)} ",
        output,
    ).strip()


def _warning(state: str, kind: str, claim: str, reason: str, evidence: dict | None = None) -> dict:
    return {
        "state": state,
        "kind": kind,
        "claim": claim,
        "evidence": evidence,
        "reason": reason,
        "heuristic": True,
    }


def diagnose(output: str, retrieved: Mapping[str, str], knowledge_base: Mapping[str, str]) -> dict:
    """Warnings for one answer, checked against THIS turn's retrieved passages."""
    result = {"version": DIAGNOSTICS_VERSION, "label": LABEL, "warnings": [], "claims_checked": 0}
    if is_abstention(output):
        result["note"] = "The answer declines; no claims to check."
        return result
    claims = split_claims(_attach_trailing_citations(output))
    result["claims_checked"] = len(claims)
    warnings = result["warnings"]
    evidence_sentences = [
        (pid, sentence, polarity(sentence))
        for pid, text in retrieved.items()
        for sentence in _sentences(text)
    ]

    polarities = [polarity(c.text) for c in claims]
    for i, claim in enumerate(claims):
        subject = _subject(claim.text)
        # 1. Claim vs evidence: opposite policy on the same subject.
        for group, stance in polarities[i].items():
            best_opp, best_same = (0, None), 0
            for pid, sentence, pol in evidence_sentences:
                if group not in pol:
                    continue
                overlap = len(subject & _subject(sentence))
                if pol[group] == stance:
                    best_same = max(best_same, overlap)
                elif overlap > best_opp[0]:
                    best_opp = (overlap, (pid, sentence))
            overlap, match = best_opp
            if match is None or overlap == 0 or overlap < best_same:
                continue
            pid, sentence = match
            shared = _words(claim.text, subject & _subject(sentence))
            opposite = "not allowed" if stance == "allowed" else "allowed"
            evidence = {"passage_id": pid, "text": sentence}
            if overlap >= 2 and overlap > best_same:
                warnings.append(
                    _warning(
                        POTENTIAL_CONTRADICTION,
                        "policy_polarity",
                        claim.text,
                        f"The answer says this is {_stance(group, stance)} ({group}); the "
                        f"passage says it is {_stance(group, opposite)} for a matching subject "
                        f"({shared}).",
                        evidence,
                    )
                )
            else:
                warnings.append(
                    _warning(
                        NEEDS_HUMAN_REVIEW,
                        "policy_polarity_uncertain",
                        claim.text,
                        f"The answer says this is {_stance(group, stance)} ({group}); a passage "
                        f"says the opposite for a related subject ({shared}), but the match is "
                        "too weak or too ambiguous to call a contradiction. Check scope and "
                        "conditions.",
                        evidence,
                    )
                )

        # 2. Claim vs another claim in the same answer.
        for j in range(i + 1, len(claims)):
            for group, stance in polarities[i].items():
                other = polarities[j].get(group)
                shared = subject & _subject(claims[j].text)
                if other and other != stance and len(shared) >= 2:
                    warnings.append(
                        _warning(
                            POTENTIAL_CONTRADICTION,
                            "self_contradiction",
                            claim.text,
                            f"Another sentence in this answer says the opposite ({group}) about "
                            f"the same subject ({_words(claim.text, shared)}): "
                            f'"{claims[j].text}"',
                            None,
                        )
                    )

        # 3. Citations.
        for cid in claim.citations:
            if cid not in knowledge_base:
                warnings.append(
                    _warning(
                        CITATION_CONCERN,
                        "citation_nonexistent",
                        claim.text,
                        f"{cid} is not a passage in this knowledge base.",
                    )
                )
            elif cid not in retrieved:
                warnings.append(
                    _warning(
                        CITATION_CONCERN,
                        "citation_not_retrieved",
                        claim.text,
                        f"{cid} was not retrieved for this turn, so the model never saw it.",
                        {"passage_id": cid, "text": knowledge_base[cid]},
                    )
                )
        cited = [c for c in claim.citations if c in retrieved]
        scores = {pid: _coverage(claim.text, text) for pid, text in retrieved.items()}
        best_pid = max(scores, key=lambda p: (scores[p], p == (cited[0] if cited else None)))
        if cited:
            cited_best = max(cited, key=lambda p: scores[p])
            gap = scores[best_pid] - scores[cited_best]
            if scores[cited_best] < MATCH_THRESHOLD and gap >= UNRELATED_MARGIN:
                numbers = extract_numbers(claim.text)
                note = ""
                if (
                    numbers
                    and not numbers <= extract_numbers(retrieved[cited_best])
                    and (numbers <= extract_numbers(retrieved[best_pid]))
                ):
                    listed = ", ".join(sorted(numbers, key=float))
                    note = f" Its numbers ({listed}) appear in {best_pid}."
                warnings.append(
                    _warning(
                        CITATION_CONCERN,
                        "citation_unrelated",
                        claim.text,
                        f"The cited passage {cited_best} shares few words with this sentence "
                        f"({scores[cited_best]:.0%}); {best_pid} matches much better "
                        f"({scores[best_pid]:.0%}).{note} Citations are not changed "
                        "automatically.",
                        {"passage_id": cited_best, "text": retrieved[cited_best]},
                    )
                )
        # 4. No retrieved passage matches the statement at all.
        if scores and scores[best_pid] < MATCH_THRESHOLD:
            warnings.append(
                _warning(
                    NEEDS_HUMAN_REVIEW,
                    "no_matching_evidence",
                    claim.text,
                    f"No retrieved passage matches this statement (best: {best_pid}, "
                    f"{scores[best_pid]:.0%} of its words). It may be unsupported, or a paraphrase "
                    "the word-matching cannot recognise.",
                    {"passage_id": best_pid, "text": retrieved[best_pid]},
                )
            )
    return result
