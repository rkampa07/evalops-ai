"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    """Identity mapping; credentials are owned by an external identity provider."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(back_populates="user")
    submitted_jobs: Mapped[list[EvaluationJob]] = relationship(
        back_populates="submitted_by_user", foreign_keys="EvaluationJob.submitted_by_user_id"
    )
    created_chat_sessions: Mapped[list[ChatSession]] = relationship(
        back_populates="created_by_user", foreign_keys="ChatSession.created_by_user_id"
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("slug", name="uq_workspaces_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_legacy: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_false(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    datasets: Mapped[list[Dataset]] = relationship(back_populates="workspace")


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'editor', 'viewer')",
            name="ck_workspace_membership_role",
        ),
        Index("ix_workspace_memberships_workspace_role", "workspace_id", "role"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")


class WorkspaceInvitation(Base):
    """One-time, user-bound invitation. Only a token digest is stored."""

    __tablename__ = "workspace_invitations"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'editor', 'viewer')", name="ck_workspace_invitation_role"
        ),
        Index(
            "uq_workspace_invitation_pending_target",
            "workspace_id",
            "target_user_id",
            unique=True,
            sqlite_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_workspace_invitations_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    invited_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("id", "workspace_id", name="uq_datasets_id_workspace"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Compatibility default for the pre-auth local API; auth integration must set this explicitly.
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        default=1,
        server_default="1",
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Incremented whenever the set of test cases or any case definition changes.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    test_cases: Mapped[list[TestCase]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", order_by="TestCase.id"
    )
    runs: Mapped[list[EvaluationRun]] = relationship(
        back_populates="dataset", foreign_keys="EvaluationRun.dataset_id"
    )
    workspace: Mapped[Workspace] = relationship(back_populates="datasets")
    chat_sessions: Mapped[list[ChatSession]] = relationship(
        back_populates="dataset", foreign_keys="ChatSession.dataset_id"
    )
    passages: Mapped[list[Passage]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", order_by="Passage.passage_id"
    )


class Passage(Base):
    """Knowledge-base passage used for RAG evaluation. `passage_id` is the citation id."""

    __tablename__ = "passages"
    __table_args__ = (UniqueConstraint("dataset_id", "passage_id", name="uq_passage_dataset_pid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    passage_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    dataset: Mapped[Dataset] = relationship(back_populates="passages")


class TestCase(Base):
    __tablename__ = "test_cases"
    __test__ = False  # not a pytest test class
    __table_args__ = (UniqueConstraint("dataset_id", "key", name="uq_test_case_dataset_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_terms: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    forbidden_terms: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    json_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    max_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    relevant_passage_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default="[]", nullable=False
    )
    expect_abstain: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_false(), nullable=False
    )
    # Incremented when the scoring-relevant definition changes (see engine/versioning.py).
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    dataset: Mapped[Dataset] = relationship(back_populates="test_cases")


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="uq_evaluation_runs_id_workspace"),
        ForeignKeyConstraint(
            ["dataset_id", "workspace_id"],
            ["datasets.id", "datasets.workspace_id"],
            name="fk_evaluation_runs_dataset_workspace",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        default=1,
        server_default="1",
        index=True,
    )
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="completed", nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Scoring rules used (engine/versioning.py). Runs created before versioning are "1.0.0".
    methodology_version: Mapped[str] = mapped_column(
        String(20), server_default="1.0.0", nullable=False
    )
    # Dataset.version at evaluation time; NULL for runs created before version tracking.
    dataset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # SHA-256 of the knowledge-base passages (id + text) at evaluation time; NULL without a KB.
    knowledge_base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Generation metadata supplied by the client (model id/revision, prompt hash, settings).
    generation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Model-based (estimated) aggregates, kept apart from the deterministic `summary`.
    model_graded: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    dataset: Mapped[Dataset] = relationship(back_populates="runs", foreign_keys=[dataset_id])
    workspace: Mapped[Workspace] = relationship()
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="EvaluationResult.case_key"
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_key", name="uq_result_run_case"),
        # Required as a composite target for workspace-safe assessment ownership.
        Index("uq_result_id_run", "id", "run_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Snapshot of the case at evaluation time; test cases may be edited or deleted later.
    test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    failures: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    # Definition the output was scored against. NULL only for legacy results whose case was
    # edited or deleted before version tracking existed (the definition can't be recovered).
    case_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retrieved_passage_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    generation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model_grades: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    run: Mapped[EvaluationRun] = relationship(back_populates="results")


class EvaluationJobQueue(Base):
    """Singleton row used to enforce the durable queue limit atomically."""

    __tablename__ = "evaluation_job_queue_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_job_queue_singleton_id"),
        CheckConstraint("queued_count >= 0 AND queued_count <= 100", name="ck_job_queue_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CitationAssessmentQueue(Base):
    """Singleton reservation counter for citation assessment submissions."""

    __tablename__ = "citation_assessment_queue_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_citation_assessment_queue_singleton"),
        CheckConstraint(
            "queued_count >= 0 AND queued_count <= 50", name="ck_citation_assessment_queue_count"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AssessmentIdSequence(Base):
    """Shared numeric ID allocator so citation and claim jobs share one status URL space."""

    __tablename__ = "assessment_id_sequence"
    __table_args__ = (CheckConstraint("id = 1", name="ck_assessment_id_sequence_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    next_id: Mapped[int] = mapped_column(Integer, nullable=False)


class AssessmentIdempotency(Base):
    """Workspace-wide idempotency ledger shared by citation and claim assessment kinds."""

    __tablename__ = "assessment_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "submitter_key", "idempotency_key", name="uq_assessment_idempotency"
        ),
        CheckConstraint("evaluator_kind IN ('citation', 'claim')", name="ck_assessment_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    submitter_key: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    evaluator_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_id: Mapped[int] = mapped_column(Integer, nullable=False)


class CitationAssessmentJob(Base):
    """Durable citation-only assessment request and lease-fenced worker state."""

    __tablename__ = "citation_assessment_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "submitter_key",
            "idempotency_key",
            name="uq_citation_assessment_idempotency",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_citation_assessment_id_workspace"),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_citation_assessment_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_citation_assessment_result_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint("evaluator_kind = 'citation'", name="ck_citation_assessment_kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_citation_assessment_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3", name="ck_citation_assessment_attempts"
        ),
        Index("ix_citation_assessment_queue", "status", "created_at"),
        Index("ix_citation_assessment_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluator_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="citation")
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    submitter_key: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    result_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    citation_expected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predictor_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class CitationAssessmentResult(Base):
    """Append-only frozen predictor output; job updates never rewrite this artifact."""

    __tablename__ = "citation_assessment_results"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_citation_assessment_result_job"),
        ForeignKeyConstraint(
            ["job_id", "workspace_id"],
            ["citation_assessment_jobs.id", "citation_assessment_jobs.workspace_id"],
            name="fk_citation_assessment_result_job_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_citation_result_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_citation_result_result_run",
            ondelete="RESTRICT",
        ),
        Index("ix_citation_assessment_result_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    result_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(50), nullable=False)
    predictor_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    per_citation: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    prediction_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ClaimAssessmentJob(Base):
    """Durable, workspace-scoped claim assessment request and lease state."""

    __tablename__ = "claim_assessment_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "submitter_key",
            "idempotency_key",
            name="uq_claim_assessment_idempotency",
        ),
        UniqueConstraint("id", "workspace_id", name="uq_claim_assessment_id_workspace"),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_claim_assessment_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_claim_assessment_result_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint("evaluator_kind = 'claim'", name="ck_claim_assessment_kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_claim_assessment_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 3", name="ck_claim_assessment_attempts"
        ),
        Index("ix_claim_assessment_queue", "status", "created_at"),
        Index("ix_claim_assessment_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluator_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="claim")
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitter_key: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    result_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False
    )
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ClaimAssessmentResult(Base):
    """Append-only semantic claim output, isolated from citation and lexical results."""

    __tablename__ = "claim_assessment_results"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_claim_assessment_result_job"),
        ForeignKeyConstraint(
            ["job_id", "workspace_id"],
            ["claim_assessment_jobs.id", "claim_assessment_jobs.workspace_id"],
            name="fk_claim_result_job_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_id", "workspace_id"],
            ["evaluation_runs.id", "evaluation_runs.workspace_id"],
            name="fk_claim_result_run_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["result_id", "run_id"],
            ["evaluation_results.id", "evaluation_results.run_id"],
            name="fk_claim_result_result_run",
            ondelete="RESTRICT",
        ),
        Index("ix_claim_assessment_result_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    result_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_key: Mapped[str] = mapped_column(String(100), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(50), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_status: Mapped[str] = mapped_column(String(50), nullable=False)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationJob(Base):
    """Durable submission and execution state, separate from completed runs."""

    __tablename__ = "evaluation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "submitter_key",
            "idempotency_key",
            name="uq_evaluation_job_workspace_submitter_key",
        ),
        UniqueConstraint("run_id", name="uq_evaluation_jobs_run_id"),
        ForeignKeyConstraint(
            ["dataset_id", "workspace_id"],
            ["datasets.id", "datasets.workspace_id"],
            name="fk_evaluation_jobs_dataset_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_evaluation_job_status",
        ),
        CheckConstraint("completed_count >= 0", name="ck_evaluation_job_completed_count"),
        CheckConstraint("total_count >= 0", name="ck_evaluation_job_total_count"),
        CheckConstraint("attempt_count >= 0", name="ck_evaluation_job_attempt_count"),
        Index("ix_evaluation_jobs_status_created_id", "status", "created_at", "id"),
        Index("ix_evaluation_jobs_dataset_id", "dataset_id"),
        Index("ix_evaluation_jobs_retry_of", "retry_of_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    submitter_key: Mapped[str] = mapped_column(
        String(100), nullable=False, default="dev:local", server_default="dev:local"
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dataset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        default=1,
        server_default="1",
        index=True,
    )
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    knowledge_base_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="SET NULL"), nullable=True
    )
    retry_of_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_jobs.id", ondelete="SET NULL"), nullable=True
    )

    workspace: Mapped[Workspace] = relationship()
    submitted_by_user: Mapped[User | None] = relationship(
        back_populates="submitted_jobs", foreign_keys=[submitted_by_user_id]
    )


class ChatSession(Base):
    """A live playground conversation. Separate from evaluation runs and release gates."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dataset_id", "workspace_id"],
            ["datasets.id", "datasets.workspace_id"],
            name="fk_chat_sessions_dataset_workspace",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=False,
        default=1,
        server_default="1",
        index=True,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="New chat", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    dataset: Mapped[Dataset] = relationship(
        back_populates="chat_sessions", foreign_keys=[dataset_id]
    )
    workspace: Mapped[Workspace] = relationship()
    created_by_user: Mapped[User | None] = relationship(
        back_populates="created_chat_sessions", foreign_keys=[created_by_user_id]
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    """One chat turn half. Assistant messages carry the full execution trace."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trace: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    saved_test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class AuthSession(Base):
    """Opaque server-side browser session; only the token digest is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_expires", "user_id", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    active_workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="auth_sessions")


class OIDCLoginTransaction(Base):
    """Short-lived, single-use OIDC state and PKCE transaction."""

    __tablename__ = "oidc_login_transactions"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ApiRateLimitBucket(Base):
    """Persistent fixed-window counters shared by API processes."""

    __tablename__ = "api_rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "principal_key", "category", "window_start", name="uq_api_rate_bucket_window"
        ),
        CheckConstraint(
            "category IN ('read', 'write', 'jobs')", name="ck_api_rate_bucket_category"
        ),
        CheckConstraint("request_count >= 0", name="ck_api_rate_bucket_count"),
        Index("ix_api_rate_bucket_window", "window_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_key: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
