"""Scoring-methodology version and test-case definition fingerprints.

METHODOLOGY_VERSION follows semantic versioning for the *scoring rules*:
- MAJOR: a change that can alter scores for the same inputs. Runs scored under different major
  versions are not comparable.
- MINOR: additive or robustness changes (new metrics, safer evaluation of pathological inputs)
  that leave scores for well-formed inputs unchanged. Comparable, with a warning.
- PATCH: no scoring impact.

History:
- 1.0.0  initial methodology
- 1.1.0  JSON Schema `pattern` evaluated with a time limit; `patternProperties` rejected
- 1.2.0  RAG evidence checks (retrieval, citations, lexical groundedness, abstention); additive,
         scores of non-RAG cases are unchanged
- 1.3.0  RAG answers with no checkable statement (empty or only citation markers) fail a
         "content" check. Found while analysing rag-v3; stricter only for degenerate output,
         scores of well-formed answers are unchanged (pinned by tests/regression)
- 1.4.0  Fix 1.3.0 over-rejection: the "content" check now fails only empty, punctuation-only
         or citation-only answers. Short answers with no extractable claim ("30 days
         [KB-RET-01].") and non-Latin answers no longer fail it (they pass, as under 1.2.0)
- 2.0.0  MAJOR: short segments containing a number are now claims ("60 days [KB-RET-01]." fails
         groundedness instead of passing unchecked), and numbered-list markers are removed
         before sentence splitting. Can change scores for the same inputs, so 1.x runs are
         not comparable with 2.x runs
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

METHODOLOGY_VERSION = "2.0.0"

# Fields that determine how a response is scored. Tags and key are identity/labels, not scoring.
DEFINITION_FIELDS = (
    "input",
    "expected_output",
    "required_terms",
    "forbidden_terms",
    "json_schema",
    "max_latency_ms",
)
# Added in methodology 1.2.0. Hashed only when set to a non-default value, so definitions of
# pre-existing (non-RAG) cases keep exactly the same hash.
OPTIONAL_DEFINITION_FIELDS = {"relevant_passage_ids": [], "expect_abstain": False}


def case_definition(case: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Extract the scoring-relevant definition of a test case (from a mapping or ORM object)."""

    def get(field: str) -> Any:
        return case[field] if isinstance(case, Mapping) else getattr(case, field)

    def get_optional(field: str, default: Any) -> Any:
        if isinstance(case, Mapping):
            return case.get(field, default)
        return getattr(case, field, default)

    latency = get("max_latency_ms")
    definition = {
        "input": get("input"),
        "expected_output": get("expected_output"),
        "required_terms": list(get("required_terms") or []),
        "forbidden_terms": list(get("forbidden_terms") or []),
        "json_schema": get("json_schema"),
        "max_latency_ms": float(latency) if latency is not None else None,
    }
    for field, default in OPTIONAL_DEFINITION_FIELDS.items():
        value = get_optional(field, default)
        value = list(value) if isinstance(default, list) else bool(value)
        if value != default:
            definition[field] = value
    return definition


def definition_hash(definition: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON encoding of a case definition."""
    fields = [*DEFINITION_FIELDS, *(f for f in OPTIONAL_DEFINITION_FIELDS if f in definition)]
    canonical = json.dumps(
        {f: definition[f] for f in fields},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(p) for p in version.split("."))
    return major, minor, patch


def methodology_compatibility(baseline: str, candidate: str) -> str | None:
    """Return None if identical, "warning" if only minor/patch differ, "error" otherwise."""
    if baseline == candidate:
        return None
    try:
        b, c = _parse(baseline), _parse(candidate)
    except ValueError:
        return "error"
    return "warning" if b[0] == c[0] else "error"


def knowledge_base_hash(passages: Mapping[str, str]) -> str | None:
    """SHA-256 over passage ids and texts (titles are labels). None for an empty KB."""
    if not passages:
        return None
    canonical = json.dumps(sorted(passages.items()), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
