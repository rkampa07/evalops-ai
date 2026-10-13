import csv
import json

import pytest

from evalops import labels as lab

KB = {
    "KB-A-01": {"passage_id": "KB-A-01", "title": "A", "text": "Returns within 30 days."},
    "KB-B-02": {"passage_id": "KB-B-02", "title": "B", "text": "Ships to Canada only."},
}


def item(i, label="supported", status="draft", **extra):
    return {
        "id": f"c{i:03d}",
        "source": "constructed",
        "passage_ids": ["KB-A-01", "KB-B-02"] if i == 1 else ["KB-A-01"],
        "claim": f"claim number {i}",
        "label": label,
        "notes": f"note {i}",
        "label_status": status,
        "reviewed_by": None,
    } | extra


@pytest.fixture()
def items():
    return [item(1), item(2, "unsupported"), item(3, "not_a_claim")]


def write(path, items):
    path.write_text("".join(json.dumps(i) + "\n" for i in items))


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, delimiter=","):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=lab.COLUMNS, delimiter=delimiter)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- export


def test_export_columns_prefill_and_unreviewed(tmp_path, items):
    out = tmp_path / "review.csv"
    lab.export_csv(items, KB, out)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM so Excel reads UTF-8
    rows = read_csv(out)
    assert list(rows[0]) == [
        "Claim ID",
        "Source Passage",
        "AI Claim",
        "Draft Label",
        "My Label",
        "Notes",
        "Review Status",
    ]
    assert len(rows) == 3
    assert all(r["My Label"] == r["Draft Label"] for r in rows)
    assert all(r["Review Status"] == "Unreviewed" for r in rows)
    assert (
        rows[0]["Source Passage"]
        == "[KB-A-01] Returns within 30 days.\n[KB-B-02] Ships to Canada only."
    )
    assert rows[1]["Draft Label"] == "unsupported" and rows[1]["Notes"] == "note 2"


def test_export_keeps_previous_reviews_visible(tmp_path, items):
    items[0] |= {
        "label": "unsupported",
        "draft_label": "supported",
        "label_status": "reviewed",
        "reviewed_by": "R",
    }
    out = tmp_path / "review.csv"
    lab.export_csv(items, KB, out)
    row = read_csv(out)[0]
    assert (row["Draft Label"], row["My Label"], row["Review Status"]) == (
        "supported",
        "unsupported",
        "Reviewed",
    )


def test_export_refuses_to_overwrite_without_force(tmp_path, items):
    out = tmp_path / "review.csv"
    lab.export_csv(items, KB, out)
    with pytest.raises(FileExistsError):
        lab.export_csv(items, KB, out)
    lab.export_csv(items, KB, out, force=True)


# ---------------------------------------------------------------- import


def exported(tmp_path, items):
    out = tmp_path / "review.csv"
    lab.export_csv(items, KB, out)
    return out, read_csv(out)


def test_untouched_export_imports_nothing(tmp_path, items):
    out, _ = exported(tmp_path, items)
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert report.imported == [] and updated == items
    assert all(i["label_status"] == "draft" for i in updated)


def test_unreviewed_rows_ignored_even_if_label_changed(tmp_path, items):
    out, rows = exported(tmp_path, items)
    rows[0]["My Label"] = "unsupported"  # changed, but not confirmed
    write_csv(out, rows)
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert updated[0]["label"] == "supported" and updated[0]["label_status"] == "draft"
    assert report.imported == []
    assert any("c001" in w and "Unreviewed" in w for w in report.warnings)


def test_reviewed_rows_are_imported_with_provenance(tmp_path, items):
    out, rows = exported(tmp_path, items)
    rows[0] |= {"My Label": "Unsupported ", "Review Status": "reviewed", "Notes": "my reason"}
    rows[1] |= {"Review Status": "Reviewed"}  # explicit confirmation of the draft label
    write_csv(out, rows)
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    first, second, third = updated
    assert first["label"] == "unsupported" and first["draft_label"] == "supported"
    assert first["label_status"] == "reviewed" and first["reviewed_by"] == "Rithik"
    assert first["reviewed_at"] and first["reviewer_notes"] == "my reason"
    assert first["notes"] == "note 1"  # draft note kept
    assert second["label"] == "unsupported" and second["label_status"] == "reviewed"
    assert "reviewer_notes" not in second  # notes unchanged -> nothing added
    assert third["label_status"] == "draft"
    assert report.imported == ["c001", "c002"] and report.changed == ["c001"]


