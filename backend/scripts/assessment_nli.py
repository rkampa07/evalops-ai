"""Explicit pinned NLI cache setup and real CPU smoke test. Never opens a database."""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from evalops.claim_assessments import MODEL_ID, MODEL_REVISION, config_digest
from evalops.nli_runtime import check_local_nli, download_pinned_nli, make_cpu_verifier

SMOKE_PAIRS = (
    ("entailment", "A woman is reading a book.", "A person is reading."),
    ("contradiction", "A woman is reading a book.", "No one is reading."),
    ("neutral", "A woman is reading a book.", "The woman has two children."),
)


def smoke() -> dict:
    verifier = make_cpu_verifier()
    start = time.perf_counter()
    verifier.backend.load()
    load_seconds = time.perf_counter() - start
    rows = []
    for expected, evidence, claim in SMOKE_PAIRS:
        start = time.perf_counter()
        probabilities = verifier.backend.score([(evidence, claim)])[0]
        elapsed = time.perf_counter() - start
        rows.append(
            {
                "expected": expected,
                "predicted": max(probabilities, key=probabilities.get),
                "evidence": evidence,
                "claim": claim,
                "probabilities": probabilities,
                "latency_seconds": round(elapsed, 6),
            }
        )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "config_digest": config_digest(),
        "device": "cpu",
        "model": verifier.backend.describe(),
        "model_load_seconds": round(load_seconds, 6),
        "peak_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "smoke": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "download", "smoke"), default="check", nargs="?")
    parser.add_argument(
        "--output", type=Path, help="New JSON report path; existing files are refused."
    )
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new path")
    try:
        if args.mode == "smoke":
            report = smoke()
        else:
            snapshot = download_pinned_nli() if args.mode == "download" else check_local_nli()
            report = {
                "model_id": MODEL_ID,
                "revision": snapshot["commit"],
                "files": snapshot["files"],
            }
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        print(f"NLI {args.mode} failed: {exc}. No simulated fallback.", file=sys.stderr)
        return 2
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(encoded)
    print(encoded, end="")
    return int(any(row["predicted"] != row["expected"] for row in report.get("smoke", [])))


if __name__ == "__main__":
    raise SystemExit(main())
