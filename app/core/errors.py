from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException


class DomainError(Exception):
    status = 400
    code = "BAD_REQUEST"
    message = "Bad request"

    def __init__(
        self,
        code: str | None = None,
        message: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        self.code = code or self.code
        self.message = message or self.message
        self.details = details or []
        self.headers = headers or {}


class BadRequestError(DomainError):
    status = 400


class UnauthorizedError(DomainError):
    status = 401
    code = "INVALID_TOKEN"
    message = "Invalid token"

    def __init__(
        self,
        code: str | None = None,
        message: str | None = None,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        # RFC 9110 §15.5.2: a 401 must carry a challenge (RFC 6750 Bearer scheme).
        super().__init__(code, message, details, {"WWW-Authenticate": "Bearer", **(headers or {})})


class ForbiddenError(DomainError):
    status = 403
    code = "FORBIDDEN"
    message = "Forbidden"


class NotFoundError(DomainError):
    status = 404
    code = "NOT_FOUND"
    message = "Not found"


class ConflictError(DomainError):
    status = 409
    code = "CONFLICT"
    message = "Conflict"


class UnprocessableError(DomainError):
    status = 422
    code = "VALIDATION_ERROR"
    message = "Validation failed"


class RateLimitedError(DomainError):
    status = 429
    code = "RATE_LIMITED"
    message = "Too many requests"


_CONSTRAINTS: dict[str, tuple[type[DomainError], str]] = {
    "uq_users_email": (ConflictError, "EMAIL_TAKEN"),
    "uq_trainer_profiles_slug": (ConflictError, "SLUG_TAKEN"),
    "uq_coach_one_active_trainer": (ConflictError, "COACH_ALREADY_ASSIGNED"),
    "ex_availability_no_overlap": (UnprocessableError, "OVERLAPPING_WINDOWS"),
}


def constraint_name(exc: IntegrityError) -> str | None:
    """Violated constraint name, for both drivers.

    asyncpg (our driver): SQLAlchemy wraps the error and the name lives on
    ``orig.__cause__.constraint_name``. psycopg: ``orig.diag.constraint_name``.
    """
    orig = exc.orig
    name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if name is None:
        name = getattr(getattr(orig, "__cause__", None), "constraint_name", None)
    return name if isinstance(name, str) else None


def map_integrity_error(exc: IntegrityError) -> DomainError | None:
    hit = _CONSTRAINTS.get(constraint_name(exc) or "")
    if hit is None:
        return None
    error_cls, code = hit
    return error_cls(code, code.replace("_", " ").capitalize())


def _envelope(code: str, message: str, details: list[Any]) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


# Only codes the framework itself can raise and the spec catalog lists; anything else
# falls back to HTTP_{status} (see api-designer-spec.md "Shared error codes").
_HTTP_CODES: dict[int, str] = {
    401: "INVALID_TOKEN",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    429: "RATE_LIMITED",
}


def internal_error() -> JSONResponse:
    return JSONResponse(_envelope("INTERNAL_ERROR", "Internal server error", []), 500)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            _envelope(exc.code, exc.message, exc.details), exc.status, headers=exc.headers
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        mapped = map_integrity_error(exc)
        if mapped is None:
            structlog.get_logger(__name__).error("unmapped_integrity_error", exc_info=exc)
            return internal_error()
        return JSONResponse(_envelope(mapped.code, mapped.message, []), mapped.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(_envelope("VALIDATION_ERROR", "Validation failed", details), 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
        try:
            message = HTTPStatus(exc.status_code).phrase
        except ValueError:
            message = "Error"
        return JSONResponse(
            _envelope(code, message, []),
            exc.status_code,
            headers=dict(exc.headers) if exc.headers else None,
        )
