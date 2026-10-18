import re

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.errors import internal_error
from app.core.ids import new_id
from app.db.session import SessionFactory
from app.repositories.audit import AuditRepository
from app.services.audit_service import DbAuditService

CORRELATION_HEADER = "X-Correlation-Id"
IMPERSONATED_BY_HEADER = "X-Impersonated-By"

# A client-supplied id is echoed into logs and a response header: accept only a tame shape.
_VALID_CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

_log = structlog.get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Outermost. Always sets ``X-Correlation-Id`` and is the single 500 path.

    An unhandled exception is logged once (redacted, with the correlation id) and answered
    with the ``INTERNAL_ERROR`` envelope. It is deliberately not re-raised: Starlette's
    ``ServerErrorMiddleware`` and uvicorn would log the traceback a second time, unredacted,
    and the 500 would carry no correlation id.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get(CORRELATION_HEADER, "")
        correlation_id = supplied if _VALID_CORRELATION_ID.fullmatch(supplied) else str(new_id())
        request.state.correlation_id = correlation_id
        with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
            try:
                response = await call_next(request)
            except Exception:
                _log.error("unhandled_exception", exc_info=True)
                response = internal_error()
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


class ImpersonationMiddleware(BaseHTTPMiddleware):
    """Sets ``X-Impersonated-By`` and audits every request that carries ``act`` (NFR-006).

    The principal is published on ``request.state`` by ``current_principal`` once the route
    has authenticated, so requests rejected before that point are neither marked nor audited.
    A request whose handler crashed is audited too, then the exception propagates to the 500
    path. The audit row is written in its own session after the handler has finished, so it is
    fail-visible, not atomic: a failing audit write on a successful request fails that request
    even though its effects are committed.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            try:
                await self._audit(request)
            except Exception:
                _log.error("impersonation_audit_failed", exc_info=True)
            raise
        principal = getattr(request.state, "principal", None)
        if principal is None or principal.act is None:
            return response
        response.headers[IMPERSONATED_BY_HEADER] = str(principal.act)
        await self._audit(request)
        return response

    @staticmethod
    async def _audit(request: Request) -> None:
        principal = getattr(request.state, "principal", None)
        if principal is None or principal.act is None:
            return
        factory: SessionFactory = request.app.state.session_factory
        async with factory() as session:
            await DbAuditService(AuditRepository(session)).record(
                action="impersonated_request",
                actor_id=principal.user_id,
                act=principal.act,
                subject_type="user",
                subject_id=principal.user_id,
                trainer_id=principal.trainer_id,
                method=request.method,
                path=request.url.path,
            )
            await session.commit()
