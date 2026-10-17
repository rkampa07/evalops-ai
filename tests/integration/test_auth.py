from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from evalops.auth import SESSION_COOKIE, _csrf_token_for, _digest
from evalops.config import Settings
from evalops.db import run_migrations
from evalops.main import create_app
from evalops.models import AuthSession, User, Workspace, WorkspaceMembership


def _settings(db_url: str, **overrides) -> Settings:
    values = {
        "database_url": db_url,
        "auto_migrate": True,
        "allowed_hosts": ["testserver", "localhost", "127.0.0.1"],
        "cors_origins": ["http://localhost:5173"],
        "environment": "development",
        "auth_mode": "oidc",
        "auth_cookie_secure": False,
    }
    values.update(overrides)
    return Settings(**values)


def _client(db_url: str, **overrides):
    app = create_app(_settings(db_url, **overrides))
    return app, TestClient(app, client=("127.0.0.1", 51000), raise_server_exceptions=False)


def _create_session(
    app,
    *,
    expires_at: datetime,
    revoked_at: datetime | None = None,
    member: bool = False,
):
    raw_token = f"raw-session-token-{expires_at.timestamp()}-{revoked_at}"
    csrf = _csrf_token_for(raw_token)
    with app.state.session_factory() as session:
        user = User(issuer="https://issuer.example", subject=raw_token)
        session.add(user)
        session.flush()
        session.add(
            AuthSession(
                token_hash=_digest(raw_token),
                user_id=user.id,
                csrf_token_hash=_digest(csrf),
                expires_at=expires_at,
                revoked_at=revoked_at,
            )
        )
        if member:
            workspace = Workspace(name="member workspace", slug=f"member-{user.id}")
            session.add(workspace)
            session.flush()
            session.add(
                WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="editor")
            )
        session.commit()
    return raw_token, csrf


def test_health_is_public_but_application_api_fails_closed_without_oidc_config(tmp_path):
    app, client = _client(f"sqlite:///{tmp_path / 'auth.db'}")
    with client:
        assert client.get("/health").status_code == 200
        response = client.get("/datasets")
        assert response.status_code == 503
        assert response.json()["error"]["message"] == "authentication is not configured"
    app.state.engine.dispose()


def test_development_identity_is_loopback_only_and_never_grants_legacy_membership(tmp_path):
    url = f"sqlite:///{tmp_path / 'dev.db'}"
    app = create_app(_settings(url, auth_mode="development", auth_cookie_secure=False))
    with TestClient(app, client=("127.0.0.1", 51000)) as local:
        session = local.get("/auth/session").json()
        assert session["authenticated"] is True
        assert session["mode"] == "development"
        assert local.post("/datasets", json={"name": "local-data"}).status_code == 201
    with TestClient(app, client=("198.51.100.10", 51000)) as remote:
        assert remote.get("/datasets").status_code == 401
    with app.state.session_factory() as session:
        assert session.scalar(select(User.id)) is None
        assert session.scalar(select(WorkspaceMembership.user_id)) is None
    app.state.engine.dispose()


def test_development_identity_configuration_is_rejected_outside_development():
    with pytest.raises(ValueError, match="only when EVALOPS_ENV=development"):
        Settings(auth_mode="development", environment="production")
    with pytest.raises(ValueError, match="cookies must be Secure"):
        Settings(environment="production", auth_cookie_secure=False)
    with pytest.raises(ValueError, match="CORS origins must be explicit"):
        Settings(cors_origins=["*"])


