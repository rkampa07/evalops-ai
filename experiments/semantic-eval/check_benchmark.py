"""Validate the frozen benchmark and the human review before anything is scored.

    backend/.venv/bin/python experiments/semantic-eval/check_benchmark.py [--require-reviewed SPLIT]

Fails if: a frozen file or source changed since manifest.json was written; a review was made
against a different version of an example, question or labelling-rules version (stale review_key); a decision is not one of the
allowed values; or (with --require-reviewed) any row of that split is still Pending.
Read-only: never edits the decision sheet or review.csv.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LABELS = {"supported", "contradicted", "insufficient_evidence", "needs_review"}
CITATIONS = {"correct", "wrong_passage", "unrelated", "not_retrieved", "partial"}
SHEET = HERE / "human_review_decisions.csv"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_reviews(rows: dict[str, dict], path: Path = SHEET) -> tuple[dict[str, dict], list[str]]:
    """Decided rows by id ({label, citation, reviewer, notes}), plus problems.

    A row is decided when Decision is filled. 'ok' means the label (or citation) proposed in the
    benchmark row (read from benchmark.jsonl, not from the sheet). Rows left blank are pending.
    """
    problems, reviewed = [], {}
    if not path.exists():
        return {}, [f"{path.name} missing"]
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            decision = (r.get("Decision") or "").strip()
            if not decision:
                continue
            rid = r["id"]
            row = rows.get(rid)
            if row is None:
                problems.append(f"{rid}: decided row no longer exists in the benchmark")
                continue
            if r["review_key"] != row["review_key"]:
                problems.append(f"{rid}: STALE review (made against {r['review_key']}, example is now "
                                f"{row['review_key']}); re-review it")  # fmt: skip
                continue
            label = row["expected_label"] if decision == "ok" else decision
            if label not in LABELS:
                problems.append(f"{rid}: Decision must be ok or one of {sorted(LABELS)}, got {decision!r}")
                continue
            cite = (r.get("Citation Decision") or "").strip()
            if row["citation_expectation"] == "none":
                if cite:
                    problems.append(f"{rid}: no citation to judge; leave Citation Decision empty")
                    continue
                citation = "none"
            else:
                citation = row["citation_expectation"] if cite == "ok" else cite
                if citation not in CITATIONS:
                    problems.append(f"{rid}: Citation Decision must be ok or one of {sorted(CITATIONS)}")
                    continue
            reviewer = (r.get("Reviewer") or "").strip()
            if not reviewer:
                problems.append(f"{rid}: Reviewer is empty")
                continue
            reviewed[rid] = {"label": label, "citation": citation, "reviewer": reviewer,
                             "notes": r.get("Notes", ""), "agrees": label == row["expected_label"]}  # fmt: skip
    return reviewed, problems


def superseded_input(path: Path = HERE / "review.csv") -> bool:
    """review.csv was the earlier sheet; anything typed there would otherwise be silently ignored."""
    if not path.exists():
        return False
    with path.open(newline="") as f:
        return any(
            any((r.get(c) or "").strip() for c in ("Your Label", "Your Citation Verdict", "Reviewer", "Notes"))
            or (r.get("Review Status") or "Pending").strip() != "Pending"
            for r in csv.DictReader(f)
        )


AI_REVIEWER_RE = re.compile(r"chatgpt|gpt|claude|gemini|copilot|\bai\b|ai-assisted|llm", re.I)


# Every manifest entry that must exist. A missing entry fails validation: dropping a line from the
# manifest must not silently drop its check.
REQUIRED_MANIFEST_ENTRIES = (
    "benchmark.jsonl",
    "split.json",
    "test_rows",
    "build_benchmark.py",
    "examples/rag/kestrel_policy_kb.json",
    *(f"examples/rag/responses/qwen2.5-1.5b_rag-v{i}.json" for i in range(1, 6)),
    "experiments/semantic-eval/sources/live_chat_2026-10-14.json",
    "ai_review/chatgpt_earlier_2026-10-14.csv",
    "ai_review/chatgpt_earlier_2026-10-14.provenance.json",
    "ai_review/latest_ai_recommendations_2026-10-14.json",
)


class Validation:
    """Everything an experiment needs, read once from disk, with hashes of the exact bytes read."""

    def __init__(self, rows, benchmark_bytes, split, reviewed, problems, hashes):
        self.rows, self.benchmark_bytes, self.split = rows, benchmark_bytes, split
        self.reviewed, self.problems, self.hashes = reviewed, problems, hashes


def validate(require_reviewed: str | None = None) -> Validation:
    """Fail-closed integrity check of the benchmark, its sources, the split and the decisions."""
    problems: list[str] = []
    manifest = json.loads((HERE / "manifest.json").read_text())
    actual: dict[str, str] = {}
    missing_entries = [k for k in REQUIRED_MANIFEST_ENTRIES if k not in manifest.get("hashes", {})]
    if missing_entries:
        problems.append(f"manifest.json lacks required entries: {missing_entries}")
    for name, expected in manifest.get("hashes", {}).items():
        if name == "test_rows":
            continue
        path = HERE / name if (HERE / name).exists() else ROOT / name
        if not path.exists():
            problems.append(f"{name}: missing")
            continue
        actual[name] = sha256(path)
        if actual[name] != expected:
            problems.append(f"{name}: changed since manifest.json was written")

    benchmark_bytes = (HERE / "benchmark.jsonl").read_bytes()
    split_bytes = (HERE / "split.json").read_bytes()
    sheet_bytes = SHEET.read_bytes() if SHEET.exists() else b""
    hashes = {  # hashes of the bytes actually used below, not copied from the manifest
        "benchmark.jsonl": hashlib.sha256(benchmark_bytes).hexdigest(),
        "split.json": hashlib.sha256(split_bytes).hexdigest(),
        SHEET.name: hashlib.sha256(sheet_bytes).hexdigest(),
    }
    rows = {}
    for line in benchmark_bytes.decode().splitlines():
        r = json.loads(line)
        if r["id"] in rows:
            problems.append(f"{r['id']}: duplicate id")
        rows[r["id"]] = r
    split = json.loads(split_bytes)
    if set(split) != set(rows) or any(rows[i]["split"] != s for i, s in split.items()):
        problems.append("split.json and benchmark.jsonl disagree")

    # Row-level integrity: recompute review keys; evidence must equal the frozen knowledge base.
    sys.path.insert(0, str(HERE))
    import build_benchmark

    kb = {p["passage_id"]: p["text"] for p in json.loads(
        (ROOT / "examples/rag/kestrel_policy_kb.json").read_text())["passages"]}  # fmt: skip
    for rid, r in rows.items():
        if build_benchmark.review_key(r) != r["review_key"]:
            problems.append(f"{rid}: stored review_key doesn't match the example's content")
        if r.get("rules_version") != build_benchmark.RULES_VERSION:
            problems.append(f"{rid}: rules_version {r.get('rules_version')} != {build_benchmark.RULES_VERSION}")
        if list(r["evidence"]) != r["retrieved_passage_ids"] or any(
            kb.get(pid) != text for pid, text in r["evidence"].items()
        ):
            problems.append(f"{rid}: evidence snapshot differs from the knowledge base or retrieval")
        if r["label_status"] != "PROVISIONAL" or r["reviewed_by"] is not None:
            problems.append(f"{rid}: benchmark row claims a review; decisions live only in {SHEET.name}")

    reviewed, review_problems = load_reviews(rows)
    problems += review_problems
    for rid, v in reviewed.items():
        if AI_REVIEWER_RE.search(v["reviewer"]):
            problems.append(f"{rid}: Reviewer {v['reviewer']!r} looks like an AI; AI-assisted "
                            "recommendations are not human decisions")  # fmt: skip
    if require_reviewed:
        wanted = [i for i, r in rows.items() if require_reviewed in ("all", r["split"])]
        pending = [i for i in wanted if i not in reviewed]
        if pending:
            problems.append(f"{len(pending)} {require_reviewed} rows not reviewed: {pending}")
    if superseded_input():
        problems.append("review.csv (superseded) contains review input: copy it into "
                        f"{SHEET.name}; review.csv is not read")  # fmt: skip
    if SHEET.exists() and sheet_bytes != SHEET.read_bytes():
        problems.append(f"{SHEET.name} changed while being validated")
    return Validation(rows, benchmark_bytes, split, reviewed, problems, hashes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-reviewed", choices=["dev", "test", "all"])
    args = parser.parse_args()
    v = validate(args.require_reviewed)
    rows, reviewed, problems = v.rows, v.reviewed, v.problems
    by_split = {s: sum(1 for i in reviewed if rows[i]["split"] == s) for s in ("dev", "test")}
    corrected = sorted(i for i, x in reviewed.items() if not x["agrees"])
    try:
        import ai_review

        ai = ai_review.summary(rows, ai_review.load(rows))
        print(f"AI-assisted recommendations ({ai['reviewer']}, separate from decisions): current "
              f"{ai['current']}, differ from proposed label: {ai['differ_label'] or 'none'}, "
              f"stale: {ai['stale'] or 'none'}")  # fmt: skip
    except (OSError, ValueError, KeyError) as exc:
        problems.append(f"AI review files: {exc}")
    print(f"examples {len(rows)} · decided {len(reviewed)} (dev {by_split['dev']}, test "
          f"{by_split['test']}) · pending {len(rows) - len(reviewed)} · your corrections: {corrected or 'none'}")
    for p in problems:
        print("FAIL", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
