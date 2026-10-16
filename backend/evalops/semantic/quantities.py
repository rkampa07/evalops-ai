"""Quantities with units, ranges and bounds, checked together with their conditions.

A claim's quantities count as verified only if ONE evidence sentence, among the sentences most
related to the claim's own content, states every one of them: the same unit (8 a.m. is not
8 p.m.), the same value or range, and the same bound ("within 5" is not "in 5"). Quantities
scattered across different sentences of a passage don't verify a claim, because each sentence
may attach them to a different condition (RET-03: "free over $75" vs "$9.95 for $75 or less").
If an equally relevant sentence states a competing value for the same kind of quantity, the
binding is ambiguous and the claim goes to review. Number words, fractions and bare numbers that
can't be parsed into a quantity also send the claim to review unless the identical word appears in
the verifying sentence. The customer's question is never a source of numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .segments import relevant_sentences

_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45, "fifty": 50, "sixty": 60,
    "ninety": 90, "hundred": 100,
}  # fmt: skip
# Words that express an amount but aren't parsed into a Quantity: never silently accepted.
_UNPARSED_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|half|halves|quarter|quarters|third|thirds|double|twice|triple|"
    r"dozen|dozens|several|multiple|unlimited|infinite|percent)\b",
    re.I,
)
_UNITS = {
    "business day": "business_day", "business days": "business_day",
    "day": "day", "days": "day", "week": "week", "weeks": "week",
    "month": "month", "months": "month",
    "year": "year", "years": "year", "hour": "hour", "hours": "hour", "minute": "minute",
    "minutes": "minute", "pound": "pound", "pounds": "pound", "point": "point", "points": "point",
    "percent": "percent", "%": "percent", "am": "clock_am", "a.m.": "clock_am", "pm": "clock_pm",
    "p.m.": "clock_pm", "mm": "mm",
}  # fmt: skip
_TO_DAYS = {"week": 7}  # exact conversions only ("a month" is not 30 days)

_NUM = (
    r"(?:\$?\d[\d,]*(?:\.\d+)?|"
    + "|".join(sorted(map(re.escape, _WORDS), key=len, reverse=True))
    + r")"
)
_UNIT = (
    r"(?:%|"
    + "|".join(sorted(map(re.escape, (u for u in _UNITS if u != "%")), key=len, reverse=True))
    + r")"
)
_QTY_RE = re.compile(
    rf"(?P<pre>\b(?:within|in|up to|at least|at most|more than|less than|over|under|after|before|"
    rf"between|no more than|not more than|no less than|not less than|no fewer than|no later than|"
    rf"for)\s+)?"
    rf"(?P<lo>(?<![\w.]){_NUM})(?:\s*(?:to|-|–|and)\s*(?P<hi>{_NUM}))?"
    rf"(?:[\s-]*(?P<unit>{_UNIT})(?!\w)|(?P<pct>%))?"
    rf"(?P<post>\s+or\s+(?:less|more|fewer|under|over))?",
    re.IGNORECASE,
)
_BOUND = {
    "within": "<=", "up to": "<=", "at most": "<=", "no more than": "<=", "less than": "<",
    "under": "<", "before": "<", "at least": ">=", "more than": ">", "over": ">", "after": ">",
    "or less": "<=", "or fewer": "<=", "or under": "<=", "or more": ">=", "or over": ">=",
    "not more than": "<=", "no less than": ">=", "not less than": ">=", "no fewer than": ">=",
    "no later than": "<=",
}  # fmt: skip
# What a duration is measured from ("within 30 days of delivery"). Only a few exact synonyms.
_ANCHOR_RE = re.compile(
    r"^\s*(?:of|from|after|since|following|before|upon)\s+(?:the\s+|your\s+|its\s+|their\s+|"
    r"an?\s+|my\s+)?([a-z]+)",
    re.I,
)
_ANCHOR_SYNONYMS = {
    "delivery": "delivery", "delivered": "delivery", "arrival": "delivery", "arrives": "delivery",
    "purchase": "purchase", "purchased": "purchase", "buying": "purchase", "order": "purchase",
    "ordering": "purchase", "return": "return", "returned": "return", "returns": "return",
}  # fmt: skip
_DURATION_UNITS = {"day", "business_day", "hour", "minute", "month", "year"}


@dataclass(frozen=True)
class Quantity:
    low: float
    high: float
    unit: str | None  # None = a bare number (unverifiable unless the same bare value appears)
    bound: str  # "=", "<=", "<", ">=", ">"
    money: bool
    text: str
    span: tuple[int, int]
    anchor: str | None = None  # what a duration is measured from, normalised


def _value(token: str) -> float | None:
    token = token.lower().replace("$", "").replace(",", "")
    if token in _WORDS:
        return float(_WORDS[token])
    try:
        return float(token)
    except ValueError:
        return None


def quantities(text: str) -> list[Quantity]:
    found = []
    for m in _QTY_RE.finditer(text):
        lo = _value(m["lo"])
        if lo is None:
            continue
        hi = _value(m["hi"]) if m["hi"] else lo
        unit_word = (m["unit"] or ("%" if m["pct"] else "")).lower()
        unit = _UNITS.get(unit_word)
        money = m["lo"].startswith("$")
        # Number words without a unit aren't parsed here ("a photo", "one-time"); amount words
        # are caught separately by unparsed_amounts().
        if unit is None and not money and not re.match(r"[\d$]", m["lo"]):
            continue
        if unit in _TO_DAYS:
            lo, hi, unit = lo * _TO_DAYS[unit], hi * _TO_DAYS[unit], "day"
        pre = (m["pre"] or "").strip().lower()
        post = re.sub(r"\s+", " ", (m["post"] or "").strip().lower())
        bound = _BOUND.get(post) or _BOUND.get(pre) or "="
        if pre in ("in", "between", "for") or (pre == "within" and hi != lo):
            bound = "="  # "in 7 to 12 days" / "within 7 to 12 days" both state the range
        anchor = None
        if unit in _DURATION_UNITS:
            a = _ANCHOR_RE.match(text[m.end() :])
            if a:
                word = a.group(1).lower()
                anchor = _ANCHOR_SYNONYMS.get(word, word)
        found.append(
            Quantity(
                lo,
                hi,
                "money" if money else unit,
                bound,
                money,
                m.group(0).strip(),
                m.span(),
                anchor,
            )
        )
    return found


def unparsed_amounts(text: str) -> list[str]:
    """Amount words outside any parsed quantity ("two replacement parts", "half the fee")."""
    spans = [q.span for q in quantities(text)]
    return [
        m.group(0)
        for m in _UNPARSED_WORDS.finditer(text)
        if not any(a <= m.start() < b for a, b in spans)
    ]


def _matches(q: Quantity, e: Quantity) -> bool:
    # Only identical bounds are safe: "within 5 days" (<=5) and "in 5 days" (=5) differ.
    # A claim that says what a duration is measured from must name the same starting point.
    same = e.unit == q.unit and (e.low, e.high) == (q.low, q.high) and e.bound == q.bound
    return same and (q.anchor is None or q.anchor == e.anchor)


def unverified(claim: str, evidence: str) -> list[str]:
    """Claim quantities that `evidence` (treated as one statement) doesn't state identically."""
    stated = quantities(evidence)
    missing = []
    for q in quantities(claim):
        same_value = [e for e in stated if e.unit == q.unit and (e.low, e.high) == (q.low, q.high)]
        if q.unit is None and not same_value:
            missing.append(f"{q.text} (no unit; can't verify)")
        elif not same_value:
            missing.append(q.text)
        elif not any(_matches(q, e) for e in same_value):
            anchors = {e.anchor for e in same_value if e.bound == q.bound}
            if q.anchor and anchors:
                stated = ", ".join(sorted(a or "unstated" for a in anchors))
                missing.append(
                    f"{q.text} (measured from {q.anchor}; the evidence measures from {stated})"
                )
            else:
                missing.append(f"{q.text} (different bound from the evidence)")
    evidence_words = {w.lower() for w in unparsed_amounts(evidence)}
    missing += [f"{w} (amount word; can't verify)" for w in unparsed_amounts(claim)
                if w.lower() not in evidence_words]  # fmt: skip
    return missing


