"""End-to-end RAG release check for the fictional Kestrel policy assistant.

1. Load the knowledge base and questions into EvalOps (idempotent).
2. Submit the recorded local-model answers for the baseline and candidate prompts.
3. Attach model-judge grades: replay saved verdicts (default), run the judge live, or skip.
4. Compare candidate vs baseline, evaluate release gates, and print the readiness verdicts.

    backend/.venv/bin/python backend/scripts/rag_demo.py              # replay, no model needed
    backend/.venv/bin/python backend/scripts/rag_demo.py --judge live # needs local-ai + model
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _api import call  # noqa: E402

from evalops.engine.versioning import METHODOLOGY_VERSION  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RAG = ROOT / "examples/rag"
BASELINE = RAG / "responses/qwen2.5-1.5b_rag-v1.json"
CANDIDATE = RAG / "responses/qwen2.5-1.5b_rag-v2.json"
CANDIDATE_V3 = RAG / "responses/qwen2.5-1.5b_rag-v3.json"
GRADES = {
    BASELINE: RAG / "grades/llama3.2-3b_judge-v2_on_qwen-rag-v1.json",
    CANDIDATE: RAG / "grades/llama3.2-3b_judge-v2_on_qwen-rag-v2.json",
    CANDIDATE_V3: RAG / "grades/llama3.2-3b_judge-v2_on_qwen-rag-v3.json",
}
JUDGE_MODEL = "mlx-community/Llama-3.2-3B-Instruct-4bit"


def ensure_dataset(base: str) -> dict:
    fixture = json.loads((RAG / "kestrel_policy_kb.json").read_text())
    existing = {d["name"]: d for d in call(base, "GET", "/datasets")}
    if fixture["name"] in existing:
        ds = existing[fixture["name"]]
        print(f"dataset '{ds['name']}' exists (id={ds['id']}, version {ds['version']})")
        return ds
    ds = call(
        base, "POST", "/datasets", {"name": fixture["name"], "description": fixture["description"]}
    )
    call(base, "POST", f"/datasets/{ds['id']}/passages", {"passages": fixture["passages"]})
    call(
        base, "POST", f"/datasets/{ds['id']}/test-cases/bulk", {"test_cases": fixture["test_cases"]}
    )
    ds = call(base, "GET", f"/datasets/{ds['id']}")
    print(
        f"created dataset '{ds['name']}' (id={ds['id']}) with {ds['passage_count']} passages "
        f"and {ds['test_case_count']} questions"
    )
    return ds


def ensure_run(base: str, ds_id: int, path: Path) -> dict:
    record = json.loads(path.read_text())
    runs = call(base, "GET", f"/evaluations/runs?dataset_id={ds_id}&limit=200")["items"]
    for run in runs:
        same = (run["model_name"], run["prompt_version"]) == (
            record["model_name"],
            record["prompt_version"],
        )
        if (
            same
            and run["methodology_version"] == METHODOLOGY_VERSION
            and (run["generation"] or {}).get("prompt_hash") == record["generation"]["prompt_hash"]
        ):
            print(
                f"run for {path.name} exists (id={run['id']}, "
                f"methodology {run['methodology_version']})"
            )
            return run
    body = {k: record[k] for k in ("model_name", "prompt_version", "label", "config", "generation")}
    body |= {"dataset_id": ds_id, "responses": record["responses"]}
    run = call(base, "POST", "/evaluations/runs", body)
    s = run["summary"]
    print(
        f"submitted {path.name}: run {run['id']}, passed {s['passed_cases']}/{s['total_cases']}, "
        f"groundedness {s['groundedness']}, abstention accuracy {s['abstention_accuracy']}"
    )
    return run


def attach_grades(base: str, run: dict, path: Path, mode: str) -> None:
    script = Path(__file__).resolve().parent / "rag_judge.py"
    grades = GRADES[path]
    if mode == "live":
        cmd = [
            "grade-run",
            "--run-id",
            str(run["id"]),
            "--model",
            JUDGE_MODEL,
            "--save",
            str(grades),
        ]
    elif mode == "replay":
        if not grades.exists():
            print(f"no saved grades at {grades.relative_to(ROOT)}; run with --judge live first")
            return
        cmd = ["attach", "--run-id", str(run["id"]), "--grades", str(grades)]
    else:
        return
    # Separate process: the judge model is loaded, used and freed without sharing memory.
    subprocess.run(
        [sys.executable, str(script), *cmd, "--base-url", base],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    graded = call(base, "GET", f"/evaluations/runs/{run['id']}")["model_graded"]
    print(
        f"attached {mode} judge grades to run {run['id']}: estimated groundedness "
        f"{graded['estimated_groundedness']} (judge agreement with heuristic "
        f"{graded['heuristic_judge_agreement']})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--judge", choices=["replay", "live", "none"], default="replay")
    args = parser.parse_args()
    base = args.base_url

    print(f"Replaying historical recordings under methodology {METHODOLOGY_VERSION}")
    ds = ensure_dataset(base)
    baseline = ensure_run(base, ds["id"], BASELINE)
    candidate = ensure_run(base, ds["id"], CANDIDATE)
    candidate_v3 = ensure_run(base, ds["id"], CANDIDATE_V3)
    pairs = ((baseline, BASELINE), (candidate, CANDIDATE), (candidate_v3, CANDIDATE_V3))
    for run, path in pairs:
        attach_grades(base, run, path, args.judge)

    for cand in (candidate, candidate_v3):
        cmp = call(
            base,
            "GET",
            f"/evaluations/compare?baseline_run_id={baseline['id']}&candidate_run_id={cand['id']}",
        )
        print(
            f"\nComparison rag-v1 -> {cand['prompt_version']}: compatible={cmp['compatible']} "
            f"has_regression={cmp['has_regression']}"
        )
        for reason in cmp["regression_reasons"]:
            print(f"  - {reason}")
        for d in cmp["estimated_metric_deltas"]:
            print(
                f"  (estimated) {d['metric']}: {d['baseline']} -> {d['candidate']} [{d['status']}]"
            )

    reports = (
        ("baseline", baseline, None),
        ("candidate", candidate, baseline["id"]),
        ("candidate", candidate_v3, baseline["id"]),
    )
    for label, run, b in reports:
        query = f"?baseline_run_id={b}" if b else ""
        rep = call(base, "GET", f"/evaluations/runs/{run['id']}/report{query}")
        g = rep["gate_evaluation"]
        print(
            f"\n{label} run {run['id']} ({run['prompt_version']}): {rep['readiness'].upper()} "
            f"- {g['passed_gates']} passed, {g['failed_gates']} failed, "
            f"{g['skipped_gates']} skipped"
        )
        for gate in g["gates"]:
            if gate["status"] != "skipped":
                print(f"  {gate['status'].upper():4} {gate['gate']}: {gate['message']}")
    for cand in (candidate, candidate_v3):
        print(
            f"\nUI ({cand['prompt_version']}): http://127.0.0.1:5173/compare"
            f"?baseline={baseline['id']}&candidate={cand['id']}"
        )


if __name__ == "__main__":
    main()
