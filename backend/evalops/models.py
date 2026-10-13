"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import false as sa_false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="dataset")
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="RESTRICT"), nullable=False, index=True
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

    dataset: Mapped[Dataset] = relationship(back_populates="runs")
    results: Mapped[list[EvaluationResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="EvaluationResult.case_key"
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("run_id", "case_key", name="uq_result_run_case"),)

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
