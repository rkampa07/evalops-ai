"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from . import __version__
from .api import datasets, evaluations, health
from .config import Settings
from .db import make_engine, make_session_factory, run_migrations
from .errors import register_error_handlers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.auto_migrate:
            run_migrations(settings.database_url)
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title="EvalOps AI",
        version=__version__,
        description="Evaluate AI responses, detect regressions, and gate releases.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.session_factory = make_session_factory(app.state.engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        # Reject oversized payloads before parsing; protects memory on small machines.
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > settings.max_body_bytes):
            return JSONResponse(
                status_code=413,
                content={
                    "error": {"code": "payload_too_large", "message": "request body too large"}
                },
            )
        return await call_next(request)

    # Added last so it runs first: reject unexpected Host headers (DNS rebinding) before
    # any other processing. A "*" entry is never accepted from configuration.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts or ["localhost"])
    app.include_router(health.router)
    app.include_router(datasets.router)
    app.include_router(evaluations.router)
    return app


app = create_app()
