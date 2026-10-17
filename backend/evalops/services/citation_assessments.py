"""Workspace-scoped submission and read service for durable citation assessments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..citation_assessments import (
    build_citation_input,
    config_provenance,
)
from ..citation_evaluator_v4 import VERSION
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
    CitationAssessmentResult,
    ClaimAssessmentJob,
    Dataset,
    EvaluationResult,
    EvaluationRun,
    Passage,
)
from ..schemas import (
    CitationAssessmentJobOut,
    CitationAssessmentResultOut,
    JobErrorOut,
    JobProgressOut,
)

MAX_GLOBAL_QUEUED = 50


def utcnow() -> datetime:
    return datetime.now(UTC)


def _begin_write(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


class CitationAssessmentService:
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

    def submit(
        self,
        run_id: int,
        result_id: int,
        *,
        citation_expected: bool,
        idempotency_key: str,
    ) -> tuple[CitationAssessmentJob, bool]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise UnprocessableError("Idempotency-Key must contain 1 to 200 characters")
        request = {
            "evaluator": "citation",
            "run_id": run_id,
            "result_id": result_id,
            "citation_expected": citation_expected,
        }
        request_hash = canonical_json_hash(request)
        ledger = self._ledger_by_key(idempotency_key)
        if ledger is not None:
            if ledger.evaluator_kind != "citation":
                raise ConflictError("Idempotency-Key was already used for another evaluator")
            if ledger.request_hash != request_hash:
                raise ConflictError("Idempotency-Key was already used with a different request")
            existing = self._by_key(idempotency_key)
            if existing is not None:
                return self._replay(existing, request_hash)
        existing = self._by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, request_hash)
        self.s.commit()

        _run, result, passages = self._load_inputs(run_id, result_id)
        _input, input_fingerprint, kb_hash = build_citation_input(
            _run, result, passages, citation_expected=citation_expected
        )
        predictor_hash, runner_hash, config_digest = config_provenance()

        _begin_write(self.s)
        queue = self.s.scalar(
            select(CitationAssessmentQueue).where(CitationAssessmentQueue.id == 1).with_for_update()
        )
        if queue is None:
            raise RuntimeError("citation assessment queue state is missing")
        existing = self._by_key(idempotency_key, for_update=True)
        if existing is not None:
            return self._replay(existing, request_hash)
        ledger = self._ledger_by_key(idempotency_key, for_update=True)
        if ledger is not None:
            if ledger.evaluator_kind != "citation" or ledger.request_hash != request_hash:
                self.s.rollback()
                raise ConflictError("Idempotency-Key was already used with a different request")
            existing = self._by_key(idempotency_key)
            if existing is not None:
                return self._replay(existing, request_hash)
        workspace_queued = (
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
        workspace_queued += (
            self.s.scalar(
                select(func.count())
                .select_from(ClaimAssessmentJob)
                .where(
                    ClaimAssessmentJob.workspace_id == self.workspace_id,
                    ClaimAssessmentJob.status == "queued",
                )
            )
            or 0
        )
        if workspace_queued >= self.max_queued_per_workspace:
            self.s.rollback()
            raise QuotaExceededError("workspace citation assessment queue is full")
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
            raise BusyError("citation assessment queue is full; try again later")

        now = utcnow()
        sequence = self.s.scalar(
            select(AssessmentIdSequence).where(AssessmentIdSequence.id == 1).with_for_update()
        )
        if sequence is None:
            self.s.rollback()
            raise RuntimeError("assessment ID allocator is missing")
        assessment_id = sequence.next_id
        sequence.next_id += 1
        job = CitationAssessmentJob(
            id=assessment_id,
            evaluator_kind="citation",
            workspace_id=self.workspace_id,
            submitted_by_user_id=self.user_id,
            submitter_key=self.submitter_key,
            run_id=_run.id,
            result_id=result.id,
            dataset_id=_run.dataset_id,
            case_key=result.case_key,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload={"citation_expected": citation_expected},
            citation_expected=citation_expected,
            status="queued",
            input_fingerprint=input_fingerprint,
            knowledge_base_hash=kb_hash,
            predictor_sha256=predictor_hash,
            runner_sha256=runner_hash,
            config_digest=config_digest,
            completed_count=0,
            total_count=1,
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
                evaluator_kind="citation",
                request_hash=request_hash,
                assessment_id=assessment_id,
            )
        )
        try:
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            existing = self._by_key(idempotency_key)
            if existing is not None:
                return self._replay(existing, request_hash)
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

    def _by_key(self, key: str, *, for_update: bool = False):
        stmt = select(CitationAssessmentJob).where(
            CitationAssessmentJob.workspace_id == self.workspace_id,
            CitationAssessmentJob.submitter_key == self.submitter_key,
            CitationAssessmentJob.idempotency_key == key,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)

    def _ledger_by_key(self, key: str, *, for_update: bool = False):
        stmt = select(AssessmentIdempotency).where(
            AssessmentIdempotency.workspace_id == self.workspace_id,
            AssessmentIdempotency.submitter_key == self.submitter_key,
            AssessmentIdempotency.idempotency_key == key,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.s.scalar(stmt)

    def _replay(self, job: CitationAssessmentJob, request_hash: str):
        if job.request_hash != request_hash:
            self.s.rollback()
            raise ConflictError("Idempotency-Key was already used with a different request")
        self.s.commit()
        return job, True

    def get(self, assessment_id: int) -> CitationAssessmentJob:
        job = self.s.scalar(
            select(CitationAssessmentJob).where(
                CitationAssessmentJob.id == assessment_id,
                CitationAssessmentJob.workspace_id == self.workspace_id,
            )
        )
        if job is None:
            raise NotFoundError("citation assessment not found")
        return job

    def get_result(self, assessment_id: int) -> CitationAssessmentResultOut:
        row = self.s.execute(
            select(CitationAssessmentResult, CitationAssessmentJob)
            .join(
                CitationAssessmentJob, CitationAssessmentJob.id == CitationAssessmentResult.job_id
            )
            .where(
                CitationAssessmentResult.job_id == assessment_id,
                CitationAssessmentResult.workspace_id == self.workspace_id,
                CitationAssessmentJob.workspace_id == self.workspace_id,
                CitationAssessmentJob.status == "succeeded",
            )
        ).first()
        if row is None:
            job = self.get(assessment_id)
            if job.status != "succeeded":
                raise ConflictError("citation assessment result is not available yet")
            raise NotFoundError("citation assessment result not found")
        result, _job = row
        payload = result.prediction_payload
        return CitationAssessmentResultOut(
            assessment_id=result.job_id,
            run_id=result.run_id,
            result_id=result.result_id,
            case_key=result.case_key,
            evaluator_version=result.evaluator_version,
            predictor_sha256=result.predictor_sha256,
            runner_sha256=result.runner_sha256,
            config_digest=result.config_digest,
            input_fingerprint=result.input_fingerprint,
            knowledge_base_hash=result.knowledge_base_hash,
            status=result.status,
            reason_code=result.reason_code,
            reason=result.reason,
            per_citation=payload["per_citation"],
            created_at=result.created_at,
        )

    def cancel(self, assessment_id: int) -> CitationAssessmentJob:
        _begin_write(self.s)
        job = self.s.scalar(
            select(CitationAssessmentJob)
            .where(
                CitationAssessmentJob.id == assessment_id,
                CitationAssessmentJob.workspace_id == self.workspace_id,
            )
            .with_for_update()
        )
        if job is None:
            self.s.rollback()
            raise NotFoundError("citation assessment not found")
        if job.status in {"succeeded", "failed", "cancelled"}:
            self.s.rollback()
            raise ConflictError(f"citation assessment is already {job.status}")
        now = utcnow()
        if job.cancellation_requested_at is None:
            job.cancellation_requested_at = now
        if job.status == "queued":
            queue = self.s.scalar(
                select(CitationAssessmentQueue)
                .where(CitationAssessmentQueue.id == 1)
                .with_for_update()
            )
            if queue is None or queue.queued_count < 1:
                self.s.rollback()
                raise RuntimeError("citation assessment queue counter is inconsistent")
            queue.queued_count -= 1
            job.status = "cancelled"
            job.finished_at = now
            job.updated_at = now
        self.s.commit()
        return job


def job_out(job: CitationAssessmentJob, *, replayed: bool = False) -> CitationAssessmentJobOut:
    return CitationAssessmentJobOut(
        assessment_id=job.id,
        evaluator="citation",
        status=job.status,
        status_url=f"/evaluations/assessments/{job.id}",
        result_url=f"/evaluations/assessments/{job.id}/result",
        run_id=job.run_id,
        result_id=job.result_id,
        progress=JobProgressOut(
            completed=job.completed_count,
            total=job.total_count,
            fraction=job.completed_count / job.total_count if job.total_count else None,
        ),
        evaluator_version=VERSION,
        predictor_sha256=job.predictor_sha256,
        runner_sha256=job.runner_sha256,
        config_digest=job.config_digest,
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
            JobErrorOut(code=job.error_code, message=job.error_message)
            if job.error_code and job.error_message
            else None
        ),
        replayed=replayed,
    )
