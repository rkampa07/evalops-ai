"""Focused demo replay checks; no server, inference, or recording writes."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "backend/scripts/rag_demo.py"
spec = importlib.util.spec_from_file_location("rag_demo", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.mark.parametrize("version", ["1.3.0", "1.4.0", "2.0.0"])
def test_demo_reuses_only_current_methodology(monkeypatch, version):
    recording = json.loads(demo.BASELINE.read_text())
    existing = {
        "id": 7,
        "model_name": recording["model_name"],
        "prompt_version": recording["prompt_version"],
        "generation": recording["generation"],
        "methodology_version": version,
    }
    created = {
        **existing,
        "id": 8,
        "methodology_version": "2.0.0",
        "summary": {
            "passed_cases": 9,
            "total_cases": 20,
            "groundedness": 0.777778,
            "abstention_accuracy": 0.9,
        },
    }
    submissions = []

    def call(base, method, endpoint, body=None):
        if method == "GET":
            return {"items": [existing]}
        assert method == "POST" and endpoint == "/evaluations/runs"
        assert body["responses"] == recording["responses"]
        submissions.append(body)
        return created

    monkeypatch.setattr(demo, "call", call)
    result = demo.ensure_run("http://unused", 1, demo.BASELINE)
    assert result["id"] == (7 if version == "2.0.0" else 8)
    assert len(submissions) == (0 if version == "2.0.0" else 1)
    assert existing["methodology_version"] == version
