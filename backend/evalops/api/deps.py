from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..auth import AuthenticatedPrincipal, require_principal
from ..chat.service import ChatService
from ..models import ApiRateLimitBucket, Workspace, WorkspaceMembership
from ..services.citation_assessments import CitationAssessmentService
from ..services.claim_assessments import ClaimAssessmentService
from ..services.datasets import DatasetService
from ..services.evaluations import EvaluationService
from ..services.jobs import EvaluationJobService


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def get_authenticated_principal(request: Request, session: SessionDep) -> AuthenticatedPrincipal:
    principal, auth_session = require_principal(request, session, request.app.state.settings)
    request.state.auth_session_record = auth_session
    enforce_authenticated_rate_limit(request, session, principal, request.app.state.settings)
    return principal


def enforce_authenticated_rate_limit(
    request: Request, session: Session, principal: AuthenticatedPrincipal, settings
) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        category, limit = "read", settings.api_read_requests_per_minute
    elif request.method == "POST" and (
        request.url.path == "/evaluations/jobs" or request.url.path.endswith("/assessments")
    ):
        category, limit = "jobs", settings.api_job_submissions_per_minute
    else:
        category, limit = "write", settings.api_write_requests_per_minute

    now = datetime.now(UTC)
    window = now.replace(second=0, microsecond=0)
    principal_key = (
        f"user:{principal.user_id}" if principal.user_id is not None else "development:loopback"
    )
    table = ApiRateLimitBucket.__table__
    dialect = session.get_bind().dialect.name
    insert = (
        sqlite_insert if dialect == "sqlite" else pg_insert if dialect == "postgresql" else None
    )
    if insert is None:
        raise HTTPException(
            status_code=503, detail="database does not support persistent rate limiting"
        )
    statement = insert(table).values(
        principal_key=principal_key,
        category=category,
        window_start=window,
        request_count=1,
    )
    statement = statement.on_conflict_do_update(
        index_elements=["principal_key", "category", "window_start"],
        set_={"request_count": table.c.request_count + 1},
    ).returning(table.c.request_count)
    try:
        count = session.execute(statement).scalar_one()
        session.execute(delete(table).where(table.c.window_start < window - timedelta(minutes=2)))
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(
            status_code=503, detail="persistent rate limiting is unavailable"
        ) from exc
    if count > limit:
        retry_after = max(1, 60 - now.second)
        raise HTTPException(
            status_code=429,
            detail="request rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


AuthenticatedPrincipalDep = Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)]


@dataclass(frozen=True)
class WorkspaceAccess:
    principal: AuthenticatedPrincipal
    workspace_id: int
    role: str


def get_workspace_access(
    request: Request,
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
    workspace_header: Annotated[int | None, Header(alias="X-Workspace-ID")] = None,
) -> WorkspaceAccess:
    # Development identity is loopback-only and deliberately confined to the reserved
    # legacy workspace. Real users can select only a workspace for which membership exists.
    if principal.mode == "development":
        return WorkspaceAccess(principal, 1, "owner")
    stmt = (
        select(WorkspaceMembership)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.user_id == principal.user_id,
            Workspace.is_legacy.is_(False),
        )
    )
    selected_workspace_id = workspace_header
    if selected_workspace_id is None:
        auth_session = getattr(request.state, "auth_session_record", None)
        selected_workspace_id = auth_session.active_workspace_id if auth_session else None
    if selected_workspace_id is not None:
        stmt = stmt.where(WorkspaceMembership.workspace_id == selected_workspace_id)
    memberships = list(session.scalars(stmt.order_by(WorkspaceMembership.workspace_id)))
    if not memberships:
        raise HTTPException(status_code=404, detail="workspace not found")
    # Without an explicit selector, choose the lowest-ID membership deterministically.
    # Clients can select another verified membership with X-Workspace-ID.
    membership = memberships[0]
    return WorkspaceAccess(principal, membership.workspace_id, membership.role)


WorkspaceAccessDep = Annotated[WorkspaceAccess, Depends(get_workspace_access)]


def require_editor(access: WorkspaceAccessDep) -> WorkspaceAccess:
    if access.role not in {"owner", "admin", "editor"}:
        raise HTTPException(status_code=403, detail="editor role required")
    return access


EditorAccessDep = Annotated[WorkspaceAccess, Depends(require_editor)]


def get_dataset_service(
    request: Request, session: SessionDep, access: WorkspaceAccessDep
) -> DatasetService:
    settings = request.app.state.settings
    return DatasetService(
        session,
        workspace_id=access.workspace_id,
        max_datasets=settings.max_datasets_per_workspace,
        max_test_cases_per_dataset=settings.max_test_cases_per_dataset,
        max_passages_per_workspace=settings.max_passages_per_workspace,
    )


def get_evaluation_service(session: SessionDep, access: WorkspaceAccessDep) -> EvaluationService:
    return EvaluationService(session, workspace_id=access.workspace_id)


def get_evaluation_job_service(
    request: Request, session: SessionDep, access: WorkspaceAccessDep
) -> EvaluationJobService:
    return EvaluationJobService(
        session,
        workspace_id=access.workspace_id,
        user_id=access.principal.user_id,
        max_queued_per_workspace=request.app.state.settings.max_queued_jobs_per_workspace,
        submitter_key=(
            f"user:{access.principal.user_id}" if access.principal.user_id else "dev:local"
        ),
    )


DatasetServiceDep = Annotated[DatasetService, Depends(get_dataset_service)]
EvaluationServiceDep = Annotated[EvaluationService, Depends(get_evaluation_service)]
EvaluationJobServiceDep = Annotated[EvaluationJobService, Depends(get_evaluation_job_service)]


def get_chat_service(
    request: Request, session: SessionDep, access: WorkspaceAccessDep
) -> ChatService:
    return ChatService(
        session,
        request.app.state.chat_runtime,
        request.app.state.settings,
        request.app.state.retriever_cache,
        workspace_id=access.workspace_id,
        user_id=access.principal.user_id,
    )


ChatServiceDep = Annotated[ChatService, Depends(get_chat_service)]


def get_citation_assessment_service(
    request: Request, session: SessionDep, access: WorkspaceAccessDep
) -> CitationAssessmentService:
    return CitationAssessmentService(
        session,
        workspace_id=access.workspace_id,
        user_id=access.principal.user_id,
        submitter_key=(
            f"user:{access.principal.user_id}" if access.principal.user_id else "dev:local"
        ),
        max_queued_per_workspace=(
            request.app.state.settings.max_queued_citation_assessments_per_workspace
        ),
    )


CitationAssessmentServiceDep = Annotated[
    CitationAssessmentService, Depends(get_citation_assessment_service)
]


def get_claim_assessment_service(
    request: Request, session: SessionDep, access: WorkspaceAccessDep
) -> ClaimAssessmentService:
    return ClaimAssessmentService(
        session,
        workspace_id=access.workspace_id,
        user_id=access.principal.user_id,
        submitter_key=(
            f"user:{access.principal.user_id}" if access.principal.user_id else "dev:local"
        ),
        max_queued_per_workspace=(
            request.app.state.settings.max_queued_citation_assessments_per_workspace
        ),
    )


ClaimAssessmentServiceDep = Annotated[ClaimAssessmentService, Depends(get_claim_assessment_service)]
