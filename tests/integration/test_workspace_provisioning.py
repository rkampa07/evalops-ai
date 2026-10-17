from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from evalops.api.deps import get_authenticated_principal
from evalops.auth import SESSION_COOKIE, AuthenticatedPrincipal, _csrf_token_for, _digest
from evalops.config import Settings
from evalops.main import create_app
from evalops.models import AuthSession, User, Workspace, WorkspaceMembership


def _identity(
    client, subject: str, role: str | None = None, workspace_id: int | None = None
) -> tuple[int, int | None]:
    with client.app.state.session_factory() as session:
        user = (
            session.query(User)
            .filter_by(issuer="https://issuer.example", subject=subject)
            .one_or_none()
        )
        if user is None:
            user = User(
                issuer="https://issuer.example", subject=subject, email=f"{subject}@example.test"
            )
            session.add(user)
            session.flush()
        user_id = user.id
        if (
            role
            and workspace_id
            and session.get(WorkspaceMembership, (user_id, workspace_id)) is None
        ):
            session.add(WorkspaceMembership(user_id=user_id, workspace_id=workspace_id, role=role))
        session.commit()

    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(
        user_id=user_id, issuer="https://issuer.example", subject=subject, mode="oidc"
    )
    return user_id, workspace_id


def test_workspace_creation_and_user_bound_invitation_flow(client):
    owner_id, _ = _identity(client, "owner")
    created = client.post("/workspaces", json={"name": "Review team"})
    assert created.status_code == 201, created.text
    workspace = created.json()
    assert workspace["role"] == "owner" and not workspace["is_legacy"]
    workspace_id = workspace["id"]

    target_id, _ = _identity(client, "target")
    _identity(client, "owner")
    invite = client.post(
        f"/workspaces/{workspace_id}/invitations",
        json={"target_user_id": target_id, "role": "editor"},
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["token"]
    assert invite.json()["role"] == "editor"

    # A different authenticated identity cannot redeem another user's token.
    _identity(client, "intruder")
    denied = client.post("/workspaces/invitations/accept", json={"token": token})
    assert denied.status_code == 404

    _identity(client, "target")
    accepted = client.post("/workspaces/invitations/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["id"] == workspace_id and accepted.json()["role"] == "editor"
    assert client.post("/workspaces/invitations/accept", json={"token": token}).status_code == 404

    members = client.get(f"/workspaces/{workspace_id}/members")
    assert members.status_code == 200
    assert {item["user_id"] for item in members.json()["items"]} == {owner_id, target_id}


def test_admin_restrictions_cross_workspace_and_final_owner_protection(client):
    owner_id, _ = _identity(client, "owner-two")
    workspace = client.post("/workspaces", json={"name": "Owner workspace"}).json()
    ws_id = workspace["id"]
    with client.app.state.session_factory() as session:
        admin = User(issuer="https://issuer.example", subject="admin")
        editor = User(issuer="https://issuer.example", subject="editor")
        other_owner = User(issuer="https://issuer.example", subject="other-owner")
        session.add_all([admin, editor, other_owner])
        session.flush()
        session.add_all(
            [
                WorkspaceMembership(user_id=admin.id, workspace_id=ws_id, role="admin"),
                WorkspaceMembership(user_id=editor.id, workspace_id=ws_id, role="editor"),
                WorkspaceMembership(user_id=other_owner.id, workspace_id=ws_id, role="owner"),
            ]
        )
        admin_id, editor_id, other_owner_id = admin.id, editor.id, other_owner.id
        foreign = Workspace(name="Foreign", slug="foreign-workspace")
        session.add(foreign)
        session.flush()
        session.add(WorkspaceMembership(user_id=editor_id, workspace_id=foreign.id, role="owner"))
        foreign_id = foreign.id
        session.commit()

    _identity(client, "admin")
    assert (
        client.patch(f"/workspaces/{ws_id}/members/{editor_id}", json={"role": "owner"}).status_code
        == 403
    )
    assert (
        client.patch(f"/workspaces/{ws_id}/members/{admin_id}", json={"role": "owner"}).status_code
        == 403
    )
    assert client.get(f"/workspaces/{foreign_id}/members").status_code == 404

    _identity(client, "owner-two")
    assert (
        client.patch(f"/workspaces/{ws_id}/members/{owner_id}", json={"role": "viewer"}).status_code
        == 403
    )
    assert (
        client.patch(
            f"/workspaces/{ws_id}/members/{other_owner_id}", json={"role": "editor"}
        ).status_code
        == 200
    )
    assert client.post(f"/workspaces/{ws_id}/leave").status_code == 409


def test_development_identity_sees_only_reserved_legacy_workspace(client):
    payload = client.get("/workspaces")
    assert payload.status_code == 200
    assert len(payload.json()["items"]) == 1
    assert payload.json()["items"][0]["is_legacy"] is True
    assert client.post("/workspaces", json={"name": "OIDC only"}).status_code == 403


def test_selected_workspace_is_session_persisted_and_revalidated(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'workspace-session.db'}",
            auto_migrate=True,
            auth_mode="oidc",
            environment="development",
            auth_cookie_secure=False,
            oidc_issuer="https://issuer.example",
            oidc_client_id="client",
            oidc_redirect_uri="https://app.example/auth/callback",
            allowed_hosts=["testserver", "localhost"],
        )
    )
    raw = "workspace-session-token-for-focused-test"
    csrf = _csrf_token_for(raw)
    with TestClient(app, client=("127.0.0.1", 52000), raise_server_exceptions=False) as browser:
        with app.state.session_factory() as session:
            user = User(issuer="https://issuer.example", subject="workspace-user")
            first = Workspace(name="First", slug="first")
            second = Workspace(name="Second", slug="second")
            session.add_all([user, first, second])
            session.flush()
            session.add_all(
                [
                    WorkspaceMembership(user_id=user.id, workspace_id=first.id, role="owner"),
                    WorkspaceMembership(user_id=user.id, workspace_id=second.id, role="editor"),
                    AuthSession(
                        token_hash=_digest(raw),
                        user_id=user.id,
                        csrf_token_hash=_digest(csrf),
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    ),
                ]
            )
            second_id = second.id
            session.commit()
        browser.cookies.set(SESSION_COOKIE, raw)
        initial = browser.get("/auth/session").json()
        assert initial["active_workspace_id"] == 2
        assert [item["id"] for item in initial["workspaces"]] == [2, 3]
        selected = browser.put(
            "/auth/session/workspace",
            json={"workspace_id": second_id},
            headers={"X-CSRF-Token": csrf},
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["active_workspace_id"] == second_id
        assert browser.get("/auth/session").json()["active_workspace_id"] == second_id
        denied = browser.put(
            "/auth/session/workspace",
            json={"workspace_id": 1},
            headers={"X-CSRF-Token": csrf},
        )
        assert denied.status_code == 404
        with app.state.session_factory() as session:
            record = session.get(AuthSession, _digest(raw))
            assert record.active_workspace_id == second_id
            assert (
                session.scalar(
                    select(WorkspaceMembership.role).where(
                        WorkspaceMembership.workspace_id == second_id
                    )
                )
                == "editor"
            )
    app.state.engine.dispose()
