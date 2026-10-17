"""add workspace invitations and session workspace selection

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(sa.Column("active_workspace_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_auth_sessions_active_workspace_id_workspaces",
            "workspaces",
            ["active_workspace_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_auth_sessions_active_workspace_id", ["active_workspace_id"])

    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "workspace_id",
            sa.Integer(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invited_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('admin', 'editor', 'viewer')", name="ck_workspace_invitation_role"
        ),
    )
    op.create_index(
        "uq_workspace_invitation_pending_target",
        "workspace_invitations",
        ["workspace_id", "target_user_id"],
        unique=True,
        sqlite_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )
    op.create_index(
        "ix_workspace_invitations_workspace_created",
        "workspace_invitations",
        ["workspace_id", "created_at"],
    )

    if op.get_bind().dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER trg_membership_prevent_final_owner_delete
            BEFORE DELETE ON workspace_memberships
            FOR EACH ROW WHEN OLD.role = 'owner'
              AND (SELECT COUNT(*) FROM workspace_memberships
                   WHERE workspace_id = OLD.workspace_id AND role = 'owner') <= 1
            BEGIN SELECT RAISE(ABORT, 'workspace must retain an owner'); END
        """)
        op.execute("""
            CREATE TRIGGER trg_membership_prevent_final_owner_demotion
            BEFORE UPDATE OF role ON workspace_memberships
            FOR EACH ROW WHEN OLD.role = 'owner' AND NEW.role != 'owner'
              AND (SELECT COUNT(*) FROM workspace_memberships
                   WHERE workspace_id = OLD.workspace_id AND role = 'owner') <= 1
            BEGIN SELECT RAISE(ABORT, 'workspace must retain an owner'); END
        """)
    elif op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION evalops_retain_workspace_owner()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM id FROM workspaces WHERE id = OLD.workspace_id FOR UPDATE;
                IF OLD.role = 'owner' AND (TG_OP = 'DELETE' OR NEW.role <> 'owner')
                   AND (SELECT COUNT(*) FROM workspace_memberships
                        WHERE workspace_id = OLD.workspace_id AND role = 'owner') <= 1 THEN
                    RAISE EXCEPTION 'workspace must retain an owner';
                END IF;
                IF TG_OP = 'DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
            END; $$
        """)
        op.execute("""
            CREATE TRIGGER trg_membership_retain_workspace_owner_delete
            BEFORE DELETE ON workspace_memberships
            FOR EACH ROW EXECUTE FUNCTION evalops_retain_workspace_owner()
        """)
        op.execute("""
            CREATE TRIGGER trg_membership_retain_workspace_owner_update
            BEFORE UPDATE OF role ON workspace_memberships
            FOR EACH ROW EXECUTE FUNCTION evalops_retain_workspace_owner()
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_membership_prevent_final_owner_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_membership_prevent_final_owner_demotion")
    elif op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_membership_retain_workspace_owner_delete "
            "ON workspace_memberships"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_membership_retain_workspace_owner_update "
            "ON workspace_memberships"
        )
        op.execute("DROP FUNCTION IF EXISTS evalops_retain_workspace_owner()")
    op.drop_index("uq_workspace_invitation_pending_target", table_name="workspace_invitations")
    op.drop_index("ix_workspace_invitations_workspace_created", table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_index("ix_auth_sessions_active_workspace_id")
        batch.drop_constraint("fk_auth_sessions_active_workspace_id_workspaces", type_="foreignkey")
        batch.drop_column("active_workspace_id")