def test_previously_reviewed_labels_are_preserved(tmp_path, items):
    items[0] |= {"label_status": "reviewed", "reviewed_by": "R", "draft_label": "supported"}
    out, rows = exported(tmp_path, items)
    # 1) row left as Reviewed and unchanged -> no change, no duplicate import
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert updated[0]["reviewed_by"] == "R" and report.imported == []
    # 2) row set back to Unreviewed -> earlier review is kept
    rows[0]["Review Status"] = "Unreviewed"
    write_csv(out, rows)
    updated, _ = lab.import_csv(items, out, reviewer="Rithik")
    assert updated[0]["label_status"] == "reviewed" and updated[0]["reviewed_by"] == "R"
    # 3) a different label on an already-reviewed item needs explicit --allow-relabel
    rows[0] |= {"Review Status": "Reviewed", "My Label": "unsupported"}
    write_csv(out, rows)
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert updated[0]["label"] == "supported" and report.conflicts == ["c001"]
    updated, report = lab.import_csv(items, out, reviewer="Rithik", allow_relabel=True)
    assert updated[0]["label"] == "unsupported" and updated[0]["reviewed_by"] == "Rithik"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda rows: rows[0].update({"My Label": "maybe", "Review Status": "Reviewed"}),
            "My Label",
        ),
        (lambda rows: rows[0].update({"Review Status": "done"}), "Review Status"),
        (lambda rows: rows[0].update({"Claim ID": "zzz"}), "unknown Claim ID"),
        (lambda rows: rows.append(dict(rows[0])), "duplicate Claim ID"),
        (lambda rows: rows[0].update({"AI Claim": "edited text"}), "AI Claim was edited"),
    ],
)
def test_invalid_rows_abort_the_whole_import(tmp_path, items, mutate, message):
    out, rows = exported(tmp_path, items)
    rows[1]["Review Status"] = "Reviewed"  # a valid row that must NOT be applied either
    mutate(rows)
    write_csv(out, rows)
    with pytest.raises(lab.ImportErrors, match=message):
        lab.import_csv(items, out, reviewer="Rithik")


def test_not_a_claim_spellings_and_semicolon_csv(tmp_path, items):
    out, rows = exported(tmp_path, items)
    rows[0] |= {"My Label": "Not a claim", "Review Status": "REVIEWED"}
    write_csv(out, rows, delimiter=";")  # Excel with a European locale
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert updated[0]["label"] == "not_a_claim" and report.imported == ["c001"]


def test_missing_columns_rejected(tmp_path, items):
    out = tmp_path / "bad.csv"
    out.write_text("Claim ID,My Label\nc001,supported\n")
    with pytest.raises(lab.ImportErrors, match="missing column"):
        lab.import_csv(items, out, reviewer="Rithik")


def test_non_utf8_file_gives_actionable_error(tmp_path, items):
    out = tmp_path / "latin1.csv"
    out.write_bytes(
        ",".join(lab.COLUMNS).encode() + b"\nc001,caf\xe9,x,supported,supported,,Reviewed\n"
    )
    with pytest.raises(lab.ImportErrors, match="CSV UTF-8"):
        lab.import_csv(items, out, reviewer="Rithik")


def test_reviewer_required(tmp_path, items):
    out, _ = exported(tmp_path, items)
    with pytest.raises(ValueError):
        lab.import_csv(items, out, reviewer="  ")


def test_save_and_load_roundtrip(tmp_path, items):
    path = tmp_path / "claims.jsonl"
    write(path, items)
    assert lab.load_items(path) == items
    lab.save_items(path, items)
    assert lab.load_items(path) == items


# ---------------------------------------------------------------- priority export with hints


def hints_file(tmp_path):
    path = tmp_path / "ai.csv"
    cols = ["Claim ID", "AI Suggested Label", "AI Review Notes", "AI Review Flag"]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(
            {
                "Claim ID": "c003",
                "AI Suggested Label": "supported",
                "AI Review Notes": "check this one",
                "AI Review Flag": "Needs careful human judgment",
            }
        )
        w.writerow(
            {
                "Claim ID": "c001",
                "AI Suggested Label": "unsupported",
                "AI Review Notes": "generic",
                "AI Review Flag": "",
            }
        )
    return path


def test_priority_export_orders_flagged_first_with_hint_columns(tmp_path, items):
    hints = lab.load_hints(hints_file(tmp_path))
    out = tmp_path / "priority.csv"
    lab.export_csv(items, KB, out, hints=hints)
    rows = read_csv(out)
    assert list(rows[0])[:7] == lab.COLUMNS
    assert list(rows[0])[7:] == lab.HINT_COLUMNS
    assert [r["Claim ID"] for r in rows] == ["c003", "c001", "c002"]
    assert (
        rows[0]["Priority"] == "1 - flagged"
        and rows[0]["AI Hint (not imported)"] == "check this one"
    )
    assert rows[1]["Priority"] == "2" and rows[2]["AI Suggested Label (not imported)"] == ""
    # hints never pre-fill the reviewer's columns
    assert rows[1]["My Label"] == "supported" and rows[1]["Review Status"] == "Unreviewed"


def test_hint_columns_are_never_imported(tmp_path, items):
    out = tmp_path / "priority.csv"
    lab.export_csv(items, KB, out, hints=lab.load_hints(hints_file(tmp_path)))
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert report.imported == [] and updated == items
    rows = read_csv(out)
    rows[1]["Review Status"] = "Reviewed"  # c001: reviewer confirms the draft, not the AI hint
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    updated, report = lab.import_csv(items, out, reviewer="Rithik")
    assert updated[0]["label"] == "supported"  # AI suggested 'unsupported'; ignored
    assert report.imported == ["c001"]


def test_hints_for_unknown_ids_are_reported(tmp_path, items):
    hints = lab.load_hints(hints_file(tmp_path))
    hints["zzz"] = lab.Hint("x", "y", True)
    with pytest.raises(ValueError, match="zzz"):
        lab.export_csv(items, KB, tmp_path / "p.csv", hints=hints)
