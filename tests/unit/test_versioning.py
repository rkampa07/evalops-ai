import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from evalops.engine.versioning import (
    METHODOLOGY_VERSION,
    case_definition,
    definition_hash,
    methodology_compatibility,
)

CASE = {
    "input": "q",
    "expected_output": "a",
    "required_terms": ["x"],
    "forbidden_terms": [],
    "json_schema": {"type": "object", "required": ["b", "a"]},
    "max_latency_ms": 1500,
    "tags": ["t"],
    "key": "k",
}


def test_hash_is_stable_and_ignores_labels():
    h = definition_hash(case_definition(CASE))
    assert len(h) == 64
    assert definition_hash(case_definition(CASE | {"tags": ["other"], "key": "z"})) == h
    # int vs float latency normalizes to the same definition
    assert definition_hash(case_definition(CASE | {"max_latency_ms": 1500.0})) == h


@pytest.mark.parametrize(
    "change",
    [
        {"input": "q2"},
        {"expected_output": "b"},
        {"required_terms": ["y"]},
        {"forbidden_terms": ["z"]},
        {"json_schema": {"type": "string"}},
        {"max_latency_ms": 900},
    ],
)
def test_every_scoring_field_changes_the_hash(change):
    assert definition_hash(case_definition(CASE | change)) != definition_hash(case_definition(CASE))


def test_methodology_compatibility():
    assert methodology_compatibility("1.1.0", "1.1.0") is None
    assert methodology_compatibility("1.0.0", "1.1.0") == "warning"
    assert methodology_compatibility("1.1.0", "2.0.0") == "error"
    assert methodology_compatibility("garbage", "1.1.0") == "error"
    assert METHODOLOGY_VERSION == "2.0.0"


def test_migration_hash_matches_engine_hash():
    """Migration 0002 carries a frozen copy of the hash; it must agree with the engine."""
    path = Path(__file__).parents[2] / "backend/migrations/versions/0002_version_tracking.py"
    spec = importlib.util.spec_from_file_location("migration_0002", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import json

    row = SimpleNamespace(
        input=CASE["input"],
        expected_output=CASE["expected_output"],
        required_terms=json.dumps(CASE["required_terms"]),  # SQLite returns JSON as text
        forbidden_terms=json.dumps(CASE["forbidden_terms"]),
        json_schema=json.dumps(CASE["json_schema"]),
        max_latency_ms=1500.0,
    )
    assert module._hash(module._definition(row)) == definition_hash(case_definition(CASE))
