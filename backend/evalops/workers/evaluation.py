"""Lease-fenced execution for deterministic evaluation jobs."""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, aliased, sessionmaker

from ..engine.job_fingerprints import canonical_request_hash
from ..engine.metrics import regex_time_budget
from ..engine.scoring import EvalConfig, ResponseSpec, summarize
from ..engine.versioning import METHODOLOGY_VERSION
from ..errors import NotFoundError, UnprocessableError
from ..models import EvaluationJob, EvaluationJobQueue, EvaluationRun
from ..quotas import count_workspace_queued_jobs
from ..repositories.runs import RunRepository
from ..schemas import RunCreate
from ..services.evaluations import RUN_REGEX_BUDGET_S, EvaluationService
from ..services.jobs import MAX_JOB_RETRIES, MAX_QUEUED_JOBS

log = logging.getLogger(__name__)
MAX_ATTEMPTS = MAX_JOB_RETRIES + 1
DEFAULT_LEASE_SECONDS = 120
PROGRESS_INTERVAL = 10


class LeaseLost(Exception):
    """The caller no longer owns the job lease and must stop immediately."""


class InputChanged(Exception):
    """Scoring inputs no longer match the submitted job snapshot."""


class JobCancelled(Exception):
    """Cancellation was requested while the job was running."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _begin_write(session: Session) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _transient_database_error(exc: BaseException) -> bool:
    if not isinstance(exc, (OperationalError, DBAPIError)):
        return False
    if getattr(exc, "connection_invalidated", False):
        return True
    original = getattr(exc, "orig", None)
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if code and (str(code).startswith("08") or code in {"40001", "40P01"}):
        return True
    if isinstance(original, sqlite3.OperationalError):
        message = str(original).lower()
        return "locked" in message or "busy" in message
    return False


class EvaluationJobWorker:
    """Single-process polling worker; each public operation opens its own Session."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        progress_interval: int = PROGRESS_INTERVAL,
        max_queued_per_workspace: int = 20,
        max_active_per_workspace: int = 1,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_seconds = lease_seconds
        self.progress_interval = max(1, progress_interval)
        self.max_queued_per_workspace = max_queued_per_workspace
        self.max_active_per_workspace = max_active_per_workspace

    def _session(self):
        return self.session_factory()

    def recover_one_expired(self) -> bool:
        """Requeue one stale attempt, or make it terminal if retries are exhausted."""
        session = self._session()
        try:
            _begin_write(session)
            now = _now()
            queue = session.scalar(
                select(EvaluationJobQueue).where(EvaluationJobQueue.id == 1).with_for_update()
            )
            job = session.scalar(
                select(EvaluationJob)
                .where(
                    EvaluationJob.status == "running",
                    EvaluationJob.lease_expires_at.is_not(None),
                    EvaluationJob.lease_expires_at <= now,
                )
                .order_by(EvaluationJob.lease_expires_at, EvaluationJob.id)
                .limit(1)
                .with_for_update()
            )
            if queue is None or job is None:
                session.rollback()
                return False
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
            elif job.attempt_count >= MAX_ATTEMPTS:
                self._terminal(
                    job,
                    "failed",
                    "retry_exhausted",
                    "Evaluation could not be completed after transient failures.",
                    now,
                )
            elif (
                queue.queued_count < MAX_QUEUED_JOBS
                and count_workspace_queued_jobs(session, job.workspace_id, exclude_job_id=job.id)
                < self.max_queued_per_workspace
            ):
                queue.queued_count += 1
                job.status = "queued"
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = now + self._backoff(job.attempt_count)
                job.error_code = "transient_infrastructure"
                job.error_message = "Temporary infrastructure issue; retry scheduled."
                job.updated_at = now
            else:
                # Park the retry without an active lease. It still occupies the workspace's
                # queued allowance, but does not block another active job from that workspace.
                job.lease_owner = None
                job.lease_expires_at = now + self._backoff(job.attempt_count)
                job.error_code = "transient_infrastructure"
                job.error_message = "Temporary infrastructure issue; retry is waiting for capacity."
                job.updated_at = now
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_next(self) -> tuple[int, str] | None:
        """Atomically claim the oldest available queued job and release queue capacity."""
        self.recover_one_expired()
        session = self._session()
        try:
            _begin_write(session)
            now = _now()
            queue = session.scalar(
                select(EvaluationJobQueue).where(EvaluationJobQueue.id == 1).with_for_update()
            )
            if queue is None:
                raise RuntimeError("evaluation job queue state is missing")
            queued_job = aliased(EvaluationJob)
            active_job = aliased(EvaluationJob)
            active_count = (
                select(func.count(active_job.id))
                .where(
                    active_job.workspace_id == queued_job.workspace_id,
                    active_job.status == "running",
                    active_job.lease_owner.is_not(None),
                    active_job.lease_expires_at > now,
                )
                .correlate(queued_job)
                .scalar_subquery()
            )
            job = session.scalar(
                select(queued_job)
                .where(
                    queued_job.status == "queued",
                    (queued_job.next_attempt_at.is_(None)) | (queued_job.next_attempt_at <= now),
                    active_count < self.max_active_per_workspace,
                )
                .order_by(queued_job.created_at, queued_job.id)
                .limit(1)
                .with_for_update()
            )
            if job is None:
                session.rollback()
                return None
            token = f"{self.worker_id}:{uuid4()}"
            changed = session.execute(
                update(EvaluationJob)
                .where(EvaluationJob.id == job.id, EvaluationJob.status == "queued")
                .values(
                    status="running",
                    started_at=job.started_at or now,
                    updated_at=now,
                    heartbeat_at=now,
                    lease_owner=token,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    attempt_count=EvaluationJob.attempt_count + 1,
                    next_attempt_at=None,
                    error_code=None,
                    error_message=None,
                )
            )
            if changed.rowcount != 1 or queue.queued_count < 1:
                session.rollback()
                return None
            queue.queued_count -= 1
            session.commit()
            return job.id, token
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def run_once(self) -> int | None:
        claimed = self.claim_next()
        if claimed is None:
            return None
        job_id, token = claimed
        try:
            snapshot = self._load_snapshot(job_id, token)
            self._execute(job_id, token, *snapshot)
            return job_id
        except JobCancelled:
            return job_id
        except LeaseLost:
            log.info("worker lease lost for evaluation job %s", job_id)
            return job_id
        except InputChanged:
            self._fail(job_id, token, "input_changed", "Scoring inputs changed after submission.")
            return job_id
        except Exception as exc:
            if _transient_database_error(exc):
                self._retry_or_fail(job_id, token)
            else:
                # Exceptions may include SQL parameters or user-supplied scoring inputs.
                log.error("evaluation job %s failed (%s)", job_id, type(exc).__name__)
                self._fail(job_id, token, "evaluation_failed", "Evaluation could not be completed.")
            return job_id

    def _load_snapshot(self, job_id: int, token: str):
        session = self._session()
        try:
            job = session.get(EvaluationJob, job_id)
            if job is None or job.status != "running" or job.lease_owner != token:
                raise LeaseLost
            now = _now()
            if _aware(job.lease_expires_at) is None or _aware(job.lease_expires_at) <= now:
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                raise JobCancelled
            expected = (
                job.dataset_id,
                job.dataset_version,
                job.scoring_input_fingerprint,
                job.knowledge_base_hash,
            )
            try:
                data = RunCreate.model_validate(job.request_payload)
            except Exception as exc:
                raise InputChanged from exc
            from ..services.jobs import EvaluationJobService

            dataset, cases, kb_hash, fingerprint = EvaluationJobService(
                session,
                workspace_id=job.workspace_id,
                user_id=job.submitted_by_user_id,
                submitter_key=job.submitter_key,
            )._validated_fingerprints(data)
            if dataset.workspace_id != job.workspace_id:
                raise InputChanged
            knowledge_base = {passage.passage_id: passage.text for passage in dataset.passages}
            if (dataset.id, dataset.version, fingerprint, kb_hash) != expected[:4]:
                raise InputChanged
            # Session closes after this method; ORM data is fully loaded and sessionmaker does
            # not expire on close. The worker scores this immutable in-memory snapshot.
            return data, dataset, cases, knowledge_base
        except NotFoundError as exc:
            raise InputChanged from exc
        except UnprocessableError as exc:
            raise InputChanged from exc
        finally:
            session.close()

    def _execute(self, job_id: int, token: str, data, dataset, cases, knowledge_base) -> None:
        responses = {
            response.case_key: ResponseSpec(
                response.output, response.latency_ms, response.retrieved_passage_ids
            )
            for response in data.responses
        }
        generations = {response.case_key: response.generation for response in data.responses}
        config = EvalConfig(**data.config.model_dump())
        case_results = []
        rows = []
        try:
            with regex_time_budget(RUN_REGEX_BUDGET_S):
                for index, case in enumerate(cases, start=1):
                    self._checkpoint(job_id, token, index - 1)
                    case_results.append(
                        EvaluationService._score(
                            case,
                            responses.get(case.key),
                            config,
                            rows,
                            knowledge_base,
                            generations.get(case.key),
                        )
                    )
                    # Each case is a cancellation/lease checkpoint. Progress is durable at
                    # bounded intervals, while heartbeat fencing is refreshed every case.
                    self._checkpoint(
                        job_id,
                        token,
                        index,
                        persist=(index % self.progress_interval == 0 or index == len(cases)),
                    )
        except JobCancelled:
            raise
        summary = summarize(case_results)
        self._finalize(job_id, token, data, dataset, knowledge_base, config, summary, rows)

    def _checkpoint(self, job_id: int, token: str, completed: int, *, persist: bool = True) -> None:
        session = self._session()
        try:
            _begin_write(session)
            job = session.get(EvaluationJob, job_id, with_for_update=True)
            if job is None or job.status != "running" or job.lease_owner != token:
                session.rollback()
                raise LeaseLost
            now = _now()
            if _aware(job.lease_expires_at) is None or _aware(job.lease_expires_at) <= now:
                session.rollback()
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
                session.commit()
                raise JobCancelled
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            if persist:
                job.completed_count = completed
            job.updated_at = now
            session.commit()
        except (LeaseLost, JobCancelled):
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _finalize(self, job_id, token, data, dataset, knowledge_base, config, summary, rows):
        session = self._session()
        try:
            _begin_write(session)
            job = session.get(EvaluationJob, job_id, with_for_update=True)
            if job is None:
                raise LeaseLost
            if job.status == "succeeded" and job.run_id is not None:
                # A prior commit may have succeeded despite a lost client acknowledgement.
                # This is a read-only idempotent acknowledgement; no stale worker mutation.
                session.commit()
                return job.run_id
            now = _now()
            if job.status != "running" or job.lease_owner != token:
                raise LeaseLost
            if _aware(job.lease_expires_at) is None or _aware(job.lease_expires_at) <= now:
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
                session.commit()
                return None
            try:
                data_from_job = RunCreate.model_validate(job.request_payload)
            except Exception as exc:
                raise InputChanged from exc
            if canonical_request_hash(data_from_job) != job.request_hash:
                raise InputChanged
            from ..services.jobs import EvaluationJobService

            verified_dataset, _cases, current_kb_hash, current_fingerprint = EvaluationJobService(
                session,
                workspace_id=job.workspace_id,
                user_id=job.submitted_by_user_id,
                submitter_key=job.submitter_key,
            )._validated_fingerprints(data_from_job)
            if (
                verified_dataset.workspace_id != job.workspace_id
                or verified_dataset.id != dataset.id
                or verified_dataset.version != job.dataset_version
                or current_kb_hash != job.knowledge_base_hash
                or current_fingerprint != job.scoring_input_fingerprint
                or data_from_job.model_dump(mode="json") != data.model_dump(mode="json")
            ):
                raise InputChanged
            run = EvaluationRun(
                dataset_id=verified_dataset.id,
                workspace_id=job.workspace_id,
                model_name=data.model_name,
                prompt_version=data.prompt_version,
                label=data.label,
                status="completed",
                config=asdict(config),
                summary=summary,
                pass_rate=summary["pass_rate"],
                methodology_version=METHODOLOGY_VERSION,
                dataset_version=verified_dataset.version,
                knowledge_base_hash=current_kb_hash,
                generation=data.generation,
                results=rows,
            )
            RunRepository(session).add(run)
            job.run_id = run.id
            job.status = "succeeded"
            job.completed_count = job.total_count
            job.finished_at = now
            job.updated_at = now
            job.heartbeat_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            job.error_message = None
            session.commit()
            return run.id
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _fail(self, job_id: int, token: str, code: str, message: str) -> None:
        self._finish_if_owner(job_id, token, "failed", code, message)

    def _retry_or_fail(self, job_id: int, token: str) -> None:
        session = self._session()
        try:
            _begin_write(session)
            job = session.get(EvaluationJob, job_id, with_for_update=True)
            if job is None or job.status != "running" or job.lease_owner != token:
                session.rollback()
                return
            now = _now()
            if _aware(job.lease_expires_at) is None or _aware(job.lease_expires_at) <= now:
                session.rollback()
                return
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
            elif job.attempt_count >= MAX_ATTEMPTS:
                self._terminal(
                    job,
                    "failed",
                    "retry_exhausted",
                    "Evaluation could not be completed after transient failures.",
                    now,
                )
            else:
                queue = session.scalar(
                    select(EvaluationJobQueue).where(EvaluationJobQueue.id == 1).with_for_update()
                )
                if (
                    queue is None
                    or queue.queued_count >= MAX_QUEUED_JOBS
                    or count_workspace_queued_jobs(session, job.workspace_id, exclude_job_id=job.id)
                    >= self.max_queued_per_workspace
                ):
                    # Keep the attempt recoverable without exceeding the hard queue cap.
                    # Once a queued job is claimed and frees a slot, stale recovery will
                    # requeue this job with its remaining bounded retry budget.
                    job.lease_owner = None
                    job.lease_expires_at = now + self._backoff(job.attempt_count)
                    job.error_code = "transient_infrastructure"
                    job.error_message = (
                        "Temporary infrastructure issue; retry is waiting for capacity."
                    )
                    job.updated_at = now
                else:
                    queue.queued_count += 1
                    job.status = "queued"
                    job.next_attempt_at = now + self._backoff(job.attempt_count)
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.error_code = "transient_infrastructure"
                    job.error_message = "Temporary infrastructure issue; retry scheduled."
                    job.updated_at = now
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _finish_if_owner(self, job_id, token, status, code, message) -> None:
        session = self._session()
        try:
            _begin_write(session)
            job = session.get(EvaluationJob, job_id, with_for_update=True)
            now = _now()
            if (
                job is not None
                and job.status == "running"
                and job.lease_owner == token
                and _aware(job.lease_expires_at) is not None
                and _aware(job.lease_expires_at) > now
            ):
                self._terminal(job, status, code, message, now)
                session.commit()
            else:
                session.rollback()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _terminal(job, status, code, message, now) -> None:
        job.status = status
        job.finished_at = now
        job.updated_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_code = code
        job.error_message = message

    @staticmethod
    def _backoff(attempt: int) -> timedelta:
        return timedelta(seconds=min(30, 2 ** max(0, attempt)))

    def serve(self, stop_event, *, poll_seconds: float = 0.5) -> None:
        while not stop_event.is_set():
            try:
                if self.run_once() is None:
                    stop_event.wait(poll_seconds)
            except Exception as exc:
                log.error("worker poll failed (%s)", type(exc).__name__)
                stop_event.wait(poll_seconds)