def binding_problems(claim: str, unit_text: str) -> list[str]:
    """[] if one of the sentences most related to the claim states all its quantities with no
    equally related sentence stating a competing value; otherwise the reasons for review."""
    if not quantities(claim) and not unparsed_amounts(claim):
        return []
    candidates = relevant_sentences(claim, unit_text) or [unit_text]
    verifying = [s for s in candidates if not unverified(claim, s)]
    if not verifying:
        best = min(candidates, key=lambda s: len(unverified(claim, s)))
        return unverified(claim, best) + [
            "the quantities aren't all stated in the evidence sentence about this claim"
        ]
    claim_units = {q.unit for q in quantities(claim)}
    subject = _subject_binding(claim, verifying)
    if subject:
        return subject
    competing = [
        e.text
        for s in candidates
        if s not in verifying
        for e in quantities(s)
        if e.unit in claim_units
    ]
    if competing:
        return [
            "ambiguous: an equally related sentence states a different "
            f"{'/'.join(sorted(u or 'number' for u in claim_units))} ({', '.join(competing)})"
        ]
    return []


_FRAGMENT_SPLIT = re.compile(r"\s*(?:[,;:]|\band\b|\bwhile\b|\bwhereas\b)\s*", re.I)


def _subject_binding(claim: str, verifying: list[str]) -> list[str]:
    """In a sentence listing several values of the same kind ("$5 standard shipping, $15 express
    shipping"), the claim's value must sit in the fragment that best matches the claim's own words.
    Otherwise -> review. [] if some verifying sentence binds every claim quantity this way."""
    from ..engine.rag import content_tokens

    claim_qs = quantities(claim)
    claim_tokens = set(content_tokens(claim))
    reasons = []
    for sentence in verifying:
        fragments = [f for f in _FRAGMENT_SPLIT.split(sentence) if f.strip()]
        ok = True
        for q in claim_qs:
            hits, rivals = [], []
            for f in fragments:
                overlap = len(claim_tokens & set(content_tokens(f)))
                for e in quantities(f):
                    if e.unit != q.unit:
                        continue
                    if _matches(q, e):
                        hits.append((overlap, f))
                    elif e.bound == q.bound and not any(_matches(other, e) for other in claim_qs):
                        # Same kind of value in the same role (two prices, not a threshold vs a
                        # fee) that the claim doesn't state at all.
                        rivals.append((overlap, f))
            if not rivals:
                continue
            best_hit = max((o for o, _ in hits), default=-1)
            best_rival = max(o for o, _ in rivals)
            if best_hit <= best_rival:
                ok = False
                listed = ", ".join(sorted({f.strip() for _, f in rivals}))
                reasons.append(
                    f'{q.text}: "{sentence.strip()}" lists another {q.unit} value ({listed}), '
                    "and the claim's wording doesn't clearly attach its value to the right item"
                )
        if ok:
            return []
    return reasons


