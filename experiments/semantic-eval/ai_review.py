"""AI-assisted review recommendations, kept separate from human decisions.

Two sources, both by "ChatGPT, AI-assisted":
- ai_review/chatgpt_earlier_2026-10-14.csv: the earlier file (byte-identical copy; SHA-256 in its
  provenance file). Only the AI columns are read; its Decision/Reviewer columns are ignored.
- ai_review/latest_ai_recommendations_2026-10-14.json: latest decisions relayed by the owner; these
  supersede the earlier file for their ids.

A recommendation is used only if its review_key matches the current example, so a stale
recommendation (made against a different version) is reported, never applied. Nothing here ever
becomes a human decision.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EARLIER = HERE / "ai_review/chatgpt_earlier_2026-10-14.csv"
EARLIER_PROVENANCE = HERE / "ai_review/chatgpt_earlier_2026-10-14.provenance.json"
LATEST = HERE / "ai_review/latest_ai_recommendations_2026-10-14.json"
REVIEWER = "ChatGPT, AI-assisted"


def _resolve(value: str, proposed: str) -> str:
    return proposed if value == "ok" else value


def load(rows: dict[str, dict]) -> dict[str, dict]:
    """{id: {source, label, citation, note, status}} with status 'current' or 'stale'."""
    provenance = json.loads(EARLIER_PROVENANCE.read_text())
    if hashlib.sha256(EARLIER.read_bytes()).hexdigest() != provenance["sha256"]:
        raise ValueError(f"{EARLIER.name} no longer matches its recorded SHA-256")
    recs: dict[str, dict] = {}
    with EARLIER.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            row = rows[r["id"]]
            label = (r["AI Suggested Decision"] or "").strip()
            cite = (r["AI Suggested Citation Decision"] or "").strip()
            recs[r["id"]] = {
                "source": "earlier",
                "reviewer": REVIEWER,
                "label": _resolve(label, row["expected_label"]),
                "citation": _resolve(cite, row["citation_expectation"]) if cite else "none",
                "note": r["AI Review Notes"],
                "status": "current" if r["review_key"] == row["review_key"] else "stale",
            }
    for r in json.loads(LATEST.read_text())["recommendations"]:
        row = rows[r["id"]]
        recs[r["id"]] = {
            "source": "latest",
            "reviewer": REVIEWER,
            "label": r["label"],
            "citation": r["citation"] or row["citation_expectation"],
            "note": r["note"],
            "earlier": recs.get(r["id"]),
            "status": "current" if r["review_key"] == row["review_key"] else "stale",
        }
    return recs


def summary(rows: dict[str, dict], recs: dict[str, dict]) -> dict:
    current = {i: r for i, r in recs.items() if r["status"] == "current"}
    return {
        "reviewer": REVIEWER,
        "current": len(current),
        "stale": sorted(i for i, r in recs.items() if r["status"] == "stale"),
        "earlier_stale_superseded": sorted(
            i for i, r in recs.items()
            if r["source"] == "latest" and r["earlier"] and r["earlier"]["status"] == "stale"
        ),
        "agree_label": sorted(i for i, r in current.items() if r["label"] == rows[i]["expected_label"]),
        "differ_label": sorted(i for i, r in current.items() if r["label"] != rows[i]["expected_label"]),
        "differ_citation": sorted(
            i for i, r in current.items() if r["citation"] != rows[i]["citation_expectation"]
        ),
    }
