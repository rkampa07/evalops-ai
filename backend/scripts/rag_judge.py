"""Model-based claim judging (estimated) and judge calibration against labels.

  calibrate  Measure the lexical heuristic and (optionally) a local model judge against the
             labelled claim set. Writes a JSON report; nothing is assumed about accuracy.
  grade-run  Judge every extracted claim of a stored run with a local model, save the verdicts
             to a file, and attach them to the run as estimated grades.
  attach     Attach previously saved verdicts (replay without loading a model).

Examples:
  rag_judge.py calibrate --model mlx-community/Llama-3.2-3B-Instruct-4bit \\
      --out examples/rag/calibration/llama3.2-3b_judge-v1.json
  rag_judge.py calibrate --heuristic-only --out /tmp/heuristic.json
  rag_judge.py grade-run --run-id 5 --model mlx-community/Llama-3.2-3B-Instruct-4bit \\
      --save examples/rag/grades/llama3.2-3b_on_qwen-rag-v1.json
  rag_judge.py attach --run-id 5 --grades examples/rag/grades/llama3.2-3b_on_qwen-rag-v1.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _api import call  # noqa: E402
from _evidence import assert_writable  # noqa: E402

from evalops.engine.agreement import binary_agreement  # noqa: E402
from evalops.engine.rag import lexical_support  # noqa: E402
from evalops.llm.backends import MLXBackend  # noqa: E402
from evalops.llm.judge import JUDGE_PROMPT_VERSION, JUDGE_PROMPTS, judge_claim, judge_info  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KB = ROOT / "examples/rag/kestrel_policy_kb.json"
DEFAULT_LABELS = ROOT / "examples/rag/labels/claims.jsonl"


def load_kb(path: Path) -> dict[str, dict]:
    return {p["passage_id"]: p for p in json.loads(path.read_text())["passages"]}


def calibrate(args) -> None:
    kb = load_kb(args.kb)
    items = [json.loads(line) for line in args.labels.read_text().splitlines() if line.strip()]
    if args.source:
        items = [i for i in items if i["source"].split(":")[0] == args.source]
    if args.reviewed_only:
        items = [i for i in items if i["label_status"] == "reviewed"]
        if not items:
            sys.exit(
                "no reviewed labels yet; review them first (labels_csv.py or review_labels.py)"
            )
    backend = None if args.heuristic_only else MLXBackend(args.model)
    if backend:
        backend.load()
    rows, latencies = [], []
    for i, item in enumerate(items, start=1):
        passages = [kb[pid] for pid in item["passage_ids"]]
        support = lexical_support(item["claim"], {p["passage_id"]: p["text"] for p in passages})
        row = {
            "id": item["id"],
            "source": item["source"].split(":")[0],
            "label": item["label"],
            "label_status": item["label_status"],
            "heuristic": "supported" if support.supported else "unsupported",
        }
        if backend:
            verdict, raw, latency = judge_claim(backend, item["claim"], passages, args.judge_prompt)
            row |= {"judge": verdict, "judge_raw": raw}
            latencies.append(latency)
            print(
                f"[{i}/{len(items)}] {item['id']} label={item['label']} judge={verdict}", flush=True
            )
        rows.append(row)

    def evaluate(verifier: str, subset: list[dict]) -> dict:
        usable = [r for r in subset if r["label"] != "not_a_claim" and r.get(verifier) != "error"]
        return binary_agreement(
            [r["label"] for r in usable], [r[verifier] for r in usable], positive="unsupported"
        )

    verifiers = ["heuristic"] + (["judge"] if backend else [])
    status = Counter(r["label_status"] for r in rows)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "labels_file": str(args.labels.relative_to(ROOT)),
        "label_status_counts": dict(status),
        "labels_are_reviewed": set(status) == {"reviewed"},
        "label_counts": dict(Counter(r["label"] for r in rows)),
        "positive_class": "unsupported",
        "results": {
            v: {
                "all": evaluate(v, rows),
                "constructed": evaluate(v, [r for r in rows if r["source"] == "constructed"]),
                "generated": evaluate(v, [r for r in rows if r["source"] == "generated"]),
            }
            for v in verifiers
        },
        "not_a_claim_items": {
            v: dict(Counter(r[v] for r in rows if r["label"] == "not_a_claim")) for v in verifiers
        },
        "items": rows,
    }
    report["subset"] = args.source or "all"
    report["reviewed_only"] = args.reviewed_only
    if backend:
        report["judge"] = judge_info(backend, args.judge_prompt)
        report["judge_errors"] = sum(r["judge"] == "error" for r in rows)
        report["benchmark"] = {
            "model_load_seconds": round(backend.load_seconds or 0, 2),
            "judgments": len(latencies),
            "latency_ms_median": round(statistics.median(latencies), 1),
            "latency_ms_max": round(max(latencies), 1),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    for v in verifiers:
        m = report["results"][v]["all"]
        print(
            f"{v:9} n={m['n']} precision={m['precision']} recall={m['recall']} "
            f"f1={m['f1']} accuracy={m['accuracy']} kappa={m['cohen_kappa']}"
        )
    print("labels reviewed:", report["labels_are_reviewed"], dict(status))


def grade_run(args) -> None:
    run = call(args.base_url, "GET", f"/evaluations/runs/{args.run_id}")
    passages = {
        p["passage_id"]: p
        for p in call(args.base_url, "GET", f"/datasets/{run['dataset_id']}/passages")
    }
    backend = MLXBackend(args.model)
    backend.load()
    grades, started = [], time.perf_counter()
    for result in run["results"]:
        claims = result["metrics"].get("claims", [])
        if not claims:
            continue
        evidence = [passages[pid] for pid in (result["retrieved_passage_ids"] or [])]
        graded, case_latency = [], 0.0
        for claim in claims:
            verdict, raw, latency = judge_claim(backend, claim["text"], evidence, args.judge_prompt)
            graded.append({"claim": claim["text"], "verdict": verdict, "raw": raw})
            case_latency += latency
        grades.append(
            {"case_key": result["case_key"], "claims": graded, "latency_ms": round(case_latency, 1)}
        )
        print(f"{result['case_key']}: {[g['verdict'] for g in graded]}", flush=True)
    body = {"judge": judge_info(backend, args.judge_prompt), "grades": grades}
    record = body | {
        "graded_run": {"model_name": run["model_name"], "prompt_version": run["prompt_version"]},
        "benchmark": {
            "model_load_seconds": round(backend.load_seconds or 0, 2),
            "total_seconds": round(time.perf_counter() - started, 2),
            "claims": sum(len(g["claims"]) for g in grades),
        },
    }
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(record, indent=2) + "\n")
    graded_run = call(args.base_url, "PUT", f"/evaluations/runs/{args.run_id}/model-grades", body)
    print(json.dumps(graded_run["model_graded"], indent=2))


def attach(args) -> None:
    record = json.loads(args.grades.read_text())
    body = {"judge": record["judge"], "grades": record["grades"]}
    run = call(args.base_url, "PUT", f"/evaluations/runs/{args.run_id}/model-grades", body)
    print(json.dumps(run["model_graded"], indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    cal = sub.add_parser("calibrate")
    cal.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    cal.add_argument("--kb", type=Path, default=DEFAULT_KB)
    cal.add_argument("--model", default="mlx-community/Llama-3.2-3B-Instruct-4bit")
    cal.add_argument("--heuristic-only", action="store_true")
    cal.add_argument("--judge-prompt", choices=sorted(JUDGE_PROMPTS), default=JUDGE_PROMPT_VERSION)
    cal.add_argument("--source", choices=["constructed", "generated"], help="label subset")
    cal.add_argument("--reviewed-only", action="store_true", help="use only reviewed labels")
    cal.add_argument("--out", type=Path, required=True)
    cal.set_defaults(func=calibrate)

    grade = sub.add_parser("grade-run")
    grade.add_argument("--run-id", type=int, required=True)
    grade.add_argument("--model", default="mlx-community/Llama-3.2-3B-Instruct-4bit")
    grade.add_argument("--base-url", default="http://127.0.0.1:8000")
    grade.add_argument("--save", type=Path)
    grade.add_argument(
        "--judge-prompt", choices=sorted(JUDGE_PROMPTS), default=JUDGE_PROMPT_VERSION
    )
    grade.set_defaults(func=grade_run)

    att = sub.add_parser("attach")
    att.add_argument("--run-id", type=int, required=True)
    att.add_argument("--grades", type=Path, required=True)
    att.add_argument("--base-url", default="http://127.0.0.1:8000")
    att.set_defaults(func=attach)

    args = parser.parse_args()
    for output in (getattr(args, "out", None), getattr(args, "save", None)):
        if output is not None:
            try:  # fail before loading a model or calling the API
                assert_writable(output)
            except ValueError as exc:
                parser.error(str(exc))
    args.func(args)


if __name__ == "__main__":
    main()
