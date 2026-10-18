from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.middleware import CorrelationIdMiddleware, ImpersonationMiddleware
from app.api.v1.admin.routes import router as admin_router
from app.api.v1.auth.routes import router as auth_router
from app.api.v1.me.routes import router as me_router
from app.api.v1.public.routes import router as public_router
from app.api.v1.trainer.routes import router as trainer_router
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.ratelimit import RateLimiter
from app.core.security import PasswordHasher, TokenIssuer
from app.db.session import create_engine, create_session_factory


def create_app(settings: Settings | None = None, *, clock: Clock | None = None) -> FastAPI:
    """Build the application. Connections are lazy, so this is safe without lifespan events."""
    settings = settings or get_settings()
    clock = clock or SystemClock()
    configure_logging()

    engine = create_engine(settings.database_url)
    rate_limiter = RateLimiter(settings.redis_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await rate_limiter.aclose()
        await engine.dispose()

    app = FastAPI(title="Trainer Platform API", lifespan=lifespan)
    app.state.settings = settings
    app.state.clock = clock
    app.state.session_factory = create_session_factory(engine)
    app.state.rate_limiter = rate_limiter
    app.state.hasher = PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_kib=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
    )
    app.state.token_issuer = TokenIssuer(
        secret=settings.jwt_secret.get_secret_value(),
        access_ttl=settings.access_ttl_seconds,
        clock=clock,
    )

    install_error_handlers(app)
    # Starlette wraps in reverse order: the last middleware added is the outermost.
    app.add_middleware(ImpersonationMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    for router in (auth_router, me_router, trainer_router, public_router, admin_router):
        app.include_router(router, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
