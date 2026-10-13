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
import time
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import regex
from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import SchemaError, ValidationError

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
MAX_SCHEMA_ERRORS = 5

# Regex safety for user-supplied JSON Schema patterns (catastrophic backtracking / ReDoS).
REGEX_TIMEOUT_S = 0.1  # per pattern match
MAX_PATTERN_LENGTH = 1000
MAX_PATTERNS_PER_SCHEMA = 50


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


# ---------------------------------------------------------------- safe regex evaluation


@dataclass
class _RegexBudget:
    remaining_s: float


_regex_budget: ContextVar[_RegexBudget | None] = ContextVar("regex_budget", default=None)


@contextmanager
def regex_time_budget(seconds: float) -> Iterator[None]:
    """Cap the total time spent matching schema patterns (e.g. across one evaluation run)."""
    token = _regex_budget.set(_RegexBudget(seconds))
    try:
        yield
    finally:
        _regex_budget.reset(token)


def _safe_pattern(validator, patrn, instance, schema):
    """Replacement for jsonschema's `pattern` keyword with a time limit (fails closed)."""
    if not validator.is_type(instance, "string"):
        return
    budget = _regex_budget.get()
    timeout = REGEX_TIMEOUT_S if budget is None else min(REGEX_TIMEOUT_S, budget.remaining_s)
    if timeout <= 0:
        yield ValidationError("regex time budget for this run exhausted; treated as a failure")
        return
    start = time.perf_counter()
    try:
        matched = regex.search(patrn, instance, timeout=timeout)
    except TimeoutError:
        yield ValidationError(
            f"pattern {patrn!r} exceeded the {timeout * 1000:.0f} ms evaluation limit; "
            "treated as a failure"
        )
        return
    finally:
        if budget is not None:
            budget.remaining_s -= time.perf_counter() - start
    if not matched:
        yield ValidationError(f"{instance!r} does not match {patrn!r}")


_SafeValidator = validators.extend(Draft202012Validator, {"pattern": _safe_pattern})


def _unsafe_schema_reason(schema: Any) -> str | None:
    """Return why a schema can't be evaluated safely, or None.

    `patternProperties` is rejected because jsonschema evaluates those regexes internally
    (also inside additionalProperties/unevaluatedProperties) without a time limit.
    """
    patterns: list[str] = []
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "patternProperties" in node:
                return "patternProperties is not supported (its regexes cannot be time-limited)"
            if isinstance(node.get("pattern"), str):
                patterns.append(node["pattern"])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    if len(patterns) > MAX_PATTERNS_PER_SCHEMA:
        return f"too many patterns ({len(patterns)} > {MAX_PATTERNS_PER_SCHEMA})"
    for p in patterns:
        if len(p) > MAX_PATTERN_LENGTH:
            return f"pattern longer than {MAX_PATTERN_LENGTH} characters"
        try:
            regex.compile(p)
        except regex.error as exc:
            return f"invalid regex pattern {p!r}: {exc}"
    return None


def check_json_schema_definition(schema: dict[str, Any]) -> None:
    """Raise ValueError if the schema is not a valid Draft 2020-12 schema or is unsafe."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid JSON schema: {exc.message}") from exc
    reason = _unsafe_schema_reason(schema)
    if reason:
        raise ValueError(f"unsupported JSON schema: {reason}")


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
    # Re-checked at evaluation time so schemas stored before these rules can't bypass them.
    reason = _unsafe_schema_reason(schema)
    if reason:
        return JsonCheck(parsed=True, valid=False, errors=[f"schema not evaluated: {reason}"])
    try:
        validator = _SafeValidator(schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    except Exception as exc:  # e.g. unresolvable $ref: fail the case, never crash the run
        return JsonCheck(
            parsed=True,
            valid=False,
            errors=[f"schema evaluation error: {type(exc).__name__}"],
        )
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
