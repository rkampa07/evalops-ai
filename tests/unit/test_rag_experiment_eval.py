"""Focused replay/CLI checks; input recordings and saved experiment reports are read-only."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RAG = ROOT / "examples/rag"
spec = importlib.util.spec_from_file_location(
    "rag_experiment_eval", ROOT / "backend/scripts/rag_experiment_eval.py"
)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)

REPLAYS = [
    ("rag-v4", "responses/qwen2.5-1.5b_rag-v4.json", "kestrel_policy_kb.json", "development_set"),
    ("rag-v5", "responses/qwen2.5-1.5b_rag-v5.json", "kestrel_policy_kb.json", "development_set"),
    (
        "rag-v4",
        "prospective/responses/qwen2.5-1.5b_rag-v4_prospective.json",
        "prospective/kestrel_prospective_unanswerable.json",
        "prospective_check",
    ),
    (
        "rag-v5",
        "prospective/responses/qwen2.5-1.5b_rag-v5_prospective_v2.json",
        "prospective/kestrel_prospective_v2.json",
        "prospective_check",
    ),
]


@pytest.mark.parametrize("version,recording,dataset,section", REPLAYS)
def test_saved_experiment_reproduces_without_input_writes(
    version, recording, dataset, section, capsys
):
    paths = [RAG / recording, RAG / dataset, RAG / f"experiments/{version}_evaluation.json"]
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    assert (
        replay.main(
            [
                "--recording",
                str(paths[0]),
                "--dataset",
                str(paths[1]),
                "--experiment",
                str(paths[2]),
                "--section",
                section,
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["matches"] is True and output["differences"] == {}
    assert output["methodology_version"] == "2.0.0"
    expected = json.loads(paths[2].read_text())[section]
    if section == "development_set":
        expected = expected[version]
    assert output["compared_fields"] == sorted(expected)
    assert before == [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]


def test_changed_saved_metric_reports_difference_and_nonzero_exit(tmp_path, capsys):
    experiment = json.loads((RAG / "experiments/rag-v4_evaluation.json").read_text())
    experiment["development_set"]["rag-v4"]["pass_rate"] = 0.99
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(experiment))
    assert (
        replay.main(
            [
                "--recording",
                str(RAG / "responses/qwen2.5-1.5b_rag-v4.json"),
                "--dataset",
                str(RAG / "kestrel_policy_kb.json"),
                "--experiment",
                str(path),
            ]
        )
        == 1
    )
    output = json.loads(capsys.readouterr().out)
    assert output["differences"] == {
        "pass_rate": {"expected": 0.99, "actual": 0.55, "actual_present": True}
    }


def test_unknown_saved_field_is_not_silently_skipped():
    assert replay.differences({}, {"unknown": None}) == {
        "unknown": {"expected": None, "actual": None, "actual_present": False}
    }


def test_experiment_methodology_mismatch_is_rejected(tmp_path, capsys):
    experiment = json.loads((RAG / "experiments/rag-v4_evaluation.json").read_text())
    experiment["methodology_version"] = "1.4.0"
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(experiment))
    with pytest.raises(SystemExit) as exc:
        replay.main(
            [
                "--recording",
                str(RAG / "responses/qwen2.5-1.5b_rag-v4.json"),
                "--dataset",
                str(RAG / "kestrel_policy_kb.json"),
                "--experiment",
                str(path),
            ]
        )
    assert exc.value.code == 2
    assert "methodology differs" in capsys.readouterr().err


def inputs():
    return (
        {
            "config": {"min_groundedness": 0.8},
            "responses": [
                {
                    "case_key": "one",
                    "output": "Returns within 30 days [KB-RET-01].",
                    "retrieved_passage_ids": ["KB-RET-01"],
                },
            ],
        },
        {
            "test_cases": [{"key": "one", "relevant_passage_ids": ["KB-RET-01"]}],
            "passages": [{"passage_id": "KB-RET-01", "text": "Returns within 30 days."}],
        },
    )


@pytest.mark.parametrize("problem", ["duplicate", "unknown", "unknown_gold", "unknown_retrieved"])
def test_ambiguous_or_unknown_references_are_rejected(problem):
    recording, dataset = inputs()
    if problem == "duplicate":
        recording["responses"].append(recording["responses"][0].copy())
    elif problem == "unknown":
        recording["responses"][0]["case_key"] = "unknown"
    elif problem == "unknown_gold":
        dataset["test_cases"][0]["relevant_passage_ids"] = ["UNKNOWN-1"]
    else:
        recording["responses"][0]["retrieved_passage_ids"] = ["UNKNOWN-1"]
    with pytest.raises(ValueError, match="duplicate|unknown"):
        replay.evaluate(recording, dataset)


def test_missing_responses_still_count_as_failures():
    recording, dataset = inputs()
    recording["responses"] = []
    metrics = replay.evaluate(recording, dataset)["metrics"]
    assert metrics["passed"] == "0/1"
    assert metrics["failures"] == {"one": ["missing_response"]}
    assert metrics["answers_with_citation"] == "0/0"


def test_methodology_drift_is_rejected(monkeypatch):
    recording, dataset = inputs()
    monkeypatch.setattr(replay, "METHODOLOGY_VERSION", "2.1.0")
    with pytest.raises(ValueError, match="requires scorer 2.0.0"):
        replay.evaluate(recording, dataset)


def test_help_explains_read_only_scope(capsys):
    with pytest.raises(SystemExit) as exc:
        replay.main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--recording" in help_text and "--dataset" in help_text
    assert "no file writes" in help_text or "file writes" in help_text
    assert "release gates are not evaluated" in help_text
