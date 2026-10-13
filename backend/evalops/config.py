"""Runtime settings read from environment variables (see .env.example). No secrets required."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_URL = f"sqlite:///{BACKEND_DIR / 'data' / 'evalops.db'}"
DEFAULT_CORS = "http://localhost:5173,http://127.0.0.1:5173"


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DB_URL
    cors_origins: list[str] = field(default_factory=lambda: DEFAULT_CORS.split(","))
    auto_migrate: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        origins = os.environ.get("EVALOPS_CORS_ORIGINS", DEFAULT_CORS)
        return cls(
            database_url=os.environ.get("EVALOPS_DATABASE_URL", DEFAULT_DB_URL),
            cors_origins=[o.strip() for o in origins.split(",") if o.strip() and o.strip() != "*"],
            auto_migrate=_bool(os.environ.get("EVALOPS_AUTO_MIGRATE", "true")),
        )
