"""OIDC login, opaque sessions, CSRF checks, and local-only development identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException
from fastapi import Request as FastAPIRequest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    AuthSession,
    OIDCLoginTransaction,
    User,
    Workspace,
    WorkspaceMembership,
    utcnow,
)

SESSION_COOKIE = "evalops_session"
OIDC_STATE_COOKIE = "evalops_oidc_state"
OIDC_TRANSACTION_TTL = timedelta(minutes=10)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
OIDC_ALGORITHMS = ["RS256", "ES256"]


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: int | None
    issuer: str
    subject: str
    mode: str


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _csrf_token_for(session_token: str) -> str:
    return hmac.new(session_token.encode("utf-8"), b"evalops-csrf-v1", hashlib.sha256).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _loopback_request(request: FastAPIRequest) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


def _development_origin_allowed(request: FastAPIRequest, settings: Settings) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return origin in settings.cors_origins


def _session_for_request(request: FastAPIRequest, session: Session) -> AuthSession | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    record = session.get(AuthSession, _digest(raw))
    now = utcnow()
    if record is None or record.revoked_at is not None or _utc(record.expires_at) <= now:
        return None
    return record


def _principal_from_session(record: AuthSession) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id=record.user_id,
        issuer=record.user.issuer,
        subject=record.user.subject,
        mode="oidc",
    )


def optional_principal(
    request: FastAPIRequest, session: Session, settings: Settings
) -> tuple[AuthenticatedPrincipal | None, AuthSession | None]:
    if settings.auth_mode == "development":
        if settings.environment != "development" or not _loopback_request(request):
            return None, None
        principal = AuthenticatedPrincipal(
            user_id=None, issuer="evalops-local-development", subject="local", mode="development"
        )
        return principal, None
    record = _session_for_request(request, session)
    return (_principal_from_session(record), record) if record else (None, None)


def require_principal(
    request: FastAPIRequest, session: Session, settings: Settings
) -> tuple[AuthenticatedPrincipal, AuthSession | None]:
    principal, record = optional_principal(request, session, settings)
    if principal is None:
        if settings.auth_mode == "oidc" and not _oidc_configured(settings):
            raise HTTPException(503, detail="authentication is not configured")
        raise HTTPException(401, detail="authentication required")
    if request.method not in SAFE_METHODS:
        _check_csrf(request, session, settings, record)
    return principal, record


def _check_csrf(
    request: FastAPIRequest,
    session: Session,
    settings: Settings,
    record: AuthSession | None,
) -> None:
    origin = request.headers.get("origin")
    if settings.auth_mode == "development":
        if not _loopback_request(request) or not _development_origin_allowed(request, settings):
            raise HTTPException(403, detail="request origin rejected")
        return
    token = request.headers.get("x-csrf-token", "")
    raw_session = request.cookies.get(SESSION_COOKIE, "")
    expected = _csrf_token_for(raw_session) if raw_session else ""
    if (
        record is None
        or not token
        or not hmac.compare_digest(token, expected)
        or not hmac.compare_digest(record.csrf_token_hash, _digest(expected))
    ):
        raise HTTPException(403, detail="CSRF validation failed")
    if origin is not None and origin not in settings.cors_origins:
        raise HTTPException(403, detail="request origin rejected")


def _oidc_configured(settings: Settings) -> bool:
    return all([settings.oidc_issuer, settings.oidc_client_id, settings.oidc_redirect_uri])


def _http_json(url: str, *, data: dict[str, str] | None = None) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise HTTPException(502, detail="identity provider endpoint must use HTTPS")
    body = urlencode(data).encode() if data is not None else None
    headers = {"Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(
        url,
        data=body,
        headers=headers,
        method="POST" if body else "GET",
    )
    try:
        with urlopen(req, timeout=8) as response:
            if not response.geturl().startswith("https://"):
                raise HTTPException(502, detail="identity provider endpoint must use HTTPS")
            value = json.loads(response.read(1_000_001))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(502, detail="identity provider request failed") from exc
    if not isinstance(value, dict):
        raise HTTPException(502, detail="identity provider returned invalid metadata")
    return value


def _discovery(settings: Settings) -> dict[str, Any]:
    if not _oidc_configured(settings):
        raise HTTPException(503, detail="OIDC provider is not configured")
    issuer = settings.oidc_issuer
    if not issuer.startswith("https://"):
        raise HTTPException(503, detail="OIDC issuer must use HTTPS")
    metadata = _http_json(f"{issuer}/.well-known/openid-configuration")
    if metadata.get("issuer") != issuer:
        raise HTTPException(503, detail="OIDC issuer metadata does not match configuration")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(metadata.get(field), str) or not metadata[field].startswith("https://"):
            raise HTTPException(503, detail="OIDC provider metadata is incomplete or insecure")
    return metadata


def begin_login(session: Session, settings: Settings) -> tuple[str, str]:
    metadata = _discovery(settings)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    now = utcnow()
    session.add(
        OIDCLoginTransaction(
            state_hash=_digest(state),
            nonce=nonce,
            code_verifier=verifier,
            created_at=now,
            expires_at=now + OIDC_TRANSACTION_TTL,
        )
    )
    session.commit()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{metadata['authorization_endpoint']}?{query}", state


def _consume_transaction(
    state: str, state_cookie: str | None, session: Session
) -> OIDCLoginTransaction:
    if not state_cookie or not hmac.compare_digest(state, state_cookie):
        raise HTTPException(400, detail="OIDC state validation failed")
    key = _digest(state)
    transaction = session.get(OIDCLoginTransaction, key)
    if transaction is None or _utc(transaction.expires_at) <= utcnow():
        raise HTTPException(400, detail="OIDC login transaction expired or was already used")
    values = OIDCLoginTransaction(
        state_hash=transaction.state_hash,
        nonce=transaction.nonce,
        code_verifier=transaction.code_verifier,
        created_at=transaction.created_at,
        expires_at=transaction.expires_at,
    )
    session.delete(transaction)
    session.commit()
    return values


def _verify_id_token(token: str, metadata: dict[str, Any], settings: Settings) -> dict[str, Any]:
    try:
        import jwt

        key = jwt.PyJWKClient(metadata["jwks_uri"], timeout=5).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=OIDC_ALGORITHMS,
            audience=settings.oidc_client_id,
            issuer=settings.oidc_issuer.rstrip("/"),
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
            leeway=30,
        )
    except ImportError as exc:
        raise HTTPException(503, detail="OIDC verification dependency is unavailable") from exc
    except Exception as exc:
        # Do not echo token fragments or key details from the JWT library.
        raise HTTPException(401, detail="OIDC identity token validation failed") from exc
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise HTTPException(401, detail="OIDC subject is missing")
    audiences = claims.get("aud")
    if (
        isinstance(audiences, list) and len(audiences) > 1 or claims.get("azp") is not None
    ) and claims.get("azp") != settings.oidc_client_id:
        raise HTTPException(401, detail="OIDC authorized party validation failed")
    return claims


def finish_login(
    *,
    code: str,
    state: str,
    state_cookie: str | None,
    request: FastAPIRequest,
    session: Session,
    settings: Settings,
) -> str:
    if not code or len(code) > 4096:
        raise HTTPException(400, detail="OIDC authorization code is invalid")
    transaction = _consume_transaction(state, state_cookie, session)
    metadata = _discovery(settings)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri or "",
        "client_id": settings.oidc_client_id or "",
        "code_verifier": transaction.code_verifier,
    }
    if settings.oidc_client_secret:
        form["client_secret"] = settings.oidc_client_secret
    tokens = _http_json(metadata["token_endpoint"], data=form)
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise HTTPException(401, detail="OIDC response did not include an identity token")
    claims = _verify_id_token(id_token, metadata, settings)
    if not hmac.compare_digest(str(claims.get("nonce", "")), transaction.nonce):
        raise HTTPException(401, detail="OIDC nonce validation failed")

    issuer = settings.oidc_issuer or ""
    subject = claims["sub"]
    user = session.scalar(select(User).where(User.issuer == issuer, User.subject == subject))
    if user is None:
        user = User(
            issuer=issuer,
            subject=subject,
            email=_bounded_claim(claims.get("email"), 320),
            display_name=_bounded_claim(claims.get("name"), 200),
        )
        session.add(user)
        session.flush()
    else:
        user.email = _bounded_claim(claims.get("email"), 320)
        user.display_name = _bounded_claim(claims.get("name"), 200)

    existing = request.cookies.get(SESSION_COOKIE)
    if existing:
        session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == _digest(existing), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
    raw_session = secrets.token_urlsafe(32)
    raw_csrf = _csrf_token_for(raw_session)
    now = utcnow()
    session.add(
        AuthSession(
            token_hash=_digest(raw_session),
            user_id=user.id,
            csrf_token_hash=_digest(raw_csrf),
            created_at=now,
            last_seen_at=now,
            expires_at=now + settings.auth_session_ttl,
        )
    )
    session.commit()
    request.state.new_session_token = raw_session
    request.state.authenticated_user = user
    return settings.auth_frontend_url


def _bounded_claim(value: Any, max_len: int) -> str | None:
    return value[:max_len] if isinstance(value, str) and value else None


def cookie_options(settings: Settings) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": settings.auth_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }


def issue_login_cookies(response, request: FastAPIRequest, settings: Settings) -> None:
    token = getattr(request.state, "new_session_token", None)
    if token:
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(settings.auth_session_ttl.total_seconds()),
            **cookie_options(settings),
        )
    response.delete_cookie(
        OIDC_STATE_COOKIE,
        path="/auth",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def session_payload(
    request: FastAPIRequest, session: Session, settings: Settings
) -> dict[str, Any]:
    principal, record = optional_principal(request, session, settings)
    if principal is None:
        return {"authenticated": False, "mode": settings.auth_mode}
    raw_session = request.cookies.get(SESSION_COOKIE, "")
    csrf = _csrf_token_for(raw_session) if record and raw_session else None
    if principal.mode == "development":
        legacy = session.scalar(
            select(Workspace).where(Workspace.id == 1, Workspace.is_legacy.is_(True))
        )
        workspaces = (
            [{"id": legacy.id, "name": legacy.name, "role": "owner", "is_legacy": True}]
            if legacy
            else []
        )
        active_workspace_id = legacy.id if legacy else None
    else:
        memberships = list(
            session.execute(
                select(WorkspaceMembership, Workspace)
                .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
                .where(
                    WorkspaceMembership.user_id == principal.user_id,
                    Workspace.is_legacy.is_(False),
                )
                .order_by(Workspace.id)
            )
        )
        workspaces = [
            {
                "id": membership.workspace_id,
                "name": workspace.name,
                "role": membership.role,
                "is_legacy": False,
            }
            for membership, workspace in memberships
        ]
        valid_ids = {item["id"] for item in workspaces}
        active_workspace_id = (
            record.active_workspace_id if record.active_workspace_id in valid_ids else None
        )
        if active_workspace_id is None and workspaces:
            active_workspace_id = workspaces[0]["id"]
            record.active_workspace_id = active_workspace_id
            session.commit()
    return {
        "authenticated": True,
        "mode": principal.mode,
        "user": (
            {
                "id": record.user_id,
                "issuer": principal.issuer,
                "subject": principal.subject,
            }
            if record
            else None
        ),
        "csrf_token": csrf,
        "workspaces": workspaces,
        "active_workspace_id": active_workspace_id,
    }


def revoke_session(request: FastAPIRequest, session: Session) -> None:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        session.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == _digest(raw), AuthSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        session.commit()
