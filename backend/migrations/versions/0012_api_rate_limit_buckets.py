"""persist authenticated API rate limit windows

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_rate_limit_buckets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("principal_key", sa.String(100), nullable=False),
        sa.Column("category", sa.String(10), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "principal_key", "category", "window_start", name="uq_api_rate_bucket_window"
        ),
        sa.CheckConstraint(
            "category IN ('read', 'write', 'jobs')", name="ck_api_rate_bucket_category"
        ),
        sa.CheckConstraint("request_count >= 0", name="ck_api_rate_bucket_count"),
    )
    op.create_index("ix_api_rate_bucket_window", "api_rate_limit_buckets", ["window_start"])


def downgrade() -> None:
    op.drop_index("ix_api_rate_bucket_window", table_name="api_rate_limit_buckets")
    op.drop_table("api_rate_limit_buckets")
