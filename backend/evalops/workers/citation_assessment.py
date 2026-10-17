"""Single-process, lease-fenced worker for frozen deterministic citation assessments."""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, aliased, sessionmaker

from ..citation_assessments import (
    build_citation_input,
    config_provenance,
    predict_citation,
    prediction_payload,
)
from ..errors import (
    InconsistentRetrievalError,
    MissingRetrievalMetadataError,
    NotFoundError,
    StaleKnowledgeBaseError,
    UnprocessableError,
)
from ..models import (
    CitationAssessmentJob,
    CitationAssessmentQueue,
    CitationAssessmentResult,
    ClaimAssessmentJob,
)
from ..services.citation_assessments import MAX_GLOBAL_QUEUED, CitationAssessmentService, utcnow

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3  # initial attempt plus at most two transient retries
DEFAULT_LEASE_SECONDS = 120


class LeaseLost(Exception):
    pass


class AssessmentCancelled(Exception):
    pass


class AssessmentInputChanged(Exception):
    def __init__(
        self,
        code: str = "input_changed",
        message: str = "Assessment inputs changed after submission.",
    ):
        super().__init__(message)
        self.code = code
        self.public_message = message


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
    if isinstance(original, sqlite3.OperationalError):
        return any(word in str(original).lower() for word in ("locked", "busy"))
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return bool(state and (str(state).startswith("08") or state in {"40001", "40P01"}))


