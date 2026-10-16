"""Run the frozen deterministic citation evaluator on DEV only; never invokes a model."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/semantic-eval"
BENCHMARK_PATH = EXPERIMENT / "benchmark.jsonl"
SPLIT_PATH = EXPERIMENT / "split.json"
REVIEWS_PATH = EXPERIMENT / "human_review_decisions.csv"
KB_PATH = ROOT / "examples/rag/kestrel_policy_kb.json"
RESULTS_DIR = EXPERIMENT / "results"
FIVE_CLASSES = ("correct", "wrong_passage", "unrelated", "not_retrieved", "partial")
SPECIAL_STATUSES = ("not_applicable", "missing_citation", "invalid_citation", "unresolved")


def _load_predictor_module():
    path = EXPERIMENT / "citation_evaluator.py"
    spec = importlib.util.spec_from_file_location("citation_evaluator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load citation predictor at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


predictor = _load_predictor_module()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_hash(value: Any) -> str:
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def read_jsonl(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_dev_ids(path: Path = SPLIT_PATH) -> tuple[str, ...]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not data or any(value not in {"dev", "test"} for value in data.values()):
        raise ValueError("split.json must map unique example IDs to dev or test")
    ids = tuple(example_id for example_id, split in data.items() if split == "dev")
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("split.json must contain a non-empty unique DEV split")
    return ids


def build_predictions(
    examples: list[dict[str, Any]], dev_ids: tuple[str, ...], kb_ids: frozenset[str]
) -> list[dict[str, Any]]:
    """Construct predictor inputs from an explicit allowlist, then predict without gold fields."""
    by_id = {example.get("id"): example for example in examples}
    if len(by_id) != len(examples):
        raise ValueError("Benchmark IDs must be unique")
    missing = set(dev_ids) - by_id.keys()
    if missing:
        raise ValueError(f"DEV IDs missing from benchmark: {sorted(missing)}")

    rows: list[dict[str, Any]] = []
    for example_id in dev_ids:
        example = by_id[example_id]
        citation_ids = tuple(example["cited_passage_ids"])
        retrieved_ids = tuple(example["retrieved_passage_ids"])
        evidence = example["evidence"]
        if set(retrieved_ids) != set(evidence):
            raise ValueError(f"{example_id}: retrieved IDs and evidence keys differ")
        retrieved = {pid: evidence[pid] for pid in retrieved_ids}
        predictor_input = predictor.CitationInput(
            claim_text=example["claim"],
            citation_ids=citation_ids,
            retrieved_passages=retrieved,
            knowledge_base_ids=kb_ids,
            question_text=example["question"],
            citation_expected=bool(citation_ids),
        )
        prediction = predictor.predict_citation_quality(predictor_input)
        rows.append(
            {
                "id": example_id,
                "category": example["category"],
                "predictor_input": {
                    "claim_text": predictor_input.claim_text,
                    "question_text": predictor_input.question_text,
                    "citation_ids": list(predictor_input.citation_ids),
                    "retrieved_passages": dict(predictor_input.retrieved_passages),
                    "knowledge_base_ids": sorted(predictor_input.knowledge_base_ids),
                    "citation_expected": predictor_input.citation_expected,
                },
                "prediction": {
                    "version": prediction.version,
                    "status": str(prediction.status),
                    "reason_code": prediction.reason_code,
                    "reason": prediction.reason,
                    "per_citation": [
                        {
                            "citation_id": item.citation_id,
                            "status": str(item.status),
                            "reason_code": item.reason_code,
                            "reason": item.reason,
                            "component_coverage": list(item.component_coverage),
                        }
                        for item in prediction.per_citation
                    ],
                },
            }
        )
    return rows


def read_dev_gold(dev_ids: tuple[str, ...], path: Path = REVIEWS_PATH) -> dict[str, str | None]:
    """Resolve only DEV citation decisions; this function is called after predictions exist."""
    wanted = set(dev_ids)
    gold: dict[str, str | None] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            example_id = row.get("id", "")
            if example_id not in wanted:
                continue
            decision = row.get("Citation Decision", "").strip()
            if not decision:
                gold[example_id] = None
            elif decision == "ok":
                gold[example_id] = row.get("proposed_citation", "").strip()
            else:
                gold[example_id] = decision
    if set(gold) != wanted:
        raise ValueError(f"DEV citation decisions missing for IDs: {sorted(wanted - set(gold))}")
    invalid = {key: value for key, value in gold.items() if value is not None and value not in FIVE_CLASSES}
    if invalid:
        raise ValueError(f"DEV citation decisions contain unsupported labels: {invalid}")
    return gold


def score_predictions(
    prediction_rows: list[dict[str, Any]], gold_by_id: dict[str, str | None]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ids = [row["id"] for row in prediction_rows]
    if len(ids) != len(set(ids)) or set(ids) != set(gold_by_id):
        raise ValueError("Prediction and human-decision IDs must match exactly")

    evaluated: list[dict[str, Any]] = []
    applicable: list[dict[str, Any]] = []
    for row in prediction_rows:
        gold = gold_by_id[row["id"]]
        scored_row = {**row, "gold_citation": gold}
        evaluated.append(scored_row)
        if gold is not None:
            applicable.append(scored_row)

    matrix = {gold: {predicted: 0 for predicted in FIVE_CLASSES} for gold in FIVE_CLASSES}
    status_counts = Counter(row["prediction"]["status"] for row in applicable)
    correct = 0
    covered = 0
    for row in applicable:
        gold = row["gold_citation"]
        predicted = row["prediction"]["status"]
        if predicted in FIVE_CLASSES:
            covered += 1
            matrix[gold][predicted] += 1
            if predicted == gold:
                correct += 1

    metrics: dict[str, Any] = {
        "applicable_count": len(applicable),
        "non_applicable_count": len(evaluated) - len(applicable),
        "accuracy": correct / len(applicable) if applicable else None,
        "correct_count": correct,
        "coverage_count": covered,
        "coverage": covered / len(applicable) if applicable else None,
        "selective_accuracy": correct / covered if covered else None,
        "confusion_matrix": matrix,
        "special_status_counts": {status: status_counts.get(status, 0) for status in SPECIAL_STATUSES},
        "per_class": {},
    }
    for label in FIVE_CLASSES:
        tp = matrix[label][label]
        predicted_count = sum(matrix[gold][label] for gold in FIVE_CLASSES)
        gold_count = sum(1 for row in applicable if row["gold_citation"] == label)
        precision = tp / predicted_count if predicted_count else None
        recall = tp / gold_count if gold_count else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None and precision + recall else None)
        metrics["per_class"][label] = {
            "support": gold_count,
            "predicted_count": predicted_count,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return evaluated, metrics


def prepare_dev_result() -> dict[str, Any]:
    dev_ids = read_dev_ids()
    examples = read_jsonl()
    kb_data = json.loads(KB_PATH.read_text())
    kb_ids = frozenset(item["passage_id"] for item in kb_data["passages"])

    # Critical separation: every prediction is generated before the review CSV is read.
    prediction_rows = build_predictions(examples, dev_ids, kb_ids)
    gold_by_id = read_dev_gold(dev_ids)
    evaluated_rows, metrics = score_predictions(prediction_rows, gold_by_id)
    applicable_ids = [example_id for example_id in dev_ids if gold_by_id[example_id] is not None]
    dev_inputs_hash = canonical_hash([row["predictor_input"] for row in prediction_rows])
    dev_gold_hash = canonical_hash({key: gold_by_id[key] for key in sorted(applicable_ids)})
    kb_registry_hash = canonical_hash(sorted(kb_ids))
    predictor_source_hash = sha256_file(EXPERIMENT / "citation_evaluator.py")
    config = {
        "predictor_version": predictor.VERSION,
        "predictor_sha256": predictor_source_hash,
        "split": "dev",
        "input_contract": ["claim_text", "citation_ids", "retrieved_passages", "knowledge_base_ids", "question_text", "citation_expected"],
        "non_applicable_policy": "exclude from five-class metrics",
        "abstention_policy": "unresolved/missing/invalid count as non-covered and non-correct for all-applicable accuracy",
    }
    result = {
        "schema_version": "citation-dev-result-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "configuration": config,
        "configuration_digest": canonical_hash(config)[:16],
        "code_hashes": {
            "citation_evaluator.py": predictor_source_hash,
            "run_citation_dev.py": sha256_file(Path(__file__).resolve()),
        },
        "data_hashes": {
            "dev_predictor_inputs_sha256": dev_inputs_hash,
            "dev_human_citation_labels_sha256": dev_gold_hash,
            "kb_id_registry_sha256": kb_registry_hash,
            "dev_example_ids_sha256": canonical_hash(list(dev_ids)),
        },
        "metrics": metrics,
        "predictions": evaluated_rows,
    }
    if len(prediction_rows) != len(dev_ids):
        raise ValueError("Not every DEV example received a prediction")
    return result


def save_result(result: dict[str, Any], output: Path | None = None) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = RESULTS_DIR / f"citation_dev_{result['configuration_digest']}_{stamp}.json"
    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing result: {output}")
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional new result path; existing files are never overwritten")
    args = parser.parse_args()
    result = prepare_dev_result()
    path = save_result(result, args.output)
    print(json.dumps({"output_path": str(path), "configuration_digest": result["configuration_digest"],
                      "metrics": result["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
