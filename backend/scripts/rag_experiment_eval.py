"""Replay recorded RAG answers with methodology 2.0.0; compare saved experiment metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evalops.engine.scoring import (  # noqa: E402
    CaseSpec,
    EvalConfig,
    ResponseSpec,
    score_case,
    summarize,
)
from evalops.engine.versioning import METHODOLOGY_VERSION  # noqa: E402


def index(rows: list[dict], key: str) -> dict[str, dict]:
    indexed = {}
    for row in rows:
        value = row[key]
        if value in indexed:
            raise ValueError(f"duplicate {key}: {value}")
        indexed[value] = row
    return indexed


def evaluate(recording: dict, dataset: dict) -> dict:
    """Use recorded config, retrieval, output and latency; missing responses count as failures."""
    if METHODOLOGY_VERSION != "2.0.0":
        raise ValueError(f"requires scorer 2.0.0, installed methodology is {METHODOLOGY_VERSION}")
    cases = index(dataset["test_cases"], "key")
    passages = index(dataset["passages"], "passage_id")
    if not cases or not passages:
        raise ValueError("dataset must contain test cases and knowledge-base passages")
    responses = index(recording["responses"], "case_key")
    unknown = responses.keys() - cases.keys()
    if unknown:
        raise ValueError(f"unknown response case keys: {sorted(unknown)}")
    config = EvalConfig(**recording["config"])
    knowledge_base = {key: row["text"] for key, row in passages.items()}
    results = []
    for key, row in cases.items():
        unknown_gold = set(row.get("relevant_passage_ids", [])) - passages.keys()
        if unknown_gold:
            raise ValueError(f"{key}: unknown gold passage ids: {sorted(unknown_gold)}")
        case = CaseSpec(**{k: v for k, v in row.items() if k in CaseSpec.__dataclass_fields__})
        response = responses.get(key)
        spec = (
            ResponseSpec(
                response["output"],
                response.get("latency_ms"),
                response.get("retrieved_passage_ids"),
            )
            if response is not None
            else None
        )
        results.append(score_case(case, spec, config, knowledge_base))
    summary = summarize(results)
    answered = [r for r in results if r.metrics.get("responded") and not r.metrics.get("abstained")]
    claims = [claim for r in results for claim in r.metrics.get("claims", [])]
    metrics = summary | {
        "passed": f"{summary['passed_cases']}/{summary['total_cases']}",
        "answers_with_citation": (
            f"{sum(bool(r.metrics.get('citations')) for r in answered)}/{len(answered)}"
        ),
        "claims_with_citation": f"{sum(bool(c['citations']) for c in claims)}/{len(claims)}",
        "failures": {r.key: [f["check"] for f in r.failures] for r in results if r.failures},
        "outputs": {key: row["output"] for key, row in responses.items()},
    }
    # Saved experiment JSON displays these percentiles to one decimal; scoring is unmodified.
    for key in ("latency_p50_ms", "latency_p95_ms"):
        metrics[key] = round(metrics[key], 1) if metrics[key] is not None else None
    return {"config": asdict(config), "metrics": metrics}


def differences(actual: dict, expected: dict) -> dict:
    """Compare every saved field, including nested failures/outputs; never skip unknown fields."""
    return {
        key: {"expected": value, "actual": actual.get(key), "actual_present": key in actual}
        for key, value in expected.items()
        if key not in actual or actual[key] != value
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay existing RAG recordings using scorer 2.0.0 without models, inference, "
            "API calls, or file writes. Emit JSON metrics and saved-result differences to stdout."
        ),
        epilog=(
            "Coverage excludes abstentions from answered cases; claim coverage uses extracted "
            "claims. Reported p50/p95 latencies use one decimal, matching saved experiments. "
            "Only the selected metrics section is compared; other runs, cross-run comparisons, "
            "and release gates are not evaluated. "
            "Exit codes: 0 match, 1 differences, 2 invalid input."
        ),
    )
    parser.add_argument("--recording", required=True, type=Path, help="Recorded responses JSON")
    parser.add_argument(
        "--dataset", required=True, type=Path, help="Dataset JSON including passages"
    )
    parser.add_argument("--experiment", required=True, type=Path, help="Saved experiment JSON")
    parser.add_argument(
        "--section",
        choices=("development_set", "prospective_check"),
        default="development_set",
        help="Saved section; development_set selects the recording's prompt_version (default)",
    )
    args = parser.parse_args(argv)
    try:
        dataset_bytes = args.dataset.read_bytes()
        dataset = json.loads(dataset_bytes)
        recording = json.loads(args.recording.read_bytes())
        experiment = json.loads(args.experiment.read_bytes())
        result = evaluate(recording, dataset)
        if experiment["methodology_version"] != METHODOLOGY_VERSION:
            raise ValueError("experiment methodology differs from scorer; comparison refused")
        expected = experiment[args.section]
        selector = args.section
        if args.section == "development_set":
            selector += f".{recording['prompt_version']}"
            expected = expected[recording["prompt_version"]]
        if not isinstance(expected, dict) or not expected:
            raise ValueError("selected experiment section must be a nonempty metrics object")
        result["metrics"]["frozen_questions_sha256"] = hashlib.sha256(dataset_bytes).hexdigest()
        diff = differences(result["metrics"], expected)
        result.update(
            methodology_version=METHODOLOGY_VERSION,
            recording=str(args.recording),
            dataset=str(args.dataset),
            experiment=str(args.experiment),
            compared_section=selector,
            compared_fields=sorted(expected),
            differences=diff,
            matches=not diff,
            scope="Selected metrics only; other runs, comparisons, and release gates not evaluated",
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())
