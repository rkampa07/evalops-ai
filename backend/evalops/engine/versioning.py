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
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

METHODOLOGY_VERSION = "1.1.0"

# Fields that determine how a response is scored. Tags and key are identity/labels, not scoring.
DEFINITION_FIELDS = (
    "input",
    "expected_output",
    "required_terms",
    "forbidden_terms",
    "json_schema",
    "max_latency_ms",
)


def case_definition(case: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Extract the scoring-relevant definition of a test case (from a mapping or ORM object)."""

    def get(field: str) -> Any:
        return case[field] if isinstance(case, Mapping) else getattr(case, field)

    latency = get("max_latency_ms")
    return {
        "input": get("input"),
        "expected_output": get("expected_output"),
        "required_terms": list(get("required_terms") or []),
        "forbidden_terms": list(get("forbidden_terms") or []),
        "json_schema": get("json_schema"),
        "max_latency_ms": float(latency) if latency is not None else None,
    }


def definition_hash(definition: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON encoding of a case definition."""
    canonical = json.dumps(
        {f: definition[f] for f in DEFINITION_FIELDS},
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
