"""Spreadsheet (CSV) review workflow for the labelled claim set.

Export writes every claim with the draft label pre-filled in "My Label" and every not-yet-reviewed
row marked "Unreviewed". Import applies ONLY rows the reviewer explicitly set to "Reviewed":

- an "Unreviewed" row is ignored, even if its label was edited (a warning is reported);
- a label that was already reviewed is never overwritten unless `allow_relabel` is set;
- the original draft label is kept in `draft_label`, and the reviewer and time are recorded;
- any invalid row (bad label or status, unknown or duplicate id, edited claim text) aborts the
  whole import, so a half-applied spreadsheet can't happen.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COLUMNS = [
    "Claim ID",
    "Source Passage",
    "AI Claim",
    "Draft Label",
    "My Label",
    "Notes",
    "Review Status",
]
LABELS = ("supported", "unsupported", "not_a_claim")
UNREVIEWED, REVIEWED = "Unreviewed", "Reviewed"

_LABEL_ALIASES = {
    "supported": "supported",
    "unsupported": "unsupported",
    "not_a_claim": "not_a_claim",
    "not a claim": "not_a_claim",
    "not-a-claim": "not_a_claim",
    "notaclaim": "not_a_claim",
}


class ImportErrors(ValueError):
    """Raised with every problem found; nothing is written when this is raised."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- jsonl storage


def load_items(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def save_items(path: Path, items: list[dict[str, Any]]) -> None:
    """Atomic write: a crash can't leave a truncated label file."""
    data = "".join(json.dumps(i) + "\n" for i in items)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".claims-", suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------- export


def _is_reviewed(item: Mapping[str, Any]) -> bool:
    return item.get("label_status") == "reviewed"


def _draft_label(item: Mapping[str, Any]) -> str:
    return item.get("draft_label", item["label"])


def export_csv(
    items: list[dict[str, Any]],
    passages: Mapping[str, Mapping[str, Any]],
    out: Path,
    *,
    force: bool = False,
) -> None:
    if out.exists() and not force:
        raise FileExistsError(
            f"{out} already exists and may contain your edits; import it first or pass --force"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig adds a BOM so Excel opens the file as UTF-8; Google Sheets ignores it.
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "Claim ID": item["id"],
                    "Source Passage": "\n".join(
                        f"[{pid}] {passages[pid]['text']}" for pid in item["passage_ids"]
                    ),
                    "AI Claim": item["claim"],
                    "Draft Label": _draft_label(item),
                    "My Label": item["label"],
                    "Notes": item.get("reviewer_notes") or item.get("notes", ""),
                    "Review Status": REVIEWED if _is_reviewed(item) else UNREVIEWED,
                }
            )


# ---------------------------------------------------------------- import


def _read_rows(path: Path) -> list[dict[str, str]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportErrors(
            ["the file is not UTF-8; in Excel use File > Save As > 'CSV UTF-8 (Comma delimited)'"]
        ) from exc
    header = text.splitlines()[0] if text else ""
    # Excel in some locales saves with ";" instead of ","
    delimiter = ";" if header.count(";") > header.count(",") else ","
    reader = csv.DictReader(text.splitlines(keepends=True), delimiter=delimiter)
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ImportErrors([f"missing column(s): {missing}; expected {COLUMNS}"])
    return [{k: (v or "") for k, v in row.items() if k} for row in reader]


def _normalise_label(value: str) -> str | None:
    return _LABEL_ALIASES.get(" ".join(value.strip().lower().split()))


def _normalise_status(value: str) -> str | None:
    v = value.strip().lower()
    return {"reviewed": REVIEWED, "unreviewed": UNREVIEWED, "": UNREVIEWED}.get(v)


def import_csv(
    items: list[dict[str, Any]],
    path: Path,
    *,
    reviewer: str,
    allow_relabel: bool = False,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], ImportReport]:
    """Return (updated items, report). The input list is not modified."""
    reviewer = reviewer.strip()
    if not reviewer:
        raise ValueError("a reviewer name is required")
    rows = _read_rows(path)
    by_id = {i["id"]: i for i in items}
    errors: list[str] = []
    seen: set[str] = set()
    parsed: list[tuple[dict[str, Any], str, str, str]] = []

    for line, row in enumerate(rows, start=2):  # line 1 is the header
        cid = row["Claim ID"].strip()
        where = f"row {line} ({cid or 'no id'})"
        if cid in seen:
            errors.append(f"{where}: duplicate Claim ID")
            continue
        seen.add(cid)
        if cid not in by_id:
            errors.append(f"{where}: unknown Claim ID")
            continue
        if row["AI Claim"].strip() != by_id[cid]["claim"].strip():
            errors.append(f"{where}: AI Claim was edited; claims must match the label set")
        status = _normalise_status(row["Review Status"])
        if status is None:
            errors.append(
                f"{where}: Review Status must be '{UNREVIEWED}' or '{REVIEWED}', "
                f"got {row['Review Status']!r}"
            )
            continue
        label = _normalise_label(row["My Label"])
        if label is None and status == REVIEWED:
            errors.append(
                f"{where}: My Label must be one of {list(LABELS)}, got {row['My Label']!r}"
            )
            continue
        parsed.append((by_id[cid], status, label or "", row["Notes"].strip()))

    if errors:
        raise ImportErrors(errors)

    report = ImportReport()
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    updates: dict[str, dict[str, Any]] = {}
    for item, status, label, notes in parsed:
        cid = item["id"]
        if status == UNREVIEWED:
            if label and label != item["label"] and not _is_reviewed(item):
                report.warnings.append(
                    f"{cid}: My Label changed to {label!r} but Review Status is Unreviewed; "
                    "not imported"
                )
            continue
        if _is_reviewed(item):
            if label == item["label"]:
                continue  # already reviewed with this label; keep the original reviewer
            if not allow_relabel:
                report.conflicts.append(cid)
                report.warnings.append(
                    f"{cid}: already reviewed as {item['label']!r} by {item.get('reviewed_by')}; "
                    f"kept (use --allow-relabel to change it to {label!r})"
                )
                continue
        new = dict(item)
        new["draft_label"] = _draft_label(item)
        new["label"] = label
        new["label_status"] = "reviewed"
        new["reviewed_by"] = reviewer
        new["reviewed_at"] = stamp
        if notes and notes != item.get("notes", "").strip():
            new["reviewer_notes"] = notes
        updates[cid] = new
        report.imported.append(cid)
        if label != new["draft_label"]:
            report.changed.append(cid)

    return [updates.get(i["id"], i) for i in items], report
