"""Browser authentication endpoints. Workspace authorization is intentionally separate."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..auth import (
    OIDC_STATE_COOKIE,
    SESSION_COOKIE,
    begin_login,
    finish_login,
    issue_login_cookies,
    require_principal,
    revoke_session,
    session_payload,
)
from ..models import Workspace, WorkspaceMembership
from .deps import SessionDep, enforce_authenticated_rate_limit

router = APIRouter(prefix="/auth", tags=["authentication"])


class WorkspaceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: int = Field(gt=0)


@router.get("/login")
def login(request: Request, session: SessionDep):
    if request.app.state.settings.auth_mode != "oidc":
        return Response(status_code=404)
    url, state = begin_login(session, request.app.state.settings)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=request.app.state.settings.auth_cookie_secure,
        samesite="lax",
        path="/auth",
    )
    return response


@router.get("/callback")
def callback(
    request: Request,
    session: SessionDep,
    code: Annotated[str | None, Query(max_length=4096)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=100)] = None,
):
    if error or not code or not state:
        return Response(status_code=400, content="OIDC login failed")
    location = finish_login(
        code=code,
        state=state,
        state_cookie=request.cookies.get(OIDC_STATE_COOKIE),
        request=request,
        session=session,
        settings=request.app.state.settings,
    )
    redirect = RedirectResponse(location, status_code=303)
    issue_login_cookies(redirect, request, request.app.state.settings)
    return redirect


@router.get("/session")
def current_session(request: Request, session: SessionDep):
    payload = session_payload(request, session, request.app.state.settings)
    principal, record = (
        require_principal(request, session, request.app.state.settings)
        if payload["authenticated"]
        else (None, None)
    )
    if principal is not None:
        enforce_authenticated_rate_limit(request, session, principal, request.app.state.settings)
    request.state.auth_session_record = record
    return payload


@router.put("/session/workspace")
def select_workspace(body: WorkspaceSelection, request: Request, session: SessionDep):
    principal, record = require_principal(request, session, request.app.state.settings)
    enforce_authenticated_rate_limit(request, session, principal, request.app.state.settings)
    if principal.mode != "oidc" or record is None or principal.user_id is None:
        raise HTTPException(403, detail="workspace switching requires an OIDC session")
    membership = session.scalar(
        select(WorkspaceMembership)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.user_id == principal.user_id,
            WorkspaceMembership.workspace_id == body.workspace_id,
            Workspace.is_legacy.is_(False),
        )
    )
    if membership is None:
        raise HTTPException(404, detail="workspace not found")
    record.active_workspace_id = body.workspace_id
    session.commit()
    return session_payload(request, session, request.app.state.settings)


@router.post("/logout", status_code=204)
def logout(request: Request, session: SessionDep):
    principal, _ = require_principal(request, session, request.app.state.settings)
    enforce_authenticated_rate_limit(request, session, principal, request.app.state.settings)
    revoke_session(request, session)
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=request.app.state.settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
