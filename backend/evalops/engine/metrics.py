"""Deterministic, text-level metrics for a single response.

Everything here is a pure function with no I/O, so scores are reproducible.

These metrics measure *lexical overlap* and *structural conformance*. They do not
measure factual correctness, and they do not detect hallucinations. A fluent wrong
answer that reuses the reference wording can score highly. See
docs/evaluation-methodology.md for the limits.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
MAX_SCHEMA_ERRORS = 5


def normalize_text(text: str, *, case_sensitive: bool = False) -> str:
    """NFKC-normalize, optionally casefold, strip punctuation, and collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    if not case_sensitive:
        text = text.casefold()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def tokenize(text: str, *, case_sensitive: bool = False) -> list[str]:
    normalized = normalize_text(text, case_sensitive=case_sensitive)
    return normalized.split(" ") if normalized else []


def exact_match(prediction: str, reference: str, *, case_sensitive: bool = False) -> bool:
    return normalize_text(prediction, case_sensitive=case_sensitive) == normalize_text(
        reference, case_sensitive=case_sensitive
    )


@dataclass(frozen=True)
class TokenScores:
    precision: float
    recall: float
    f1: float


def token_scores(prediction: str, reference: str, *, case_sensitive: bool = False) -> TokenScores:
    """Compute bag-of-tokens precision, recall, and F1 (SQuAD-style, multiset overlap)."""
    pred = tokenize(prediction, case_sensitive=case_sensitive)
    ref = tokenize(reference, case_sensitive=case_sensitive)
    if not pred and not ref:
        return TokenScores(1.0, 1.0, 1.0)
    if not pred or not ref:
        return TokenScores(0.0, 0.0, 0.0)
    overlap = sum((Counter(pred) & Counter(ref)).values())
    if overlap == 0:
        return TokenScores(0.0, 0.0, 0.0)
    precision = overlap / len(pred)
    recall = overlap / len(ref)
    f1 = 2 * precision * recall / (precision + recall)
    return TokenScores(precision, recall, f1)


def contains_term(text: str, term: str, *, case_sensitive: bool = False) -> bool:
    """Phrase match on normalized token boundaries ("refund" does not match "refunded")."""
    norm_term = normalize_text(term, case_sensitive=case_sensitive)
    if not norm_term:
        return False
    norm_text = normalize_text(text, case_sensitive=case_sensitive)
    return f" {norm_term} " in f" {norm_text} "


def missing_terms(text: str, terms: list[str], *, case_sensitive: bool = False) -> list[str]:
    return [t for t in terms if not contains_term(text, t, case_sensitive=case_sensitive)]


def present_terms(text: str, terms: list[str], *, case_sensitive: bool = False) -> list[str]:
    return [t for t in terms if contains_term(text, t, case_sensitive=case_sensitive)]


def check_json_schema_definition(schema: dict[str, Any]) -> None:
    """Raise ValueError if the schema itself is not a valid Draft 2020-12 schema."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid JSON schema: {exc.message}") from exc


@dataclass(frozen=True)
class JsonCheck:
    parsed: bool
    valid: bool
    errors: list[str]


def validate_json_output(output: str, schema: dict[str, Any]) -> JsonCheck:
    """Parse the output as JSON and validate it against the schema."""
    try:
        instance = json.loads(output.strip())
    except (json.JSONDecodeError, ValueError) as exc:
        return JsonCheck(parsed=False, valid=False, errors=[f"output is not valid JSON: {exc.msg}"])
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    messages = []
    for err in errors[:MAX_SCHEMA_ERRORS]:
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"{path}: {err.message}")
    return JsonCheck(parsed=True, valid=not errors, errors=messages)


def percentile(values: list[float], pct: float) -> float | None:
    """Percentile with linear interpolation between closest ranks, the same as numpy's default."""
    if not values:
        return None
    if not 0 <= pct <= 100:
        raise ValueError("pct must be within [0, 100]")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] + (ordered[high] - ordered[low]) * frac)
