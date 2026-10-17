"""Workspace provisioning and user-bound invitation endpoints."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import User, Workspace, WorkspaceInvitation, WorkspaceMembership, utcnow
from .deps import AuthenticatedPrincipalDep, SessionDep, get_authenticated_principal

router = APIRouter(tags=["workspaces"], dependencies=[Depends(get_authenticated_principal)])
Role = Literal["owner", "admin", "editor", "viewer"]
InviteRole = Literal["admin", "editor", "viewer"]


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: int = Field(gt=0)
    role: InviteRole


class InvitationAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=32, max_length=200)


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role


def _workspace_item(workspace: Workspace, role: str) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "role": role,
        "is_legacy": workspace.is_legacy,
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _membership(
    session: Session, workspace_id: int, user_id: int, *, allow_legacy: bool = False
) -> WorkspaceMembership:
    row = session.execute(
        select(WorkspaceMembership, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            Workspace.is_legacy.is_(allow_legacy),
        )
    ).first()
    if row is None:
        raise HTTPException(404, detail="workspace not found")
    return row[0]


def _require_manager(
    session: Session, workspace_id: int, user_id: int, *, owner_only: bool = False
) -> tuple[WorkspaceMembership, Workspace]:
    membership = _membership(session, workspace_id, user_id)
    workspace = session.get(Workspace, workspace_id)
    if workspace is None or workspace.is_legacy:
        raise HTTPException(404, detail="workspace not found")
    allowed = {"owner"} if owner_only else {"owner", "admin"}
    if membership.role not in allowed:
        raise HTTPException(403, detail="workspace management permission required")
    return membership, workspace


def _member_out(membership: WorkspaceMembership) -> dict:
    user = membership.user
    return {
        "user_id": user.id,
        "display_name": user.display_name,
        "email": user.email,
        "role": membership.role,
        "joined_at": membership.created_at,
    }


@router.get("/workspaces")
def list_workspaces(
    request: Request, session: SessionDep, principal: AuthenticatedPrincipalDep
) -> dict:
    if principal.mode == "development":
        workspace = session.get(Workspace, 1)
        items = [_workspace_item(workspace, "owner")] if workspace and workspace.is_legacy else []
        return {"items": items, "active_workspace_id": 1 if items else None}
    rows = list(
        session.execute(
            select(Workspace, WorkspaceMembership.role)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(WorkspaceMembership.user_id == principal.user_id, Workspace.is_legacy.is_(False))
            .order_by(Workspace.id)
        )
    )
    # The auth session stores the preferred workspace, but every returned row comes from a
    # currently valid membership join.
    request_session = getattr(request.state, "auth_session_record", None)
    active = request_session.active_workspace_id if request_session else None
    ids = {workspace.id for workspace, _ in rows}
    if active not in ids:
        active = rows[0][0].id if rows else None
    return {
        "items": [_workspace_item(workspace, role) for workspace, role in rows],
        "active_workspace_id": active,
    }


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: WorkspaceCreate, session: SessionDep, principal: AuthenticatedPrincipalDep
) -> dict:
    if principal.mode != "oidc" or principal.user_id is None:
        raise HTTPException(403, detail="workspace creation requires an OIDC identity")
    user = session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(401, detail="authentication required")
    workspace = Workspace(name=body.name.strip(), slug=f"ws-{secrets.token_hex(12)}")
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    session.commit()
    return _workspace_item(workspace, "owner")


@router.get("/workspaces/{workspace_id}/members")
def list_members(
    workspace_id: Annotated[int, Path(gt=0)],
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
) -> dict:
    if principal.user_id is None:
        raise HTTPException(404, detail="workspace not found")
    _membership(session, workspace_id, principal.user_id)
    members = list(
        session.scalars(
            select(WorkspaceMembership)
            .where(WorkspaceMembership.workspace_id == workspace_id)
            .order_by(WorkspaceMembership.created_at, WorkspaceMembership.user_id)
        )
    )
    return {"items": [_member_out(item) for item in members]}


@router.post("/workspaces/{workspace_id}/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(
    workspace_id: Annotated[int, Path(gt=0)],
    body: InvitationCreate,
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
) -> dict:
    if principal.user_id is None:
        raise HTTPException(404, detail="workspace not found")
    actor, _ = _require_manager(session, workspace_id, principal.user_id)
    if actor.role == "admin" and body.role not in {"editor", "viewer"}:
        raise HTTPException(403, detail="admins may invite editors or viewers")
    if body.target_user_id == principal.user_id:
        raise HTTPException(400, detail="cannot invite yourself")
    target = session.get(User, body.target_user_id)
    if target is None:
        raise HTTPException(404, detail="user not found")
    if session.get(WorkspaceMembership, (body.target_user_id, workspace_id)):
        raise HTTPException(409, detail="user is already a workspace member")
    now = utcnow()
    session.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.target_user_id == body.target_user_id,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at <= now,
        )
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    pending = session.scalar(
        select(WorkspaceInvitation.id).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.target_user_id == body.target_user_id,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at > now,
        )
    )
    if pending is not None:
        raise HTTPException(409, detail="an active invitation already exists")
    raw_token = secrets.token_urlsafe(32)
    invitation = WorkspaceInvitation(
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        workspace_id=workspace_id,
        target_user_id=target.id,
        invited_by_user_id=principal.user_id,
        role=body.role,
        expires_at=now + timedelta(days=7),
    )
    session.add(invitation)
    session.commit()
    # The token is shown once for secure out-of-band delivery; only its digest is persisted.
    return {
        "id": invitation.id,
        "workspace_id": workspace_id,
        "target_user_id": target.id,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
        "token": raw_token,
    }


@router.post("/workspaces/invitations/accept")
def accept_invitation(
    body: InvitationAccept, session: SessionDep, principal: AuthenticatedPrincipalDep
) -> dict:
    if principal.mode != "oidc" or principal.user_id is None:
        raise HTTPException(403, detail="invitation acceptance requires an OIDC identity")
    digest = hashlib.sha256(body.token.encode()).hexdigest()
    invitation = session.scalar(
        select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == digest)
    )
    now = utcnow()
    if (
        invitation is None
        or invitation.target_user_id != principal.user_id
        or invitation.accepted_at is not None
        or invitation.revoked_at is not None
        or _utc(invitation.expires_at) <= now
    ):
        raise HTTPException(404, detail="invitation not found or expired")
    workspace = session.get(Workspace, invitation.workspace_id)
    if workspace is None or workspace.is_legacy:
        raise HTTPException(404, detail="invitation not found or expired")
    if session.get(WorkspaceMembership, (principal.user_id, workspace.id)):
        invitation.accepted_at = now
        session.commit()
        raise HTTPException(409, detail="user is already a workspace member")
    # Conditional claim makes the one-time token single-use under concurrent acceptance.
    claimed = session.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.id == invitation.id,
            WorkspaceInvitation.accepted_at.is_(None),
            WorkspaceInvitation.revoked_at.is_(None),
            WorkspaceInvitation.expires_at > now,
        )
        .values(accepted_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise HTTPException(404, detail="invitation not found or expired")
    session.add(
        WorkspaceMembership(
            user_id=principal.user_id, workspace_id=workspace.id, role=invitation.role
        )
    )
    session.commit()
    return _workspace_item(workspace, invitation.role)


@router.patch("/workspaces/{workspace_id}/members/{user_id}")
def change_member_role(
    workspace_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Path(gt=0)],
    body: RoleUpdate,
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
) -> dict:
    if principal.user_id is None:
        raise HTTPException(404, detail="workspace not found")
    actor, _ = _require_manager(session, workspace_id, principal.user_id)
    if user_id == principal.user_id:
        raise HTTPException(403, detail="cannot change your own role")
    target = session.get(WorkspaceMembership, (user_id, workspace_id))
    if target is None:
        raise HTTPException(404, detail="member not found")
    if actor.role == "admin" and (
        target.role not in {"editor", "viewer"} or body.role not in {"editor", "viewer"}
    ):
        raise HTTPException(403, detail="admins may manage editors and viewers only")
    target.role = body.role
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="workspace must retain at least one owner") from exc
    return _member_out(target)


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_member(
    workspace_id: Annotated[int, Path(gt=0)],
    user_id: Annotated[int, Path(gt=0)],
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
) -> None:
    if principal.user_id is None:
        raise HTTPException(404, detail="workspace not found")
    actor, _ = _require_manager(session, workspace_id, principal.user_id)
    if user_id == principal.user_id:
        raise HTTPException(403, detail="use leave workspace to remove yourself")
    target = session.get(WorkspaceMembership, (user_id, workspace_id))
    if target is None:
        raise HTTPException(404, detail="member not found")
    if actor.role == "admin" and target.role not in {"editor", "viewer"}:
        raise HTTPException(403, detail="admins may remove editors and viewers only")
    session.delete(target)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="workspace must retain at least one owner") from exc


@router.post("/workspaces/{workspace_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_workspace(
    workspace_id: Annotated[int, Path(gt=0)],
    session: SessionDep,
    principal: AuthenticatedPrincipalDep,
) -> None:
    if principal.user_id is None:
        raise HTTPException(404, detail="workspace not found")
    membership = _membership(session, workspace_id, principal.user_id)
    session.delete(membership)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, detail="workspace must retain at least one owner") from exc
