"""Review the claim labels in Excel or Google Sheets.

export  Write examples/rag/labels/claims_review.csv. "My Label" is pre-filled with the draft
        label and every unreviewed row has Review Status "Unreviewed".
import  Read the edited CSV and apply ONLY rows whose Review Status is "Reviewed".
        Previously reviewed labels are kept unless --allow-relabel is given.

  backend/.venv/bin/python backend/scripts/labels_csv.py export
  backend/.venv/bin/python backend/scripts/labels_csv.py import --reviewer "Rithik" --dry-run
  backend/.venv/bin/python backend/scripts/labels_csv.py import --reviewer "Rithik"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evalops import labels as lab  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "examples/rag/labels/claims.jsonl"
CSV = ROOT / "examples/rag/labels/claims_review.csv"
KB = ROOT / "examples/rag/kestrel_policy_kb.json"


def export(args) -> None:
    items = lab.load_items(args.labels)
    passages = {p["passage_id"]: p for p in json.loads(args.kb.read_text())["passages"]}
    hints = lab.load_hints(args.hints) if args.hints else None
    try:
        lab.export_csv(items, passages, args.csv, force=args.force, hints=hints)
    except FileExistsError as exc:
        sys.exit(str(exc))
    status = Counter(i["label_status"] for i in items)
    print(f"Wrote {len(items)} rows to {args.csv} ({dict(status)})")
    if hints:
        flagged = sum(h.flagged for h in hints.values())
        print(f"{flagged} flagged row(s) listed first; AI hint columns are read-only context.")
    print(
        "Open it in Excel or Google Sheets. For each row you have checked, set 'My Label' to "
        "supported / unsupported / not_a_claim and 'Review Status' to Reviewed. Save as CSV "
        "(UTF-8), then run the import command."
    )


def do_import(args) -> None:
    items = lab.load_items(args.labels)
    try:
        updated, report = lab.import_csv(
            items, args.csv, reviewer=args.reviewer, allow_relabel=args.allow_relabel
        )
    except lab.ImportErrors as exc:
        print(f"Import aborted; nothing was changed. {len(exc.errors)} problem(s):")
        for err in exc.errors:
            print(f"  - {err}")
        sys.exit(1)
    for warning in report.warnings:
        print(f"  note: {warning}")
    print(
        f"{len(report.imported)} row(s) to import as reviewed by {args.reviewer!r} "
        f"({len(report.changed)} with a label different from the draft); "
        f"{len(report.conflicts)} conflict(s) kept."
    )
    if report.changed:
        print(f"  changed from draft: {report.changed}")
    if args.dry_run:
        print("Dry run: label file not modified.")
        return
    if report.imported:
        lab.save_items(args.labels, updated)
    status = Counter(i["label_status"] for i in updated)
    print(f"Saved. Label status now: {dict(status)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", type=Path, default=LABELS)
    parser.add_argument("--csv", type=Path, default=CSV)
    sub = parser.add_subparsers(dest="command", required=True)

    exp = sub.add_parser("export")
    exp.add_argument("--kb", type=Path, default=KB)
    exp.add_argument("--force", action="store_true", help="overwrite an existing CSV")
    exp.add_argument(
        "--hints",
        type=Path,
        help="CSV with AI pre-screen columns (AI Suggested Label, AI Review Notes, AI Review "
        "Flag): flagged rows first, hints shown read-only, never imported",
    )
    exp.set_defaults(func=export)

    imp = sub.add_parser("import")
    imp.add_argument("--reviewer", required=True)
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument(
        "--allow-relabel", action="store_true", help="let the CSV change already-reviewed labels"
    )
    imp.set_defaults(func=do_import)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