# Opposite conditions: the two never cover the same value. An exact value is outside a strict
# threshold at that value ("exactly $100" vs "over $100"), but inside an inclusive one.
_COMPLEMENT = {
    (">", "<="), ("<=", ">"), ("<", ">="), (">=", "<"),
    ("=", ">"), (">", "="), ("=", "<"), ("<", "="),
}  # fmt: skip


# Wording that makes a condition exclusive: such a sentence doesn't just describe another case,
# it rules the claim's case out ("only within 30 days", "no later than 30 days").
EXCLUSIVE_CONDITION_RE = re.compile(
    r"\b(only|exclusively|solely|no later than|not later than|at the latest|no earlier than|"
    r"not before|not after|must|cannot|can't|never|not|expire\w*|reject\w*|declin\w*|"
    r"restrict\w*|ineligible)\b",
    re.I,
)


def complementary_only(claim: str, text: str) -> bool:
    """True if the text states a claim value only under the opposite condition ("$75 or less"
    vs the claim's "over $75"), never under the claim's own condition, and without exclusive
    wording ("only", "no later than", ...). It then describes a different case instead of
    ruling the claim's case out."""
    # Multiple clauses can describe overlapping cases even when one threshold is opposite (for
    # example, a separate weekend rule). Keep those contradictions for normal adjudication.
    if re.search(r";|\.\s|,\s+(?:and|then)\b|\bwhereas\b", text, re.I):
        return False
    if EXCLUSIVE_CONDITION_RE.search(text):
        return False
    claim_qs, evidence_qs = quantities(claim), quantities(text)
    condition_qs = [
        (index, q)
        for index, q in enumerate(claim_qs)
        if q.bound != "="
        or re.search(r"\bexactly\s*$", claim[max(0, q.span[0] - 40) : q.span[0]], re.I)
        or re.search(
            r"\b(?:orders?|stays?|purchases?)\s+(?:of\s+)?(?:exactly\s+)?$",
            claim[max(0, q.span[0] - 40) : q.span[0]],
            re.I,
        )
    ]
    if not condition_qs:
        return False
    complementary = same = False
    matched_claim_values: set[int] = set()
    for index, q in condition_qs:
        for e in evidence_qs:
            if e.unit != q.unit or (e.low, e.high) != (q.low, q.high):
                continue
            matched_claim_values.add(index)
            if (q.bound, e.bound) in _COMPLEMENT:
                complementary = True
            elif e.bound == q.bound:
                same = True
    # A mixed-range claim may have one opposite boundary and another overlapping policy
    # condition. Do not screen that passage as wholly complementary unless it accounts for all
    # of the claim's quantity values.
    return complementary and not same and len(matched_claim_values) == len(condition_qs)
