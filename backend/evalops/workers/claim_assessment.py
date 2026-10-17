"""Lease-fenced background worker for semantic-aggregate-v12 claim assessments."""

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

from ..claim_assessments import (
    AGGREGATION_VERSION,
    MODEL_ID,
    MODEL_REVISION,
    build_claim_inputs,
    claim_output,
    config_digest,
    make_local_verifier,
    source_sha256,
)
from ..errors import (
    InconsistentRetrievalError,
    MissingRetrievalMetadataError,
    ModelUnavailableError,
    NotFoundError,
    StaleKnowledgeBaseError,
    UnprocessableError,
)
from ..models import (
    CitationAssessmentJob,
    CitationAssessmentQueue,
    ClaimAssessmentJob,
    ClaimAssessmentResult,
)
from ..semantic.nli import SemanticBackendUnavailable
from ..services.citation_assessments import MAX_GLOBAL_QUEUED, utcnow
from ..services.claim_assessments import ClaimAssessmentService

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
DEFAULT_LEASE_SECONDS = 120


class LeaseLost(Exception):
    pass


class AssessmentCancelled(Exception):
    pass


class AssessmentInputChanged(Exception):
    def __init__(self, code="input_changed", message="Assessment inputs changed after submission."):
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


class ClaimAssessmentWorker:
    """One-process worker. The Transformers model is constructed only after claim acquisition."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_queued_per_workspace: int = 10,
        verifier_factory=make_local_verifier,
        heartbeat_interval: float | None = None,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.lease_seconds = lease_seconds
        self.max_queued_per_workspace = max_queued_per_workspace
        self.verifier_factory = verifier_factory
        self.heartbeat_interval = heartbeat_interval or max(1.0, min(30.0, lease_seconds / 3))
        self._verifier = None

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
                select(ClaimAssessmentJob)
                .where(
                    ClaimAssessmentJob.status == "running",
                    ClaimAssessmentJob.lease_expires_at.is_not(None),
                    ClaimAssessmentJob.lease_expires_at <= now,
                )
                .order_by(ClaimAssessmentJob.lease_expires_at, ClaimAssessmentJob.id)
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
                    "Claim assessment stopped after transient infrastructure failures.",
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
                raise RuntimeError("assessment queue state is missing")
            active_claim = aliased(ClaimAssessmentJob)
            active_citation = aliased(CitationAssessmentJob)
            queued = aliased(ClaimAssessmentJob)
            active = (
                select(
                    func.count(active_claim.id)
                    + select(func.count(active_citation.id))
                    .where(
                        active_citation.status == "running",
                        active_citation.lease_owner.is_not(None),
                        active_citation.lease_expires_at > now,
                    )
                    .scalar_subquery()
                )
                .where(
                    active_claim.status == "running",
                    active_claim.lease_owner.is_not(None),
                    active_claim.lease_expires_at > now,
                )
                .scalar_subquery()
            )
            job = session.scalar(
                select(queued)
                .where(
                    queued.status == "queued",
                    (queued.next_attempt_at.is_(None)) | (queued.next_attempt_at <= now),
                    active == 0,
                )
                .order_by(queued.created_at, queued.id)
                .limit(1)
                .with_for_update()
            )
            if job is None:
                session.rollback()
                return None
            token = f"{self.worker_id}:{uuid4()}"
            changed = session.execute(
                update(ClaimAssessmentJob)
                .where(ClaimAssessmentJob.id == job.id, ClaimAssessmentJob.status == "queued")
                .values(
                    status="running",
                    started_at=job.started_at or now,
                    updated_at=now,
                    heartbeat_at=now,
                    lease_owner=token,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    attempt_count=ClaimAssessmentJob.attempt_count + 1,
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
            question, claims, evidence, expected = self._load_and_verify(job_id, token)
            predictions = []
            heartbeat_stop = threading.Event()
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(job_id, token, heartbeat_stop),
                name=f"claim-assessment-heartbeat-{job_id}",
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                if claims:
                    if self._verifier is None:
                        self._verifier = self.verifier_factory()
                    for idx, claim in enumerate(claims):
                        self._checkpoint(job_id, token, completed=idx)
                        verdict = self._verifier.verify(question, claim["text"], evidence)
                        row = claim_output(claim["text"], verdict)
                        predictions.append(row)
                        self._checkpoint(job_id, token, completed=idx + 1)
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join(timeout=1)
            self._finalize(job_id, token, predictions, expected)
            return job_id
        except AssessmentCancelled:
            return job_id
        except LeaseLost:
            log.info("claim assessment lease lost for job %s", job_id)
            return job_id
        except AssessmentInputChanged as exc:
            self._fail(job_id, token, exc.code, exc.public_message)
            return job_id
        except ModelUnavailableError:
            self._fail(
                job_id,
                token,
                "model_unavailable",
                "The pinned local NLI model or dependencies are unavailable.",
            )
            return job_id
        except Exception as exc:
            if isinstance(exc, (SemanticBackendUnavailable, ImportError, OSError)):
                self._fail(
                    job_id,
                    token,
                    "model_unavailable",
                    "The pinned local NLI model or dependencies are unavailable.",
                )
            elif _transient_database_error(exc):
                self._retry_or_fail(job_id, token)
            else:
                log.error("claim assessment %s failed (%s)", job_id, type(exc).__name__)
                self._fail(
                    job_id, token, "assessment_failed", "Claim assessment could not be completed."
                )
            return job_id

    def _load_and_verify(self, job_id: int, token: str):
        session = self.session_factory()
        try:
            job = session.get(ClaimAssessmentJob, job_id)
            if not self._owns(job, token):
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                self._cancel_owned(job_id, token)
                raise AssessmentCancelled
            service = ClaimAssessmentService(
                session,
                workspace_id=job.workspace_id,
                user_id=job.submitted_by_user_id,
                submitter_key=job.submitter_key,
            )
            run, result, passages = service._load_inputs(job.run_id, job.result_id)
            question, claims, evidence, fingerprint, kb_hash = build_claim_inputs(
                run, result, passages
            )
            actual = (fingerprint, kb_hash, source_sha256(), config_digest())
            expected = (
                job.input_fingerprint,
                job.knowledge_base_hash,
                job.source_sha256,
                job.config_digest,
            )
            if (
                actual != expected
                or run.workspace_id != job.workspace_id
                or run.dataset_id != job.dataset_id
                or job.model_id != MODEL_ID
                or job.model_revision != MODEL_REVISION
            ):
                raise AssessmentInputChanged
            if len(claims) != job.total_count:
                raise AssessmentInputChanged
            return question, claims, evidence, expected
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
    def _owns(job: ClaimAssessmentJob | None, token: str) -> bool:
        return bool(
            job
            and job.status == "running"
            and job.lease_owner == token
            and job.lease_expires_at
            and _aware(job.lease_expires_at) > utcnow()
        )

    def _checkpoint(self, job_id: int, token: str, *, completed: int) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(ClaimAssessmentJob).where(ClaimAssessmentJob.id == job_id).with_for_update()
            )
            if not self._owns(job, token):
                session.rollback()
                raise LeaseLost
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, utcnow())
                session.commit()
                raise AssessmentCancelled
            now = utcnow()
            job.completed_count = completed
            job.heartbeat_at = job.updated_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            session.commit()
        except (LeaseLost, AssessmentCancelled):
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _heartbeat_loop(self, job_id: int, token: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_interval):
            session = self.session_factory()
            try:
                _begin_write(session)
                job = session.scalar(
                    select(ClaimAssessmentJob)
                    .where(ClaimAssessmentJob.id == job_id)
                    .with_for_update()
                )
                if not self._owns(job, token):
                    session.rollback()
                    return
                if job.cancellation_requested_at is None:
                    now = utcnow()
                    job.heartbeat_at = job.updated_at = now
                    job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                session.commit()
            except Exception as exc:
                session.rollback()
                log.warning("claim assessment heartbeat failed (%s)", type(exc).__name__)
            finally:
                session.close()

    def _finalize(self, job_id: int, token: str, predictions: list[dict], expected: tuple) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(ClaimAssessmentJob).where(ClaimAssessmentJob.id == job_id).with_for_update()
            )
            if not self._owns(job, token):
                raise LeaseLost
            now = utcnow()
            if job.cancellation_requested_at is not None:
                self._terminal(job, "cancelled", None, None, now)
                session.commit()
                return
            service = ClaimAssessmentService(
                session,
                workspace_id=job.workspace_id,
                user_id=job.submitted_by_user_id,
                submitter_key=job.submitter_key,
            )
            try:
                run, result, passages = service._load_inputs(job.run_id, job.result_id)
                _question, claims, _evidence, fingerprint, kb_hash = build_claim_inputs(
                    run, result, passages
                )
            except MissingRetrievalMetadataError as exc:
                raise AssessmentInputChanged("retrieval_metadata_missing", str(exc)) from exc
            except StaleKnowledgeBaseError as exc:
                raise AssessmentInputChanged("stale_knowledge_base", str(exc)) from exc
            except InconsistentRetrievalError as exc:
                raise AssessmentInputChanged("inconsistent_retrieval_metadata", str(exc)) from exc
            except UnprocessableError as exc:
                raise AssessmentInputChanged("input_changed", str(exc)) from exc
            except NotFoundError as exc:
                raise AssessmentInputChanged("input_changed") from exc
            if (
                (
                    fingerprint,
                    kb_hash,
                    source_sha256(),
                    config_digest(),
                )
                != expected
                or run.workspace_id != job.workspace_id
                or run.dataset_id != job.dataset_id
            ):
                raise AssessmentInputChanged
            if job.model_id != MODEL_ID or job.model_revision != MODEL_REVISION:
                raise AssessmentInputChanged("model_configuration_changed")
            if len(predictions) != len(claims):
                raise AssessmentInputChanged
            output_status = "assessed" if predictions else "no_checkable_claims"
            session.add(
                ClaimAssessmentResult(
                    job_id=job.id,
                    workspace_id=job.workspace_id,
                    run_id=job.run_id,
                    result_id=job.result_id,
                    case_key=job.case_key,
                    evaluator_version=AGGREGATION_VERSION,
                    source_sha256=job.source_sha256,
                    config_digest=job.config_digest,
                    model_id=job.model_id,
                    model_revision=job.model_revision,
                    input_fingerprint=job.input_fingerprint,
                    knowledge_base_hash=job.knowledge_base_hash,
                    output_status=output_status,
                    claims=predictions,
                    created_at=now,
                )
            )
            job.status = "succeeded"
            job.completed_count = job.total_count
            job.finished_at = job.updated_at = job.heartbeat_at = now
            job.lease_owner = job.lease_expires_at = None
            job.error_code = job.error_message = None
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
                select(ClaimAssessmentJob).where(ClaimAssessmentJob.id == job_id).with_for_update()
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
                    "Claim assessment stopped after transient infrastructure failures.",
                    now,
                )
            else:
                queue = session.scalar(
                    select(CitationAssessmentQueue)
                    .where(CitationAssessmentQueue.id == 1)
                    .with_for_update()
                )
                if (
                    queue
                    and queue.queued_count < MAX_GLOBAL_QUEUED
                    and self._workspace_queued(session, job.workspace_id, exclude=job.id)
                    < self.max_queued_per_workspace
                ):
                    delay = min(30, 2**job.attempt_count)
                    queue.queued_count += 1
                    job.status = "queued"
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.next_attempt_at = now + timedelta(seconds=delay)
                    job.error_code = "transient_infrastructure"
                    job.error_message = "Temporary infrastructure issue; retry scheduled."
                    job.updated_at = now
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _fail(self, job_id: int, token: str, code: str, message: str) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(ClaimAssessmentJob).where(ClaimAssessmentJob.id == job_id).with_for_update()
            )
            if self._owns(job, token):
                self._terminal(job, "failed", code, message, utcnow())
                session.commit()
            else:
                session.rollback()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _cancel_owned(self, job_id: int, token: str) -> None:
        self._fail_or_cancel(job_id, token)

    def _fail_or_cancel(self, job_id: int, token: str) -> None:
        session = self.session_factory()
        try:
            _begin_write(session)
            job = session.scalar(
                select(ClaimAssessmentJob).where(ClaimAssessmentJob.id == job_id).with_for_update()
            )
            if self._owns(job, token):
                self._terminal(job, "cancelled", None, None, utcnow())
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _terminal(job, status, code, message, now):
        job.status = status
        job.finished_at = job.updated_at = now
        job.lease_owner = job.lease_expires_at = None
        job.error_code = code
        job.error_message = message

    def _workspace_queued(
        self, session: Session, workspace_id: int, *, exclude: int | None = None
    ) -> int:
        claim_stmt = (
            select(func.count())
            .select_from(ClaimAssessmentJob)
            .where(
                ClaimAssessmentJob.workspace_id == workspace_id,
                ClaimAssessmentJob.status == "queued",
            )
        )
        citation_stmt = (
            select(func.count())
            .select_from(CitationAssessmentJob)
            .where(
                CitationAssessmentJob.workspace_id == workspace_id,
                CitationAssessmentJob.status == "queued",
            )
        )
        if exclude is not None:
            claim_stmt = claim_stmt.where(ClaimAssessmentJob.id != exclude)
        return (session.scalar(claim_stmt) or 0) + (session.scalar(citation_stmt) or 0)

    def serve(self, stop: threading.Event, *, poll_seconds: float = 0.5) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be greater than zero")
        while not stop.is_set():
            started = time.monotonic()
            job_id = self.run_once()
            if job_id is None:
                stop.wait(max(0, poll_seconds - (time.monotonic() - started)))
