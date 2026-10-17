# Authentication and workspace RBAC

EvalOps uses OIDC Authorization Code with PKCE, opaque server-side browser sessions, CSRF
validation, and workspace-scoped role-based access control (`owner`, `admin`, `editor`, `viewer`).

## Local development identity

The safe default is `EVALOPS_AUTH_MODE=oidc`. Without a configured provider, protected API calls
return an authentication-configuration error; they do not silently become anonymous. To use a
clearly identified local development principal, explicitly set:

```sh
EVALOPS_ENV=development EVALOPS_AUTH_MODE=development make backend
```

The Makefile binds Uvicorn to `127.0.0.1`. Development identity requests are also rejected unless
the direct client connection is loopback. This mode is rejected when `EVALOPS_ENV` is not
`development`. Do not use it behind a reverse proxy or on a network-facing bind. Development
identity is confined exclusively to the reserved Default Workspace (`id = 1`, `owner` role) and
never creates an OIDC user record.

## OIDC provider configuration

Configure these environment variables in the API process. Never commit client secrets:

| Variable | Purpose |
|---|---|
| `EVALOPS_ENV` | Set to `production` in deployed environments. |
| `EVALOPS_AUTH_MODE` | `oidc` (default) or explicit local-only `development`. |
| `EVALOPS_OIDC_ISSUER` | Exact HTTPS issuer identifier from provider metadata. |
| `EVALOPS_OIDC_CLIENT_ID` | OIDC client identifier. |
| `EVALOPS_OIDC_CLIENT_SECRET` | Optional for a public PKCE client; required if the provider requires a confidential client. |
| `EVALOPS_OIDC_REDIRECT_URI` | Exact registered callback, `/auth/callback`. |
| `EVALOPS_AUTH_FRONTEND_URL` | Fixed frontend URL used after successful login; it is not request-controlled. |
| `EVALOPS_AUTH_COOKIE_SECURE` | Keep `true` for HTTPS deployments. Set `false` only for explicitly local HTTP development. |
| `EVALOPS_AUTH_SESSION_TTL_SECONDS` | Server-side session lifetime; default is eight hours. |
| `EVALOPS_CORS_ORIGINS` | Exact browser origins. Use same-origin deployment in production where practical. |

For local browser login, use the same loopback hostname in the Vite URL, API URL, callback URL,
and frontend return URL (the defaults use `127.0.0.1`). Different loopback hostnames can make a
SameSite=Lax cookie unavailable to browser API requests.

The provider must publish HTTPS discovery metadata and JWKS. Login stores state, nonce, and the
PKCE verifier in a short-lived, one-use database transaction. The callback checks the browser
state cookie, exchanges the code, verifies the ID-token signature and allowed algorithm, exact
issuer, audience, authorized party when applicable, expiry, issue time, subject, and nonce. Local
users are keyed by `(issuer, subject)`; email is profile data only.

The browser receives a random session cookie marked HttpOnly, SameSite=Lax, and Secure according
to configuration. Only a SHA-256 digest is stored server-side. The session endpoint rotates and
returns a CSRF token for in-memory use; state-changing authenticated API requests must send it in
`X-CSRF-Token`. Logout revokes the server-side session. Session records expire after the configured
lifetime.

## Workspace provisioning and authorization

On first OIDC login, EvalOps automatically provisions a personal workspace (`owner` role) for the
user in the same transaction (migration `0011`), while keeping the reserved legacy Default Workspace
(`id = 1`) unassigned to OIDC principals. Users can also create additional workspaces (`POST /workspaces`)
and manage workspace members (`GET/POST/PATCH/DELETE /workspaces/{id}/members`).

All dataset, evaluation run, comparison, release-gate, evaluation job, citation/claim assessment,
and live playground queries are strictly scoped to the active workspace selected via `X-Workspace-ID`:
- `viewer`: read datasets, runs, comparisons, release reports, job/assessment statuses, and results.
- `editor`: create/update/delete datasets and test cases, submit runs/jobs/assessments, cancel jobs/assessments, and use the live playground.
- `admin` / `owner`: manage workspace memberships and roles (only `owner` can grant or modify `owner` memberships).

## Database and startup

Migrations `0007`–`0014` add workspace ownership, `auth_sessions`, `oidc_login_transactions`,
workspace-scoped job/assessment idempotency, immutable job workspace bindings, personal workspace
provisioning, persistent rate-limit buckets, and frozen citation/claim assessment tables. Local
startup applies migrations automatically when `EVALOPS_AUTO_MIGRATE=true`, or you can run
`cd backend && PYTHONPATH=. .venv/bin/alembic upgrade head` explicitly before starting the API and
workers.

Health (`GET /health`) and login/session bootstrap routes are public. Dataset, workspace,
evaluation, assessment, and chat API routes require an authenticated principal and enforce
workspace role checks. CORS keeps an explicit origin allow-list and credentials enabled only for
those origins.
