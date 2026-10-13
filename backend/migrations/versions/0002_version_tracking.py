"""dataset/test-case version tracking, result snapshots, methodology version

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13

Backfill policy (existing data is preserved, never guessed):
- datasets.version and test_cases.version start at 1.
- test_cases.content_hash is computed from each case's current definition.
- evaluation_runs.methodology_version = "1.0.0" (the only methodology before this revision).
- evaluation_results get case_hash/case_version/case_snapshot only when the test case still
  exists and was not modified after the run was created, i.e. when the definition used for
  scoring is provably the current one. Otherwise they stay NULL ("definition unknown"), and
  comparisons exclude those cases instead of trusting them.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of evalops.engine.versioning as of this revision. Migrations must not change
# behaviour when application code evolves; a test asserts both produce identical hashes.
_FIELDS = (
    "input",
    "expected_output",
    "required_terms",
    "forbidden_terms",
    "json_schema",
    "max_latency_ms",
)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _definition(row) -> dict:
    latency = row.max_latency_ms
    return {
        "input": row.input,
        "expected_output": row.expected_output,
        "required_terms": list(_json(row.required_terms) or []),
        "forbidden_terms": list(_json(row.forbidden_terms) or []),
        "json_schema": _json(row.json_schema),
        "max_latency_ms": float(latency) if latency is not None else None,
    }


def _hash(definition: dict) -> str:
    canonical = json.dumps(
        {f: definition[f] for f in _FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ts(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=None)


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
    with op.batch_alter_table("test_cases") as batch:
        batch.add_column(sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("content_hash", sa.String(length=64), nullable=True))
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.add_column(
            sa.Column(
                "methodology_version", sa.String(length=20), server_default="1.0.0", nullable=False
            )
        )
        batch.add_column(sa.Column("dataset_version", sa.Integer(), nullable=True))
    with op.batch_alter_table("evaluation_results") as batch:
        batch.add_column(sa.Column("case_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("case_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("case_snapshot", sa.JSON(), nullable=True))

    conn = op.get_bind()
    cases = {}
    for row in conn.execute(
        sa.text(
            "SELECT id, key, input, expected_output, required_terms, forbidden_terms, "
            "json_schema, max_latency_ms, tags, updated_at FROM test_cases"
        )
    ):
        definition = _definition(row)
        digest = _hash(definition)
        cases[row.id] = (row, definition, digest)
        conn.execute(
            sa.text("UPDATE test_cases SET content_hash = :h WHERE id = :id"),
            {"h": digest, "id": row.id},
        )

    results = conn.execute(
        sa.text(
            "SELECT r.id, r.test_case_id, e.created_at AS run_created "
            "FROM evaluation_results r JOIN evaluation_runs e ON e.id = r.run_id"
        )
    ).all()
    for res in results:
        found = cases.get(res.test_case_id)
        if found is None:
            continue
        row, definition, digest = found
        if _ts(row.updated_at) > _ts(res.run_created):
            continue  # case changed after this run: the scored definition is unknown
        snapshot = {"key": row.key, **definition, "tags": _json(row.tags) or [], "version": 1}
        conn.execute(
            sa.text(
                "UPDATE evaluation_results SET case_hash = :h, case_version = 1, "
                "case_snapshot = :s WHERE id = :id"
            ),
            {"h": digest, "s": json.dumps(snapshot), "id": res.id},
        )

    with op.batch_alter_table("test_cases") as batch:
        batch.alter_column("content_hash", existing_type=sa.String(length=64), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("evaluation_results") as batch:
        batch.drop_column("case_snapshot")
        batch.drop_column("case_hash")
        batch.drop_column("case_version")
    with op.batch_alter_table("evaluation_runs") as batch:
        batch.drop_column("dataset_version")
        batch.drop_column("methodology_version")
    with op.batch_alter_table("test_cases") as batch:
        batch.drop_column("content_hash")
        batch.drop_column("version")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_column("version")
