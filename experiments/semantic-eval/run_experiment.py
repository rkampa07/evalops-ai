"""Run one evaluation method over the benchmark with the same examples and frozen evidence.

    backend/.venv/bin/python experiments/semantic-eval/run_experiment.py --method lexical --split dev --dry-run
    ... --method nli --model <id> --revision <40-hex commit> --split dev   (needs torch + local model)

Methods
  lexical      methodology 2.0.0 lexical support (supported / not supported only)
  diagnostics  chat-diagnostics-v1 warnings (warning coverage only; "no warning" is never "supported")
  nli          evalops.semantic verifier with a local NLI model (four outputs)

Fail-closed: before anything else (dry runs included), check_benchmark.validate() checks the
manifest, every source file, the split, recomputed review keys, evidence snapshots and decision
provenance. Any problem stops the run. Examples are parsed from the exact bytes that are hashed
into the result.

Gold labels come from YOUR decisions in human_review_decisions.csv (--labels reviewed, the
default; every row of the split must be decided). --labels provisional uses the proposed labels;
it is allowed on dev only, and results are stamped PROVISIONAL. AI-assisted recommendations are
never used as gold.

Performance: model loading is timed separately from inference. Per-claim and per-5-claim-answer
latencies are reported. The whole pass is repeated (--repeats, default 2), and results are written
only if every repeat is identical.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import platform
import resource
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(HERE)]

import check_benchmark  # noqa: E402
import score  # noqa: E402
from evalops.chat.diagnostics import diagnose  # noqa: E402
from evalops.engine.rag import lexical_support  # noqa: E402

CODE_FILES = [HERE / f for f in ("score.py", "run_experiment.py", "check_benchmark.py", "build_benchmark.py", "ai_review.py")]
OUTPUT_MAP = {"needs_human_review": score.R}
ANSWER_SIZE = 5
LATENCY_NOTE = (
    "per_claim: one verify() call. sequential_5_claim_group: the sum of 5 consecutive single-claim "
    "verify() calls on DIFFERENT benchmark examples, run one after another in one process with no "
    "batching. It approximates checking a 5-claim answer; it isn't a measured real answer. Only "
    "complete groups count. Determinism is checked across repeats within one process only."
)
MEMORY_NOTE = (
    "Process peak resident set size (getrusage ru_maxrss, a high-water mark): it includes Python, "
    "libraries and any earlier allocations, so model memory is approximated by the rise after "
    "loading. It does not capture GPU/Metal unified memory, and the high-water mark never goes down."
)


def dependency_files() -> list[Path]:
    """Every evalops module loaded in this process (semantic, chat/diagnostics, engine/rag,
    engine/metrics, ...) plus the experiment scripts: everything that can change a verdict."""
    loaded = {
        Path(m.__file__).resolve()
        for name, m in list(sys.modules.items())
        if (name == "evalops" or name.startswith("evalops.")) and getattr(m, "__file__", None)
    }
    return sorted(loaded) + CODE_FILES


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024) if platform.system() == "Darwin" else peak / 1024, 1)


def load_examples(split: str, labels: str, ids: list[str] | None = None):
    """Validate first (fail closed), then select examples and gold labels."""
    if labels == "provisional" and split != "dev":
        sys.exit("provisional labels are allowed on the dev split only")
    v = check_benchmark.validate(require_reviewed=split if labels == "reviewed" else None)
    if v.problems:
        sys.exit("integrity check failed; nothing was run:\n" + "\n".join(f"  - {p}" for p in v.problems))
    selected = [r for r in v.rows.values() if r["split"] == split]
    if ids:
        unknown = sorted(set(ids) - {r["id"] for r in selected})
        if unknown:
            sys.exit(f"--ids not in the {split} split: {unknown}")
        selected = [r for r in selected if r["id"] in set(ids)]
    if labels == "provisional":
        gold = {r["id"]: r["expected_label"] for r in selected}
    else:
        gold = {r["id"]: v.reviewed[r["id"]]["label"] for r in selected}
        excluded = sorted(i for i, g in gold.items() if g == "needs_review")
        selected = [r for r in selected if r["id"] not in excluded]
        if excluded:
            print(f"excluded (you marked needs_review): {excluded}")
    examples = [{"id": r["id"], "gold": gold[r["id"]], "category": r["category"]} for r in selected]
    return examples, {r["id"]: r for r in selected}, v


# Sanity check before any benchmark scoring: three obvious pairs from the policy text (not
# benchmark examples). If the model's top label is wrong on two or more, stop: the label mapping,
# tokenizer or model is not behaving as an NLI classifier.
SMOKE_PAIRS = [
    ("Custom-made furniture, mattresses that have been unsealed, gift cards, and clearance items "
     "marked final sale cannot be returned.", "Custom-made furniture cannot be returned.", "entailment"),
    ("Custom-made furniture, mattresses that have been unsealed, gift cards, and clearance items "
     "marked final sale cannot be returned.", "Custom-made furniture can be returned.", "contradiction"),
    ("White-glove delivery, which includes in-room placement and packaging removal, is available "
     "for furniture for a $129 fee.", "Gift wrapping is available for every order.", "neutral"),
]  # fmt: skip


def smoke(backend) -> dict:
    load_seconds = backend.load()
    start = time.perf_counter()
    scores = backend.score([(p, h) for p, h, _ in SMOKE_PAIRS])
    seconds = time.perf_counter() - start
    rows = [
        {"hypothesis": h, "expected": want, "got": max(sc, key=sc.get), "scores": sc}
        for (_, h, want), sc in zip(SMOKE_PAIRS, scores, strict=True)
    ]
    wrong = sum(r["got"] != r["expected"] for r in rows)
    return {"pairs": rows, "wrong": wrong, "passed": wrong <= 1, "load_seconds": load_seconds,
            "score_seconds": round(seconds, 4), "peak_rss_mb": peak_rss_mb()}  # fmt: skip


def predict_lexical(row: dict) -> bool:
    return lexical_support(row["claim"], row["evidence"]).supported


def predict_diagnostics(row: dict) -> list[str]:
    cited = "".join(f" [{c}]" for c in row["cited_passage_ids"])
    claim = row["claim"].rstrip(".") + cited + "."
    return sorted({w["state"] for w in diagnose(claim, row["evidence"], row["evidence"])["warnings"]})


def predict_semantic(verifier, row: dict) -> tuple[str, dict]:
    verdict = verifier.verify(row["question"], row["claim"], row["evidence"])
    return OUTPUT_MAP.get(verdict.label, verdict.label), verdict.to_dict()


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 4)


def one_pass(method: str, examples: list[dict], rows: dict[str, dict], verifier=None) -> dict:
    predictions, details, per_claim = {}, {}, []
    for e in examples:
        row = rows[e["id"]]
        t = time.perf_counter()
        if method == "lexical":
            predictions[e["id"]] = predict_lexical(row)
        elif method == "diagnostics":
            predictions[e["id"]] = predict_diagnostics(row)
        else:
            predictions[e["id"]], details[e["id"]] = predict_semantic(verifier, row)
        per_claim.append(time.perf_counter() - t)
    # Sum of 5 consecutive single-claim calls (different examples): see LATENCY_NOTE.
    answers = [sum(per_claim[i : i + ANSWER_SIZE]) for i in range(0, len(per_claim) - ANSWER_SIZE + 1, ANSWER_SIZE)]
    return {
        "predictions": predictions,
        "details": details,
        "latency_seconds": {
            "per_claim_p50": _percentile(per_claim, 0.5),
            "per_claim_p95": _percentile(per_claim, 0.95),
            "sequential_5_claim_group_p50": _percentile(answers, 0.5),
            "sequential_5_claim_group_p95": _percentile(answers, 0.95),
            "sequential_5_claim_groups_measured": len(answers),
        },
    }


def run(
    method: str,
    examples: list[dict],
    rows: dict[str, dict],
    verifier=None,
    repeats: int = 2,
    label_status: str = "PROVISIONAL",
) -> dict:
    """Load (timed separately), then `repeats` identical passes; metrics from the first pass.
    Every metrics block carries `label_status` so a quoted number keeps its provenance."""
    rss_start = peak_rss_mb()
    load_seconds = verifier.backend.load() if verifier is not None else 0.0
    rss_loaded = peak_rss_mb()
    passes = [one_pass(method, examples, rows, verifier) for _ in range(max(1, repeats))]
    first = passes[0]
    deterministic = all(
        p["predictions"] == first["predictions"] and p["details"] == first["details"] for p in passes[1:]
    )
    if method == "lexical":
        metrics = score.score_binary_lexical(examples, first["predictions"])
    elif method == "diagnostics":
        metrics = score.score_warning_coverage(examples, {k: set(v) for k, v in first["predictions"].items()})
    else:
        metrics = score.score_semantic(examples, first["predictions"])
    metrics = {"label_status": label_status, **metrics}
    for block in metrics.get("by_category", {}).values():
        block["label_status"] = label_status
    return {
        "metrics": metrics,
        "predictions": first["predictions"],
        "details": first["details"],
        "determinism": {"repeats": len(passes), "identical": deterministic},
        "performance": {
            "model_load_seconds": load_seconds,
            "inference_latency_seconds": [p["latency_seconds"] for p in passes],
            "latency_note": LATENCY_NOTE,
            "peak_rss_mb": {"start": rss_start, "after_load": rss_loaded, "after_inference": peak_rss_mb()},
            "memory_note": MEMORY_NOTE,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=["lexical", "diagnostics", "nli"], required=True)
    ap.add_argument("--split", choices=["dev", "test"], required=True)
    ap.add_argument("--labels", choices=["reviewed", "provisional"], default="reviewed")
    ap.add_argument("--model")
    ap.add_argument("--revision", help="full 40-character commit SHA")
    ap.add_argument("--no-question", action="store_true", help="ablation: no question-context pass")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--ids", help="comma-separated example ids (a small subset of the split)")
    ap.add_argument("--smoke", action="store_true", help="NLI sanity check on 3 policy pairs only")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ids = [i.strip() for i in args.ids.split(",")] if args.ids else None
    examples, rows, validation = load_examples(args.split, args.labels, ids)
    verifier = config = None
    if args.method == "nli":
        from evalops.semantic.aggregate import AggregationConfig
        from evalops.semantic.nli import TransformersNLI
        from evalops.semantic.verifier import SemanticVerifier

        if not (args.model and args.revision):
            sys.exit("--method nli needs --model and --revision (a full 40-character commit SHA)")
        try:
            backend = TransformersNLI(args.model, args.revision)
        except ValueError as exc:
            sys.exit(str(exc))
        config = AggregationConfig(include_question=not args.no_question)
        verifier = SemanticVerifier(backend, config)

    plan = {
        "method": args.method,
        "split": args.split,
        "labels": args.labels.upper(),
        "examples": len(examples),
        "gold": dict(sorted(collections.Counter(e["gold"] for e in examples).items())),
        "config": config.__dict__ | {"digest": config.digest()} if config else None,
        "integrity": "passed",
    }
    if ids:
        plan["ids"] = sorted(ids)
    print(json.dumps(plan, indent=1))
    if args.smoke:
        if verifier is None:
            sys.exit("--smoke needs --method nli")
        if args.dry_run:
            print("dry run: smoke check not run")
            return 0
        result = smoke(verifier.backend)
        print(json.dumps(result, indent=1))
        if not result["passed"]:
            print("smoke check FAILED: stop; don't score the benchmark", file=sys.stderr)
            return 1
        print("smoke check passed (a sanity check only, not an accuracy result)")
        return 0
    if args.dry_run:
        print("dry run: integrity passed; nothing scored, no model loaded")
        return 0

    label_status = (
        "PROVISIONAL: proposed labels, not human-reviewed; not validated accuracy"
        if args.labels == "provisional"
        else "human-reviewed labels (decisions in human_review_decisions.csv)"
    )
    started = time.perf_counter()
    result = run(args.method, examples, rows, verifier, args.repeats, label_status)
    if not result["determinism"]["identical"]:
        print("repeat runs differ: results NOT written (fail closed)", file=sys.stderr)
        return 1
    record = {
        **plan,
        "status": label_status,
        "model": verifier.backend.describe() if verifier else None,
        "total_seconds": round(time.perf_counter() - started, 3),
        "hashes": {  # computed now from the files actually read and run
            **validation.hashes,
            "manifest.json": sha256(HERE / "manifest.json"),
            **{str(p.relative_to(ROOT)): sha256(p) for p in dependency_files()},
        },
        **result,
    }
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    tag = config.digest() if config else "na"
    path = out_dir / f"{args.method}_{args.split}_{args.labels}_{tag}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(record, indent=1) + "\n")
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
