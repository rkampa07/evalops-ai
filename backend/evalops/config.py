"""Runtime settings read from environment variables (see .env.example). No secrets required."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_URL = f"sqlite:///{BACKEND_DIR / 'data' / 'evalops.db'}"
DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"
DEFAULT_ALLOWED_HOSTS = "localhost,127.0.0.1"


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
        )
