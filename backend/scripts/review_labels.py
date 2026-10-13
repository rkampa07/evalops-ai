"""Review the drafted claim labels interactively. Run in your own terminal:

    backend/.venv/bin/python backend/scripts/review_labels.py --reviewer "your name"

For each draft item: Enter = accept the draft label, s/u/n = set supported / unsupported /
not_a_claim, k = skip for now, q = save and quit. Progress is saved after every item.
Reviewed items get label_status "reviewed" and your reviewer name.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHOICES = {"s": "supported", "u": "unsupported", "n": "not_a_claim"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Review drafted claim labels")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--labels", type=Path, default=ROOT / "examples/rag/labels/claims.jsonl")
    parser.add_argument("--kb", type=Path, default=ROOT / "examples/rag/kestrel_policy_kb.json")
    args = parser.parse_args()

    kb = {p["passage_id"]: p for p in json.loads(args.kb.read_text())["passages"]}
    items = [json.loads(line) for line in args.labels.read_text().splitlines() if line.strip()]

    def save() -> None:
        args.labels.write_text("".join(json.dumps(i) + "\n" for i in items))

    pending = [i for i in items if i["label_status"] != "reviewed"]
    print(f"{len(pending)} of {len(items)} items need review.\n")
    for n, item in enumerate(pending, start=1):
        print("=" * 78)
        print(f"[{n}/{len(pending)}] {item['id']}  ({item['source']})")
        for pid in item["passage_ids"]:
            print(f"  [{pid}] {kb[pid]['text']}")
        print(f"\n  CLAIM: {item['claim']}")
        print(f"  DRAFT: {item['label']}" + (f"   note: {item['notes']}" if item["notes"] else ""))
        answer = input("  Enter=accept  s/u/n=set  k=skip  q=quit > ").strip().lower()
        if answer == "q":
            break
        if answer == "k":
            continue
        if answer in CHOICES:
            item["label"] = CHOICES[answer]
        item["label_status"] = "reviewed"
        item["reviewed_by"] = args.reviewer
        save()
    save()
    done = sum(i["label_status"] == "reviewed" for i in items)
    print(f"\nSaved. {done}/{len(items)} items reviewed.")


if __name__ == "__main__":
    main()
