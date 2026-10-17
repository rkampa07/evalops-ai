"""Workspace-scoped submission and result access for frozen claim assessments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..claim_assessments import (
    AGGREGATION_VERSION,
    MODEL_ID,
    MODEL_REVISION,
    build_claim_inputs,
    config_digest,
    source_sha256,
)
from ..engine.job_fingerprints import canonical_json_hash
from ..errors import (
    BusyError,
    ConflictError,
    NotFoundError,
    QuotaExceededError,
    UnprocessableError,
)
from ..models import (
    AssessmentIdempotency,
    AssessmentIdSequence,
    CitationAssessmentJob,
    CitationAssessmentQueue,
    ClaimAssessmentJob,
    ClaimAssessmentResult,
    Dataset,
    EvaluationResult,
    EvaluationRun,
    Passage,
)
from ..schemas import (
    ClaimAssessmentJobOut,
    ClaimAssessmentResultOut,
    ClaimVerdictOut,
    JobErrorOut,
    JobProgressOut,
)

MAX_GLOBAL_QUEUED = 50


def utcnow() -> datetime:
    return datetime.now(UTC)


def _begin_write(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


class ClaimAssessmentService:
    def __init__(
        self,
        session: Session,
        *,
        workspace_id: int,
        user_id: int | None,
        submitter_key: str,
        max_queued_per_workspace: int = 10,
    ):
        self.s = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.submitter_key = submitter_key
        self.max_queued_per_workspace = max_queued_per_workspace

    def submit(self, run_id: int, result_id: int, *, idempotency_key: str):
        if not idempotency_key or len(idempotency_key) > 200:
            raise UnprocessableError("Idempotency-Key must contain 1 to 200 characters")
        request_hash = canonical_json_hash(
            {"evaluator": "claim", "run_id": run_id, "result_id": result_id}
        )
        ledger = self._ledger_by_key(idempotency_key)
        if ledger is not None:
            if ledger.evaluator_kind != "claim" or ledger.request_hash != request_hash:
                raise ConflictError("Idempotency-Key was already used with a different request")
            job = self._by_key(idempotency_key)
            if job is not None:
                self.s.commit()
                return job, True
        existing = self._by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, request_hash)
        self.s.commit()

        run, result, passages = self._load_inputs(run_id, result_id)
        _question, claims, _evidence, input_fingerprint, kb_hash = build_claim_inputs(
            run, result, passages
        )
        source_hash = source_sha256()
        digest = config_digest()

        _begin_write(self.s)
        queue = self.s.scalar(
            select(CitationAssessmentQueue).where(CitationAssessmentQueue.id == 1).with_for_update()
        )
        if queue is None:
            raise RuntimeError("assessment queue state is missing")
        ledger = self._ledger_by_key(idempotency_key, for_update=True)
        if ledger is not None:
            if ledger.evaluator_kind != "claim" or ledger.request_hash != request_hash:
                self.s.rollback()
                raise ConflictError("Idempotency-Key was already used with a different request")
            existing = self._by_key(idempotency_key)
            if existing is not None:
                self.s.commit()
                return existing, True
        workspace_count = (
            self.s.scalar(
                select(func.count())
                .select_from(ClaimAssessmentJob)
                .where(
                    ClaimAssessmentJob.workspace_id == self.workspace_id,
                    ClaimAssessmentJob.status == "queued",
                )
            )
            or 0
        ) + (
            self.s.scalar(
                select(func.count())
                .select_from(CitationAssessmentJob)
                .where(
                    CitationAssessmentJob.workspace_id == self.workspace_id,
                    CitationAssessmentJob.status == "queued",
                )
            )
            or 0
        )
        if workspace_count >= self.max_queued_per_workspace:
            self.s.rollback()
            raise QuotaExceededError("workspace assessment queue is full")
        reserved = self.s.execute(
            update(CitationAssessmentQueue)
            .where(
                CitationAssessmentQueue.id == 1,
                CitationAssessmentQueue.queued_count < MAX_GLOBAL_QUEUED,
            )
            .values(queued_count=CitationAssessmentQueue.queued_count + 1)
        )
        if reserved.rowcount != 1:
            self.s.rollback()
            raise BusyError("assessment queue is full; try again later")
        sequence = self.s.scalar(
            select(AssessmentIdSequence).where(AssessmentIdSequence.id == 1).with_for_update()
        )
        if sequence is None:
            self.s.rollback()
            raise RuntimeError("assessment ID allocator is missing")
        assessment_id = sequence.next_id
        sequence.next_id += 1
        now = utcnow()
        job = ClaimAssessmentJob(
            id=assessment_id,
            workspace_id=self.workspace_id,
            submitted_by_user_id=self.user_id,
            submitter_key=self.submitter_key,
            run_id=run.id,
            result_id=result.id,
            dataset_id=run.dataset_id,
            case_key=result.case_key,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload={},
            status="queued",
            input_fingerprint=input_fingerprint,
            knowledge_base_hash=kb_hash,
            source_sha256=source_hash,
            config_digest=digest,
            model_id=MODEL_ID,
            model_revision=MODEL_REVISION,
            completed_count=0,
            total_count=len(claims),
            attempt_count=0,
            created_at=now,
            updated_at=now,
        )
        self.s.add(job)
        self.s.add(
            AssessmentIdempotency(
                workspace_id=self.workspace_id,
                submitter_key=self.submitter_key,
                idempotency_key=idempotency_key,
                evaluator_kind="claim",
                request_hash=request_hash,
                assessment_id=assessment_id,
            )
        )
        try:
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            ledger = self._ledger_by_key(idempotency_key)
            if ledger and ledger.evaluator_kind == "claim" and ledger.request_hash == request_hash:
                existing = self._by_key(idempotency_key)
                if existing is not None:
                    self.s.commit()
                    return existing, True
            raise
        return job, False

    def _load_inputs(self, run_id: int, result_id: int):
        row = self.s.execute(
            select(EvaluationRun, EvaluationResult, Dataset)
            .join(EvaluationResult, EvaluationResult.run_id == EvaluationRun.id)
            .join(Dataset, Dataset.id == EvaluationRun.dataset_id)
            .where(
                EvaluationRun.id == run_id,
                EvaluationRun.workspace_id == self.workspace_id,
                EvaluationResult.id == result_id,
                Dataset.workspace_id == self.workspace_id,
            )
        ).first()
        if row is None:
            raise NotFoundError("saved result not found")
        run, result, dataset = row
        passages = dict(
            self.s.execute(
                select(Passage.passage_id, Passage.text)
                .where(Passage.dataset_id == dataset.id)
                .order_by(Passage.passage_id)
            ).all()
        )
        return run, result, passages

    def _ledger_by_key(self, key: str, *, for_update: bool = False):
        stmt = select(AssessmentIdempotency).where(
            AssessmentIdempotency.workspace_id == self.workspace_id,
            AssessmentIdempotency.submitter_key == self.submitter_key,
            AssessmentIdempotency.idempotency_key == key,
        )
        return self.s.scalar(stmt.with_for_update() if for_update else stmt)

    def _by_key(self, key: str):
        return self.s.scalar(
            select(ClaimAssessmentJob).where(
                ClaimAssessmentJob.workspace_id == self.workspace_id,
                ClaimAssessmentJob.submitter_key == self.submitter_key,
                ClaimAssessmentJob.idempotency_key == key,
            )
        )

    def _replay(self, job, request_hash):
        if job.request_hash != request_hash:
            self.s.rollback()
            raise ConflictError("Idempotency-Key was already used with a different request")
        self.s.commit()
        return job, True

    def get(self, assessment_id: int) -> ClaimAssessmentJob:
        job = self.s.scalar(
            select(ClaimAssessmentJob).where(
                ClaimAssessmentJob.id == assessment_id,
                ClaimAssessmentJob.workspace_id == self.workspace_id,
            )
        )
        if job is None:
            raise NotFoundError("assessment not found")
        return job

    def get_result(self, assessment_id: int) -> ClaimAssessmentResultOut:
        row = self.s.execute(
            select(ClaimAssessmentResult, ClaimAssessmentJob)
            .join(ClaimAssessmentJob, ClaimAssessmentJob.id == ClaimAssessmentResult.job_id)
            .where(
                ClaimAssessmentResult.job_id == assessment_id,
                ClaimAssessmentResult.workspace_id == self.workspace_id,
                ClaimAssessmentJob.workspace_id == self.workspace_id,
                ClaimAssessmentJob.status == "succeeded",
            )
        ).first()
        if row is None:
            job = self.get(assessment_id)
            if job.status != "succeeded":
                raise ConflictError("claim assessment result is not available yet")
            raise NotFoundError("claim assessment result not found")
        result, _job = row
        return ClaimAssessmentResultOut(
            assessment_id=result.job_id,
            run_id=result.run_id,
            result_id=result.result_id,
            case_key=result.case_key,
            evaluator_version=result.evaluator_version,
            source_sha256=result.source_sha256,
            config_digest=result.config_digest,
            model_id=result.model_id,
            model_revision=result.model_revision,
            input_fingerprint=result.input_fingerprint,
            knowledge_base_hash=result.knowledge_base_hash,
            status=result.output_status,
            claims=[ClaimVerdictOut.model_validate(item) for item in result.claims],
            created_at=result.created_at,
        )

    def cancel(self, assessment_id: int) -> ClaimAssessmentJob:
        _begin_write(self.s)
        job = self.s.scalar(
            select(ClaimAssessmentJob)
            .where(
                ClaimAssessmentJob.id == assessment_id,
                ClaimAssessmentJob.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        if job is None:
            self.s.rollback()
            raise NotFoundError("assessment not found")
        if job.status == "queued":
            queue = self.s.scalar(
                select(CitationAssessmentQueue)
                .where(CitationAssessmentQueue.id == 1)
                .with_for_update()
            )
            job.status = "cancelled"
            job.finished_at = job.updated_at = utcnow()
            if queue and queue.queued_count > 0:
                queue.queued_count -= 1
        elif job.status == "running":
            job.cancellation_requested_at = utcnow()
            job.updated_at = utcnow()
        self.s.commit()
        return job


def claim_job_out(job: ClaimAssessmentJob, *, replayed: bool = False) -> ClaimAssessmentJobOut:
    return ClaimAssessmentJobOut(
        assessment_id=job.id,
        evaluator="claim",
        status=job.status,
        status_url=f"/evaluations/assessments/{job.id}",
        result_url=f"/evaluations/assessments/{job.id}/result",
        run_id=job.run_id,
        result_id=job.result_id,
        progress=JobProgressOut(
            completed=job.completed_count,
            total=job.total_count,
            fraction=(job.completed_count / job.total_count if job.total_count else None),
        ),
        evaluator_version=AGGREGATION_VERSION,
        source_sha256=job.source_sha256,
        config_digest=job.config_digest,
        model_id=job.model_id,
        model_revision=job.model_revision,
        input_fingerprint=job.input_fingerprint,
        knowledge_base_hash=job.knowledge_base_hash,
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        heartbeat_at=job.heartbeat_at,
        finished_at=job.finished_at,
        attempt_count=job.attempt_count,
        cancellation_requested=job.cancellation_requested_at is not None,
        error=(
            JobErrorOut(code=job.error_code, message=job.error_message) if job.error_code else None
        ),
        replayed=replayed,
    )
