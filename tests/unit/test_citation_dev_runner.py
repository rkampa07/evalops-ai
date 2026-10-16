"""Synthetic integration tests for the isolated DEV citation evaluation runner."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "tools/semantic_eval/run_citation_dev.py"
SPEC = importlib.util.spec_from_file_location("run_citation_dev", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def sample_example(**overrides):
    example = {
        "id": "synthetic-1",
        "category": "synthetic-category",
        "question": "What is the return window?",
        "claim": "Items can be returned within 30 days.",
        "cited_passage_ids": ["P1"],
        "retrieved_passage_ids": ["P1"],
        "evidence": {"P1": "Items can be returned within 30 days."},
        # These fields are deliberate poison: the predictor must never receive them.
        "expected_label": "poison-claim-label",
        "deciding_passage_ids": ["GOLD-PASSAGE"],
        "explanation": "poison-review-explanation",
        "reviewer_note": "poison-reviewer-note",
    }
    example.update(overrides)
    return example


def test_split_reader_selects_only_dev_ids(tmp_path):
    path = tmp_path / "split.json"
    path.write_text(json.dumps({"dev-a": "dev", "test-a": "test", "dev-b": "dev"}))
    assert runner.read_dev_ids(path) == ("dev-a", "dev-b")


def test_predictor_inputs_are_allowlisted_and_exclude_gold_and_review_fields(monkeypatch):
    seen = []

    def record_input(value):
        seen.append(value)
        return runner.predictor.CitationPrediction(
            runner.predictor.VERSION,
            runner.predictor.CitationStatus.CORRECT,
            "synthetic_reason",
            "synthetic reason",
            (),
        )

    monkeypatch.setattr(runner.predictor, "predict_citation_quality", record_input)
    example = sample_example()
    rows = runner.build_predictions([example], ("synthetic-1",), frozenset({"P1"}))
    assert len(rows) == 1
    assert len(seen) == 1
    assert seen[0].claim_text == example["claim"]
    assert seen[0].citation_ids == ("P1",)
    assert seen[0].retrieved_passages == example["evidence"]
    assert not any(
        secret in repr(seen[0])
        for secret in ("poison-claim-label", "GOLD-PASSAGE", "poison-review-explanation", "poison-reviewer-note")
    )
    assert set(rows[0]["predictor_input"]) == {
        "claim_text", "question_text", "citation_ids", "retrieved_passages",
        "knowledge_base_ids", "citation_expected",
    }


def pred_row(example_id, status, reason="synthetic"):
    return {
        "id": example_id,
        "category": "synthetic",
        "predictor_input": {"claim_text": "synthetic", "citation_ids": ["P"]},
        "prediction": {
            "version": runner.predictor.VERSION,
            "status": status,
            "reason_code": reason,
            "reason": "synthetic reason",
            "per_citation": [],
        },
    }


def test_scorer_covers_five_classes_and_counts_non_applicable_separately():
    labels = runner.FIVE_CLASSES
    rows = [pred_row(f"id-{i}", label) for i, label in enumerate(labels)]
    rows.append(pred_row("n-a", "not_applicable"))
    enriched, metrics = runner.score_predictions(
        rows,
        {f"id-{i}": label for i, label in enumerate(labels)} | {"n-a": None},
    )
    assert metrics["applicable_count"] == 5
    assert metrics["non_applicable_count"] == 1
    assert metrics["accuracy"] == 1.0
    assert metrics["coverage"] == 1.0
    assert all(metrics["confusion_matrix"][label][label] == 1 for label in labels)
    assert all(metrics["per_class"][label]["f1"] == 1.0 for label in labels)
    assert enriched[-1]["gold_citation"] is None


def test_abstentions_reduce_all_case_accuracy_and_coverage_but_remain_outside_matrix():
    rows = [
        pred_row("a", "correct"),
        pred_row("b", "unresolved", "relevance_ambiguous"),
        pred_row("c", "missing_citation", "expected_citation_missing"),
        pred_row("d", "invalid_citation", "unknown_passage_id"),
    ]
    _, metrics = runner.score_predictions(
        rows,
        {"a": "correct", "b": "wrong_passage", "c": "partial", "d": "unrelated"},
    )
    assert metrics["accuracy"] == 0.25
    assert metrics["coverage"] == 0.25
    assert metrics["selective_accuracy"] == 1.0
    assert metrics["special_status_counts"] == {
        "not_applicable": 0,
        "missing_citation": 1,
        "invalid_citation": 1,
        "unresolved": 1,
    }
    assert sum(sum(row.values()) for row in metrics["confusion_matrix"].values()) == 1


def test_prediction_and_gold_ids_must_match_before_scoring():
    rows = [pred_row("one", "correct")]
    try:
        runner.score_predictions(rows, {"two": "correct"})
    except ValueError as exc:
        assert "IDs must match exactly" in str(exc)
    else:
        raise AssertionError("scorer accepted mismatched IDs")
