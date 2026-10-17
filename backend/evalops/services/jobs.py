"""Durable evaluation-job submission, status, and cancellation management."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..engine.job_fingerprints import canonical_request_hash, scoring_input_hash
from ..engine.versioning import (
    METHODOLOGY_VERSION,
    case_definition,
    knowledge_base_hash,
)
from ..errors import BusyError, ConflictError, NotFoundError, QuotaExceededError, UnprocessableError
from ..models import EvaluationJob, EvaluationJobQueue
from ..quotas import count_workspace_queued_jobs
from ..repositories.datasets import DatasetRepository, TestCaseRepository
from ..repositories.jobs import EvaluationJobRepository
from ..schemas import (
    EvaluationJobOut,
    JobErrorOut,
    JobProgressOut,
    RunCreate,
)
from .evaluations import EvaluationService

MAX_QUEUED_JOBS = 100
MAX_JOB_RETRIES = 2


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvaluationJobService:
    def __init__(
        self,
        session: Session,
        *,
        workspace_id: int = 1,
        user_id: int | None = None,
        submitter_key: str = "dev:local",
        max_queued_per_workspace: int = 20,
    ):
        self.s = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.submitter_key = submitter_key
        self.max_queued_per_workspace = max_queued_per_workspace
        self.jobs = EvaluationJobRepository(session)
        self.datasets = DatasetRepository(session)
        self.cases = TestCaseRepository(session)

    def submit(self, data: RunCreate, idempotency_key: str) -> tuple[EvaluationJob, bool]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise UnprocessableError("Idempotency-Key must contain 1 to 200 characters")
        request_hash = canonical_request_hash(data)

        existing = self.jobs.get_by_idempotency_key(
            idempotency_key, workspace_id=self.workspace_id, submitter_key=self.submitter_key
        )
        if existing is not None:
            return self._replay(existing, request_hash)
        self.s.commit()

        dataset, cases, kb_hash, scoring_fingerprint = self._validated_fingerprints(data)
        self.s.commit()

        # SQLite's deferred transactions can allow two readers to observe the same queue
        # count before either writes. BEGIN IMMEDIATE serializes the short reservation phase.
        if self.s.get_bind().dialect.name == "sqlite":
            self.s.execute(text("BEGIN IMMEDIATE"))

        queue = self.jobs.queue_state(for_update=True)
        if queue is None:
            raise RuntimeError("evaluation job queue state is not initialized")
        # Recheck after taking the serialization lock to handle simultaneous retries of one key.
        existing = self.jobs.get_by_idempotency_key(
            idempotency_key,
            workspace_id=self.workspace_id,
            submitter_key=self.submitter_key,
            for_update=True,
        )
        if existing is not None:
            return self._replay(existing, request_hash)

        if count_workspace_queued_jobs(self.s, self.workspace_id) >= self.max_queued_per_workspace:
            self.s.rollback()
            raise QuotaExceededError("workspace queued evaluation-job limit reached")

        reservation = self.s.execute(
            update(EvaluationJobQueue)
            .where(
                EvaluationJobQueue.id == 1,
                EvaluationJobQueue.queued_count < MAX_QUEUED_JOBS,
            )
            .values(queued_count=EvaluationJobQueue.queued_count + 1)
        )
        if reservation.rowcount != 1:
            self.s.rollback()
            raise BusyError("evaluation queue is full; try again later")

        now = _utcnow()
        job = EvaluationJob(
            status="queued",
            workspace_id=self.workspace_id,
            submitted_by_user_id=self.user_id,
            submitter_key=self.submitter_key,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload=data.model_dump(mode="json"),
            dataset_id=dataset.id,
            dataset_version=dataset.version,
            scoring_input_fingerprint=scoring_fingerprint,
            knowledge_base_hash=kb_hash,
            completed_count=0,
            total_count=len(cases),
            created_at=now,
            updated_at=now,
            attempt_count=0,
        )
        self.s.add(job)
        try:
            self.s.commit()
        except IntegrityError:
            self.s.rollback()
            existing = self.jobs.get_by_idempotency_key(
                idempotency_key, workspace_id=self.workspace_id, submitter_key=self.submitter_key
            )
            if existing is not None:
                return self._replay(existing, request_hash)
            raise
        return job, False

    def _validated_fingerprints(self, data: RunCreate):
        dataset = self.datasets.get(data.dataset_id, workspace_id=self.workspace_id)
        if dataset is None:
            raise NotFoundError(f"dataset {data.dataset_id} not found")
        cases = self.cases.list_for_dataset(dataset.id)
        if not cases:
            raise UnprocessableError(f"dataset {dataset.id} has no test cases")

        by_key = {case.key: case for case in cases}
        unknown = sorted({response.case_key for response in data.responses} - by_key.keys())
        if unknown:
            raise UnprocessableError(f"responses reference unknown case keys: {unknown}")

        knowledge_base = {passage.passage_id: passage.text for passage in dataset.passages}
        EvaluationService._validate_rag_inputs(cases, data, knowledge_base)
        kb_hash = knowledge_base_hash(knowledge_base)
        case_inputs = [
            {
                "id": case.id,
                "key": case.key,
                "definition": case_definition(case),
                "tags": list(case.tags),
            }
            for case in cases
        ]
        response_inputs = [
            {
                "case_key": response.case_key,
                "output": response.output,
                "latency_ms": response.latency_ms,
                "retrieved_passage_ids": response.retrieved_passage_ids,
            }
            for response in data.responses
        ]
        config = data.config.model_dump(mode="json")
        fingerprint = scoring_input_hash(
            dataset_id=dataset.id,
            dataset_version=dataset.version,
            methodology_version=METHODOLOGY_VERSION,
            config=config,
            cases=case_inputs,
            knowledge_base_hash=kb_hash,
            responses=response_inputs,
        )
        return dataset, cases, kb_hash, fingerprint

    def _replay(self, job: EvaluationJob, request_hash: str) -> tuple[EvaluationJob, bool]:
        if job.request_hash != request_hash:
            self.s.rollback()
            raise ConflictError("Idempotency-Key was already used with a different request")
        self.s.commit()
        return job, True

    def get(self, job_id: int) -> EvaluationJob:
        job = self.jobs.get(job_id, workspace_id=self.workspace_id, user_id=self.user_id)
        if job is None:
            raise NotFoundError(f"evaluation job {job_id} not found")
        return job

    def cancel(self, job_id: int) -> EvaluationJob:
        if self.s.get_bind().dialect.name == "sqlite":
            self.s.execute(text("BEGIN IMMEDIATE"))
        job = self.jobs.get(
            job_id, workspace_id=self.workspace_id, user_id=self.user_id, for_update=True
        )
        if job is None:
            self.s.rollback()
            raise NotFoundError(f"evaluation job {job_id} not found")
        if job.status in {"succeeded", "failed", "cancelled"}:
            self.s.rollback()
            raise ConflictError(f"evaluation job is already {job.status}")

        now = _utcnow()
        if job.cancellation_requested_at is None:
            job.cancellation_requested_at = now
        if job.status == "queued":
            job.status = "cancelled"
            job.finished_at = now
            state = self.jobs.queue_state(for_update=True)
            if state is None or state.queued_count < 1:
                self.s.rollback()
                raise RuntimeError("evaluation job queue count is inconsistent")
            self.s.execute(
                update(EvaluationJobQueue)
                .where(EvaluationJobQueue.id == 1, EvaluationJobQueue.queued_count > 0)
                .values(queued_count=EvaluationJobQueue.queued_count - 1)
            )
        elif job.lease_owner is None:
            # A retry parked for capacity is not actively running; cancel it immediately.
            job.status = "cancelled"
            job.finished_at = now
            job.lease_expires_at = None
        job.updated_at = now
        self.s.commit()
        return job


def job_out(job: EvaluationJob, *, replayed: bool = False) -> EvaluationJobOut:
    fraction = job.completed_count / job.total_count if job.total_count else None
    error = None
    if job.error_code is not None and job.error_message is not None:
        error = JobErrorOut(code=job.error_code, message=job.error_message)
    return EvaluationJobOut(
        job_id=job.id,
        status=job.status,
        status_url=f"/evaluations/jobs/{job.id}",
        dataset_id=job.dataset_id,
        dataset_version=job.dataset_version,
        scoring_input_fingerprint=job.scoring_input_fingerprint,
        knowledge_base_hash=job.knowledge_base_hash,
        progress=JobProgressOut(
            completed=job.completed_count,
            total=job.total_count,
            fraction=fraction,
        ),
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        heartbeat_at=job.heartbeat_at,
        finished_at=job.finished_at,
        attempt_count=job.attempt_count,
        lease_owner=job.lease_owner,
        lease_expires_at=job.lease_expires_at,
        cancellation_requested_at=job.cancellation_requested_at,
        error=error,
        run_id=job.run_id,
        retry_of_job_id=job.retry_of_job_id,
        replayed=replayed,
    )