class CitationAssessmentWorker:
    """Bounded worker; each DB operation owns a fresh SQLAlchemy session."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_queued_per_workspace: int = 10,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_seconds = lease_seconds
        self.max_queued_per_workspace = max_queued_per_workspace

    def recover_one_expired(self) -> bool:
        session = self.session_factory()
        try:
            _begin_write(session)
            now = utcnow()
            queue = session.scalar(
                select(CitationAssessmentQueue)
                .where(CitationAssessmentQueue.id == 1)
                .with_for_update()
            )
            job = session.scalar(
                select(CitationAssessmentJob)
                .where(
                    CitationAssessmentJob.status == "running",
                    CitationAssessmentJob.lease_expires_at.is_not(None),
                    CitationAssessmentJob.lease_expires_at <= now,
                )
                .order_by(CitationAssessmentJob.lease_expires_at, CitationAssessmentJob.id)
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
                    "Citation assessment stopped after transient infrastructure failures.",
                    now,
                )
            elif (
                queue.queued_count < MAX_GLOBAL_QUEUED
                and self._workspace_queued(session, job.workspace_id, exclude=job.id)
                < self.max_queued_per_workspace
            ):
                queue.queued_count += 1
                job.status = "queued"
                job.lease_owner = None
                job.lease_expires_at = None
                job.next_attempt_at = now + timedelta(seconds=min(30, 2**job.attempt_count))
                job.error_code = "transient_infrastructure"
                job.error_message = "Temporary infrastructure issue; retry scheduled."
                job.updated_at = now
            else:
                # Keep it recoverable without consuming a queue slot or active lease.
                job.lease_owner = None
                job.lease_expires_at = now + timedelta(seconds=5)
                job.updated_at = now
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_next(self) -> tuple[int, str] | None:
        self.recover_one_expired()
        session = self.session_factory()
        try:
            _begin_write(session)
            now = utcnow()
            queue = session.scalar(
                select(CitationAssessmentQueue)
                .where(CitationAssessmentQueue.id == 1)
                .with_for_update()
            )
            if queue is None:
                raise RuntimeError("citation assessment queue state is missing")
            active_alias = aliased(CitationAssessmentJob)
            active_claim_alias = aliased(ClaimAssessmentJob)
            queued_alias = aliased(CitationAssessmentJob)
            active = select(
                select(func.count(active_alias.id))
                .where(
                    active_alias.status == "running",
                    active_alias.lease_owner.is_not(None),
                    active_alias.lease_expires_at > now,
                )
                .scalar_subquery()
                + select(func.count(active_claim_alias.id))
                .where(
                    active_claim_alias.status == "running",
                    active_claim_alias.lease_owner.is_not(None),
                    active_claim_alias.lease_expires_at > now,
                )
                .scalar_subquery()
            ).scalar_subquery()
            job = session.scalar(
                select(queued_alias)
                .where(
                    queued_alias.status == "queued",
                    (queued_alias.next_attempt_at.is_(None))
                    | (queued_alias.next_attempt_at <= now),
                    active == 0,
                )
                .order_by(queued_alias.created_at, queued_alias.id)
                .limit(1)
                .with_for_update()
            )
            if job is None:
                session.rollback()
                return None
            token = f"{self.worker_id}:{uuid4()}"
            changed = session.execute(
                update(CitationAssessmentJob)
                .where(
                    CitationAssessmentJob.id == job.id,
                    CitationAssessmentJob.status == "queued",
                )
                .values(
                    status="running",
                    started_at=job.started_at or now,
                    updated_at=now,
                    heartbeat_at=now,
                    lease_owner=token,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    attempt_count=CitationAssessmentJob.attempt_count + 1,
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
            inputs, expected = self._load_and_verify(job_id, token)
            self._checkpoint(job_id, token)
            prediction = predict_citation(inputs)
            payload = prediction_payload(prediction)
            self._finalize(job_id, token, payload, expected)
            return job_id
        except AssessmentCancelled:
            return job_id
        except LeaseLost:
            log.info("citation assessment lease lost for job %s", job_id)
            return job_id
        except AssessmentInputChanged as exc:
            self._fail(job_id, token, exc.code, exc.public_message)
            return job_id
        except Exception as exc:
            if _transient_database_error(exc):
                self._retry_or_fail(job_id, token)
            else:
                log.error("citation assessment %s failed (%s)", job_id, type(exc).__name__)
                self._fail(
                    job_id,
                    token,
                    "assessment_failed",
                    "Citation assessment could not be completed.",
                )
            return job_id

    def _load_and_verify(self, job_id: int, token: str):
        session = self.session_factory()
        try:
            job = session.get(CitationAssessmentJob, job_id)
            if not self._owns(job, token):
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                self._cancel_owned(job_id, token)
                raise AssessmentCancelled
            service = CitationAssessmentService(
                session,
                workspace_id=job.workspace_id,
                user_id=job.submitted_by_user_id,
                submitter_key=job.submitter_key,
            )
            run, result, passages = service._load_inputs(job.run_id, job.result_id)
            inputs, fingerprint, kb_hash = build_citation_input(
                run, result, passages, citation_expected=job.citation_expected
            )
            provenance = config_provenance()
            expected = (
                job.input_fingerprint,
                job.knowledge_base_hash,
                job.predictor_sha256,
                job.runner_sha256,
                job.config_digest,
            )
            actual = (fingerprint, kb_hash, *provenance)
            if expected != actual or run.workspace_id != job.workspace_id:
                raise AssessmentInputChanged
            return inputs, expected
        except MissingRetrievalMetadataError as exc:
            raise AssessmentInputChanged("retrieval_metadata_missing", str(exc)) from exc
        except StaleKnowledgeBaseError as exc:
            raise AssessmentInputChanged("stale_knowledge_base", str(exc)) from exc
        except InconsistentRetrievalError as exc:
            raise AssessmentInputChanged("inconsistent_retrieval_metadata", str(exc)) from exc
        except (NotFoundError, UnprocessableError) as exc:
            raise AssessmentInputChanged from exc
        finally:
            session.close()

    @staticmethod
    def _owns(job: CitationAssessmentJob | None, token: str) -> bool:
        return bool(
            job is not None
            and job.status == "running"
            and job.lease_owner == token
            and job.lease_expires_at is not None
            and _aware(job.lease_expires_at) > utcnow()
        )

    def _checkpoint(self, job_id: int, token: str) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(CitationAssessmentJob)
                .where(CitationAssessmentJob.id == job_id)
                .with_for_update()
            )
            if not self._owns(job, token):
                session.rollback()
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, utcnow())
                session.commit()
                raise AssessmentCancelled
            now = utcnow()
            job.heartbeat_at = now
            job.updated_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            session.commit()
        except (LeaseLost, AssessmentCancelled):
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _finalize(self, job_id: int, token: str, payload: dict, expected: tuple) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(CitationAssessmentJob)
                .where(CitationAssessmentJob.id == job_id)
                .with_for_update()
            )
            if job is None:
                raise LeaseLost
            if job.status == "succeeded":
                session.rollback()
                return
            if not self._owns(job, token):
                raise LeaseLost
            now = utcnow()
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
                session.commit()
                return
            service = CitationAssessmentService(
                session,
                workspace_id=job.workspace_id,
                user_id=job.submitted_by_user_id,
                submitter_key=job.submitter_key,
            )
            run, result, passages = service._load_inputs(job.run_id, job.result_id)
            try:
                _inputs, fingerprint, kb_hash = build_citation_input(
                    run, result, passages, citation_expected=job.citation_expected
                )
            except MissingRetrievalMetadataError as exc:
                raise AssessmentInputChanged("retrieval_metadata_missing", str(exc)) from exc
            except StaleKnowledgeBaseError as exc:
                raise AssessmentInputChanged("stale_knowledge_base", str(exc)) from exc
            except InconsistentRetrievalError as exc:
                raise AssessmentInputChanged("inconsistent_retrieval_metadata", str(exc)) from exc
            except UnprocessableError as exc:
                raise AssessmentInputChanged("input_changed", str(exc)) from exc
            provenance = config_provenance()
            if (
                fingerprint,
                kb_hash,
                *provenance,
            ) != expected or run.workspace_id != job.workspace_id:
                raise AssessmentInputChanged
            if (
                session.scalar(
                    select(CitationAssessmentResult.id).where(
                        CitationAssessmentResult.job_id == job.id
                    )
                )
                is not None
            ):
                raise RuntimeError("citation assessment output already exists for a running job")
            session.add(
                CitationAssessmentResult(
                    job_id=job.id,
                    workspace_id=job.workspace_id,
                    run_id=job.run_id,
                    result_id=job.result_id,
                    case_key=job.case_key,
                    evaluator_version=payload["version"],
                    predictor_sha256=job.predictor_sha256,
                    runner_sha256=job.runner_sha256,
                    config_digest=job.config_digest,
                    input_fingerprint=job.input_fingerprint,
                    knowledge_base_hash=job.knowledge_base_hash,
                    status=payload["status"],
                    reason_code=payload["reason_code"],
                    reason=payload["reason"],
                    per_citation=payload["per_citation"],
                    prediction_payload=payload,
                    created_at=now,
                )
            )
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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _retry_or_fail(self, job_id: int, token: str) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(CitationAssessmentJob)
                .where(CitationAssessmentJob.id == job_id)
                .with_for_update()
            )
            if not self._owns(job, token):
                session.rollback()
                return
            now = utcnow()
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
            elif job.attempt_count >= MAX_ATTEMPTS:
                self._terminal(
                    job,
                    "failed",
                    "retry_exhausted",
                    "Citation assessment stopped after transient infrastructure failures.",
                    now,
                )
            else:
                queue = session.scalar(
                    select(CitationAssessmentQueue)
                    .where(CitationAssessmentQueue.id == 1)
                    .with_for_update()
                )
                workspace_queued = self._workspace_queued(session, job.workspace_id, exclude=job.id)
                delay = min(30, 2**job.attempt_count)
                job.lease_owner = None
                job.lease_expires_at = now + timedelta(seconds=delay)
                job.error_code = "transient_infrastructure"
                job.error_message = "Temporary infrastructure issue; retry scheduled."
                job.updated_at = now
                # Recovery moves this parked job back into the bounded queue when capacity exists.
                if (
                    queue is not None
                    and queue.queued_count < MAX_GLOBAL_QUEUED
                    and workspace_queued < self.max_queued_per_workspace
                ):
                    queue.queued_count += 1
                    job.status = "queued"
                    job.next_attempt_at = now + timedelta(seconds=delay)
                    job.lease_expires_at = None
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _fail(self, job_id: int, token: str, code: str, message: str) -> None:
        self._finish_if_owner(job_id, token, "failed", code, message)

    def _cancel_owned(self, job_id: int, token: str) -> None:
        self._finish_if_owner(job_id, token, "cancelled", None, None)

    def _finish_if_owner(self, job_id: int, token: str, status: str, code, message) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(CitationAssessmentJob)
                .where(CitationAssessmentJob.id == job_id)
                .with_for_update()
            )
            if self._owns(job, token):
                self._terminal(job, status, code, message, utcnow())
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

    def _workspace_queued(
        self, session: Session, workspace_id: int, *, exclude: int | None = None
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(CitationAssessmentJob)
            .where(
                CitationAssessmentJob.workspace_id == workspace_id,
                CitationAssessmentJob.status == "queued",
            )
        )
        if exclude is not None:
            stmt = stmt.where(CitationAssessmentJob.id != exclude)
        citation_count = session.scalar(stmt) or 0
        claim_stmt = (
            select(func.count())
            .select_from(ClaimAssessmentJob)
            .where(
                ClaimAssessmentJob.workspace_id == workspace_id,
                ClaimAssessmentJob.status == "queued",
            )
        )
        if exclude is not None:
            claim_stmt = claim_stmt.where(ClaimAssessmentJob.id != exclude)
        return citation_count + (session.scalar(claim_stmt) or 0)

    def serve(self, stop: threading.Event, *, poll_seconds: float = 0.5) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be greater than zero")
        while not stop.is_set():
            started = time.monotonic()
            job_id = self.run_once()
            if job_id is None:
                stop.wait(max(0, poll_seconds - (time.monotonic() - started)))
