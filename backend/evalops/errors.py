"""Domain errors and safe HTTP error handlers.

Clients get a consistent JSON envelope: {"error": {"code", "message", "details"?}}.
Unexpected exceptions are logged server-side; tracebacks and SQL never reach the client.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("evalops")


class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"


class UnprocessableError(DomainError):
    status_code = 422
    code = "unprocessable"


def _envelope(status: int, code: str, message: str, details=None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError):
        return _envelope(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Omit the echoed "input" to avoid reflecting large or sensitive payloads back.
        details = [
            {"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "")), "type": e.get("type")}
            for e in exc.errors()
        ]
        first = details[0] if details else {"loc": [], "msg": "invalid request"}
        loc = ".".join(str(p) for p in first["loc"] if p != "body")
        message = f"{loc}: {first['msg']}" if loc else first["msg"]
        return _envelope(422, "validation_error", message, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _envelope(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _envelope(500, "internal_error", "an internal error occurred")
