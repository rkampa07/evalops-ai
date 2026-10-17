"""Runtime settings read from environment variables (see .env.example). No secrets required."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_URL = f"sqlite:///{BACKEND_DIR / 'data' / 'evalops.db'}"
DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1"
DEFAULT_CHAT_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
CHAT_BACKENDS = ("off", "fixture", "mlx")


def _csv_without_wildcard(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip() and v.strip() != "*"]


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DB_URL
    cors_origins: list[str] = field(default_factory=lambda: DEFAULT_CORS.split(","))
    auto_migrate: bool = True
    max_body_bytes: int = 10 * 1024 * 1024
    # Host header allow-list (DNS-rebinding protection for a localhost service).
    allowed_hosts: list[str] = field(default_factory=lambda: DEFAULT_ALLOWED_HOSTS.split(","))
    # Live chat is local-only and requires API authentication. "off" is default; "fixture"
    # needs no model; "mlx" loads the local model lazily on the first message.
    chat_backend: str = "off"
    chat_model: str = DEFAULT_CHAT_MODEL
    chat_history_exchanges: int = 3
    chat_history_char_budget: int = 6000
    chat_max_message_chars: int = 2000
    chat_max_tokens: int = 256
    environment: str = "development"
    auth_mode: str = "oidc"
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    auth_frontend_url: str = "http://127.0.0.1:5173"
    auth_cookie_secure: bool = True
    auth_session_ttl: timedelta = timedelta(hours=8)
    max_datasets_per_workspace: int = 20
    max_test_cases_per_dataset: int = 10_000
    max_passages_per_workspace: int = 25_000
    max_queued_jobs_per_workspace: int = 20
    max_active_jobs_per_workspace: int = 1
    max_queued_citation_assessments_per_workspace: int = 10
    api_read_requests_per_minute: int = 300
    api_write_requests_per_minute: int = 60
    api_job_submissions_per_minute: int = 5

    def __post_init__(self) -> None:
        if self.chat_backend not in CHAT_BACKENDS:
            raise ValueError(f"EVALOPS_CHAT_BACKEND must be one of {CHAT_BACKENDS}")
        if self.auth_mode not in {"oidc", "development"}:
            raise ValueError("EVALOPS_AUTH_MODE must be 'oidc' or 'development'")
        if self.auth_mode == "development" and self.environment != "development":
            raise ValueError("development identity is permitted only when EVALOPS_ENV=development")
        if self.auth_session_ttl.total_seconds() < 60:
            raise ValueError("auth session lifetime must be at least 60 seconds")
        for name in (
            "max_datasets_per_workspace",
            "max_test_cases_per_dataset",
            "max_passages_per_workspace",
            "max_queued_jobs_per_workspace",
            "max_active_jobs_per_workspace",
            "max_queued_citation_assessments_per_workspace",
            "api_read_requests_per_minute",
            "api_write_requests_per_minute",
            "api_job_submissions_per_minute",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if "*" in self.allowed_hosts:
            raise ValueError("allowed hosts must be explicit; wildcard hosts are forbidden")
        for origin in self.cors_origins:
            parsed_origin = urlsplit(origin)
            if (
                origin == "*"
                or parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
                or parsed_origin.username
                or parsed_origin.password
            ):
                raise ValueError("CORS origins must be explicit scheme-and-host origins")
        if self.environment != "development" and not self.auth_cookie_secure:
            raise ValueError("authentication cookies must be Secure outside development")
        frontend = urlsplit(self.auth_frontend_url)
        if (
            not frontend.hostname
            or frontend.scheme not in {"http", "https"}
            or frontend.username
            or frontend.password
            or frontend.query
            or frontend.fragment
        ):
            raise ValueError("authentication frontend URL must be an absolute HTTP(S) URL")
        if self.environment != "development" and frontend.scheme != "https":
            raise ValueError("authentication frontend URL must use HTTPS outside development")
        if self.oidc_redirect_uri:
            redirect = urlsplit(self.oidc_redirect_uri)
            local_http = self.environment == "development" and redirect.hostname in {
                "localhost",
                "127.0.0.1",
                "::1",
            }
            if redirect.scheme != "https" and not (local_http and redirect.scheme == "http"):
                raise ValueError("OIDC redirect URI must use HTTPS except on local development")

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.environ.get("EVALOPS_CORS_ORIGINS", DEFAULT_CORS)
        return cls(
            database_url=os.environ.get("EVALOPS_DATABASE_URL", DEFAULT_DB_URL),
            cors_origins=_csv_without_wildcard(origins),
            auto_migrate=_bool(os.environ.get("EVALOPS_AUTO_MIGRATE", "true")),
            max_body_bytes=int(os.environ.get("EVALOPS_MAX_BODY_BYTES", 10 * 1024 * 1024)),
            allowed_hosts=_csv_without_wildcard(
                os.environ.get("EVALOPS_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOSTS)
            ),
            chat_backend=os.environ.get("EVALOPS_CHAT_BACKEND", "off").strip().lower(),
            chat_model=os.environ.get("EVALOPS_CHAT_MODEL", DEFAULT_CHAT_MODEL),
            environment=os.environ.get("EVALOPS_ENV", "development").strip().lower(),
            auth_mode=os.environ.get("EVALOPS_AUTH_MODE", "oidc").strip().lower(),
            oidc_issuer=os.environ.get("EVALOPS_OIDC_ISSUER"),
            oidc_client_id=os.environ.get("EVALOPS_OIDC_CLIENT_ID"),
            oidc_client_secret=os.environ.get("EVALOPS_OIDC_CLIENT_SECRET"),
            oidc_redirect_uri=os.environ.get("EVALOPS_OIDC_REDIRECT_URI"),
            auth_frontend_url=os.environ.get("EVALOPS_AUTH_FRONTEND_URL", "http://127.0.0.1:5173"),
            auth_cookie_secure=_bool(os.environ.get("EVALOPS_AUTH_COOKIE_SECURE", "true")),
            auth_session_ttl=timedelta(
                seconds=int(os.environ.get("EVALOPS_AUTH_SESSION_TTL_SECONDS", "28800"))
            ),
            max_datasets_per_workspace=int(
                os.environ.get("EVALOPS_MAX_DATASETS_PER_WORKSPACE", "20")
            ),
            max_test_cases_per_dataset=int(
                os.environ.get("EVALOPS_MAX_TEST_CASES_PER_DATASET", "10000")
            ),
            max_passages_per_workspace=int(
                os.environ.get("EVALOPS_MAX_PASSAGES_PER_WORKSPACE", "25000")
            ),
            max_queued_jobs_per_workspace=int(
                os.environ.get("EVALOPS_MAX_QUEUED_JOBS_PER_WORKSPACE", "20")
            ),
            max_active_jobs_per_workspace=int(
                os.environ.get("EVALOPS_MAX_ACTIVE_JOBS_PER_WORKSPACE", "1")
            ),
            max_queued_citation_assessments_per_workspace=int(
                os.environ.get("EVALOPS_MAX_QUEUED_CITATION_ASSESSMENTS_PER_WORKSPACE", "10")
            ),
            api_read_requests_per_minute=int(os.environ.get("EVALOPS_API_READ_RPM", "300")),
            api_write_requests_per_minute=int(os.environ.get("EVALOPS_API_WRITE_RPM", "60")),
            api_job_submissions_per_minute=int(
                os.environ.get("EVALOPS_API_JOB_SUBMISSIONS_RPM", "5")
            ),
        )