def test_opaque_session_expiry_revocation_and_csrf_enforcement(tmp_path):
    app, client = _client(
        f"sqlite:///{tmp_path / 'session.db'}",
        oidc_issuer="https://issuer.example",
        oidc_client_id="client",
        oidc_redirect_uri="https://app.example/auth/callback",
    )
    with client:
        expired, _ = _create_session(app, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        client.cookies.set(SESSION_COOKIE, expired)
        assert client.get("/datasets").status_code == 401

        revoked, _ = _create_session(
            app,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            revoked_at=datetime.now(UTC),
        )
        client.cookies.set(SESSION_COOKIE, revoked)
        assert client.get("/datasets").status_code == 401

        token, csrf = _create_session(
            app, expires_at=datetime.now(UTC) + timedelta(hours=1), member=True
        )
        client.cookies.set(SESSION_COOKIE, token)
        assert client.post("/datasets", json={"name": "csrf-missing"}).status_code == 403
        rejected = client.post(
            "/datasets",
            json={"name": "csrf-origin"},
            headers={"X-CSRF-Token": csrf, "Origin": "https://attacker.example"},
        )
        assert rejected.status_code == 403
        created = client.post(
            "/datasets",
            json={"name": "csrf-valid"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"},
        )
        assert created.status_code == 201, created.text
        dataset_id = created.json()["id"]
        case = client.post(
            f"/datasets/{dataset_id}/test-cases",
            json={"key": "auth-case", "input": "question", "expected_output": "answer"},
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"},
        )
        assert case.status_code == 201, case.text
        run = client.post(
            "/evaluations/runs",
            json={
                "dataset_id": dataset_id,
                "model_name": "auth-test",
                "prompt_version": "v1",
                "responses": [{"case_key": "auth-case", "output": "answer"}],
            },
            headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:5173"},
        )
        assert run.status_code == 201, run.text
        assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/datasets").status_code == 401
    app.state.engine.dispose()


def test_login_uses_state_nonce_and_pkce_and_sets_secure_session_cookie(tmp_path, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    import evalops.auth as auth

    url = f"sqlite:///{tmp_path / 'login.db'}"
    app = create_app(
        _settings(
            url,
            oidc_issuer="https://issuer.example",
            oidc_client_id="evalops-client",
            oidc_redirect_uri="https://app.example/auth/callback",
            auth_frontend_url="https://app.example",
            auth_cookie_secure=True,
        )
    )
    run_migrations(url)
    monkeypatch.setattr(
        auth,
        "_discovery",
        lambda _settings: {
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "jwks_uri": "https://issuer.example/jwks",
        },
    )
    monkeypatch.setattr(
        auth,
        "_http_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPException(502, detail="mock provider unavailable")
        ),
    )
    with TestClient(app, client=("127.0.0.1", 51000)) as client:
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 302
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["nonce"]
        state = query["state"][0]
        assert response.headers["set-cookie"].lower().find("httponly") >= 0
        assert "secure" in response.headers["set-cookie"].lower()
        client.cookies.set("evalops_oidc_state", state)
        assert (
            client.get(
                "/auth/callback",
                params={"code": "fake-code", "state": state},
            ).status_code
            == 502
        )  # callback consumed state, then provider exchange failed
        assert (
            client.get(
                "/auth/callback",
                params={"code": "fake-code", "state": state},
            ).status_code
            == 400
        )
    app.state.engine.dispose()


def test_oidc_state_cookie_mismatch_is_rejected_before_token_exchange(tmp_path, monkeypatch):
    import evalops.auth as auth

    url = f"sqlite:///{tmp_path / 'state.db'}"
    app = create_app(
        _settings(
            url,
            oidc_issuer="https://issuer.example",
            oidc_client_id="client",
            oidc_redirect_uri="https://app.example/auth/callback",
        )
    )
    run_migrations(url)
    monkeypatch.setattr(auth, "_discovery", lambda _settings: pytest.fail("must not fetch"))
    with TestClient(app, client=("127.0.0.1", 51000)) as client:
        client.cookies.set("evalops_oidc_state", "different-state")
        result = client.get(
            "/auth/callback",
            params={"code": "code", "state": "attacker-state"},
        )
        assert result.status_code == 400
        assert "state validation failed" in result.text
    app.state.engine.dispose()


def test_oidc_callback_rejects_missing_state_or_provider_error(tmp_path):
    app, client = _client(
        f"sqlite:///{tmp_path / 'callback-errors.db'}",
        oidc_issuer="https://issuer.example",
        oidc_client_id="client",
        oidc_redirect_uri="https://app.example/auth/callback",
    )
    with client:
        assert client.get("/auth/callback", params={"error": "access_denied"}).status_code == 400
        assert client.get("/auth/callback", params={"code": "code"}).status_code == 400
    app.state.engine.dispose()


@pytest.mark.parametrize(
    "claim_change",
    [
        {"iss": "https://attacker.example"},
        {"aud": "different-client"},
        {"exp": 1},
        {"nonce": "wrong-nonce"},
    ],
    ids=["issuer", "audience", "expiration", "nonce"],
)
def test_oidc_callback_rejects_invalid_identity_claims(tmp_path, monkeypatch, claim_change):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    import evalops.auth as auth

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setattr(
        jwt,
        "PyJWKClient",
        lambda *_args, **_kwargs: type(
            "JwkClient",
            (),
            {
                "get_signing_key_from_jwt": lambda _self, _token: type(
                    "Key", (), {"key": public_key}
                )()
            },
        )(),
    )
    issuer = "https://issuer.example"
    client_id = "evalops-client"
    state = "state-token"
    nonce = "nonce-value"
    claims = {
        "iss": issuer,
        "sub": "subject-1",
        "aud": client_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": nonce,
    } | claim_change
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "key-1"})
    url = f"sqlite:///{tmp_path / 'invalid-claims.db'}"
    app = create_app(
        _settings(
            url,
            oidc_issuer=issuer,
            oidc_client_id=client_id,
            oidc_redirect_uri="https://app.example/auth/callback",
        )
    )
    run_migrations(url)
    monkeypatch.setattr(
        auth,
        "_discovery",
        lambda _settings: {
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "jwks_uri": "https://issuer.example/jwks",
        },
    )
    monkeypatch.setattr(auth, "_http_json", lambda *_args, **_kwargs: {"id_token": token})
    with app.state.session_factory() as session:
        session.add(
            auth.OIDCLoginTransaction(
                state_hash=_digest(state),
                nonce=nonce,
                code_verifier="verifier",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        session.commit()
    with TestClient(app, client=("127.0.0.1", 51000)) as client:
        client.cookies.set("evalops_oidc_state", state)
        response = client.get("/auth/callback", params={"code": "code", "state": state})
        assert response.status_code == 401
        assert "OIDC" in response.json()["error"]["message"]
    with app.state.session_factory() as session:
        assert session.scalar(select(User.id)) is None
    app.state.engine.dispose()


def test_oidc_callback_validates_signature_and_rotates_session_without_legacy_membership(
    tmp_path, monkeypatch
):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    import evalops.auth as auth

    issuer = "https://issuer.example"
    client_id = "evalops-client"
    state = "state-token"
    nonce = "nonce-value"
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    monkeypatch.setattr(
        jwt,
        "PyJWKClient",
        lambda *_args, **_kwargs: type(
            "JwkClient",
            (),
            {
                "get_signing_key_from_jwt": lambda _self, _token: type(
                    "Key", (), {"key": public_key}
                )()
            },
        )(),
    )
    claims = {
        "iss": issuer,
        "sub": "stable-subject",
        "aud": client_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": nonce,
        "email": "person@example.test",
        "name": "Test Person",
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "key-1"})
    url = f"sqlite:///{tmp_path / 'valid-login.db'}"
    app = create_app(
        _settings(
            url,
            oidc_issuer=issuer,
            oidc_client_id=client_id,
            oidc_redirect_uri="https://app.example/auth/callback",
            auth_frontend_url="https://app.example",
        )
    )
    run_migrations(url)
    monkeypatch.setattr(
        auth,
        "_discovery",
        lambda _settings: {
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "jwks_uri": "https://issuer.example/jwks",
        },
    )
    monkeypatch.setattr(auth, "_http_json", lambda *_args, **_kwargs: {"id_token": token})
    with app.state.session_factory() as session:
        user = User(issuer=issuer, subject="stable-subject")
        session.add(user)
        session.flush()
        session.add(
            AuthSession(
                token_hash=_digest("old-session"),
                user_id=user.id,
                csrf_token_hash=_digest("old-csrf"),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.add(
            auth.OIDCLoginTransaction(
                state_hash=_digest(state),
                nonce=nonce,
                code_verifier="verifier",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        session.commit()
    with TestClient(
        app,
        client=("127.0.0.1", 51000),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        client.cookies.set("evalops_session", "old-session")
        client.cookies.set("evalops_oidc_state", state)
        response = client.get("/auth/callback", params={"code": "code", "state": state})
        assert response.status_code == 303
        set_cookie = response.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "samesite=lax" in set_cookie
        assert client.get("/auth/session").json()["authenticated"] is True
        assert client.get("/datasets").status_code == 404
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.subject == "stable-subject"))
        assert user is not None and user.issuer == issuer
        assert session.scalar(select(WorkspaceMembership.user_id)) is None
        old = session.get(AuthSession, _digest("old-session"))
        assert old is not None and old.revoked_at is not None
        sessions = list(session.scalars(select(AuthSession).where(AuthSession.user_id == user.id)))
        assert len(sessions) == 2
    app.state.engine.dispose()


def test_oidc_callback_rejects_invalid_jwt_signature(tmp_path, monkeypatch):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    import evalops.auth as auth

    trusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    untrusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        jwt,
        "PyJWKClient",
        lambda *_args, **_kwargs: type(
            "JwkClient",
            (),
            {
                "get_signing_key_from_jwt": lambda _self, _token: type(
                    "Key", (), {"key": trusted_key.public_key()}
                )()
            },
        )(),
    )
    issuer = "https://issuer.example"
    state = "signature-state"
    nonce = "signature-nonce"
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "subject-1",
            "aud": "client",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "nonce": nonce,
        },
        untrusted_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    app = create_app(
        _settings(
            f"sqlite:///{tmp_path / 'bad-signature.db'}",
            oidc_issuer=issuer,
            oidc_client_id="client",
            oidc_redirect_uri="https://app.example/auth/callback",
        )
    )
    run_migrations(f"sqlite:///{tmp_path / 'bad-signature.db'}")
    monkeypatch.setattr(
        auth,
        "_discovery",
        lambda _settings: {
            "authorization_endpoint": "https://issuer.example/authorize",
            "token_endpoint": "https://issuer.example/token",
            "jwks_uri": "https://issuer.example/jwks",
        },
    )
    monkeypatch.setattr(auth, "_http_json", lambda *_args, **_kwargs: {"id_token": token})
    with app.state.session_factory() as session:
        session.add(
            auth.OIDCLoginTransaction(
                state_hash=_digest(state),
                nonce=nonce,
                code_verifier="verifier",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        session.commit()
    with TestClient(app, client=("127.0.0.1", 51000)) as client:
        client.cookies.set("evalops_oidc_state", state)
        response = client.get("/auth/callback", params={"code": "code", "state": state})
        assert response.status_code == 401
        assert response.json()["error"]["message"] == "OIDC identity token validation failed"
    with app.state.session_factory() as session:
        assert session.scalar(select(User.id)) is None
    app.state.engine.dispose()
