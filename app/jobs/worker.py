"""ARQ worker: settings, retry/backoff, dead-letter log, cron registry, and the enqueue adapter.

Run with ``arq app.jobs.worker.WorkerSettings``.

ARQ retries only when a job raises ``arq.Retry``, so each job is wrapped: a failure before the
last attempt becomes ``Retry(defer=2 ** attempt)`` seconds; a failure on the last attempt writes
a structured dead-letter log (job name, attempt count, error type; never kwargs, which hold raw
tokens) and propagates so ARQ records the job as failed.
"""

import functools
import os
from collections.abc import Awaitable, Callable
from typing import cast

import structlog
from arq import cron
from arq.connections import ArqRedis, RedisSettings
from arq.cron import CronJob
from arq.typing import WorkerCoroutine
from arq.worker import Function, Retry, func

from app.core.clock import SystemClock
from app.core.config import get_settings
from app.core.email import LoggingEmailSender
from app.core.logging import configure_logging
from app.core.once import RedisOnceStore
from app.db.session import create_engine, create_session_factory
from app.jobs import identity_jobs
from app.jobs.identity_jobs import Ctx
from app.services.maintenance_service import MaintenanceService
from app.services.notification_service import NotificationService

MAX_TRIES = 5

Job = Callable[..., Awaitable[None]]

_EVENT_JOBS: tuple[Job, ...] = (
    identity_jobs.send_trainer_invite,
    identity_jobs.send_welcome_email,
    identity_jobs.send_verification_email,
    identity_jobs.send_password_reset,
    identity_jobs.send_email_change,
    identity_jobs.notify_parent_approval_request,
    identity_jobs.notify_child_approval_denied,
    identity_jobs.notify_parent_share_link_blocked,
)


def with_retries(job: Job) -> Job:
    """Exponential backoff (2, 4, 8, 16 s) for ``MAX_TRIES`` attempts, then dead-letter log."""

    @functools.wraps(job)
    async def wrapper(ctx: Ctx, **kwargs: str) -> None:
        try:
            await job(ctx, **kwargs)
        except Exception as exc:
            attempt = int(ctx["job_try"])
            if attempt >= MAX_TRIES:
                structlog.get_logger(__name__).error(
                    "job.dead_letter",
                    job=job.__name__,
                    job_id=ctx.get("job_id"),
                    attempts=attempt,
                    error_type=type(exc).__name__,
                )
                raise
            raise Retry(defer=2**attempt) from exc

    return wrapper


def _coroutine(job: Job) -> WorkerCoroutine:
    """ARQ types a job as ``(ctx) -> Awaitable``; ours also take keyword arguments."""
    return cast("WorkerCoroutine", job)


def _registered() -> list[Function]:
    return [
        func(_coroutine(with_retries(job)), name=job.__name__, max_tries=MAX_TRIES)
        for job in _EVENT_JOBS
    ]


def _every_five_minutes(job: Job) -> CronJob:
    return cron(
        _coroutine(job), name=job.__name__, minute=set(range(0, 60, 5)), run_at_startup=False
    )


async def startup(ctx: Ctx) -> None:
    configure_logging()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    clock = SystemClock()
    redis: ArqRedis = ctx["redis"]
    ctx["engine"] = engine
    ctx["notifications"] = NotificationService(
        session_factory=session_factory,
        clock=clock,
        settings=settings,
        # No SMTP/provider adapter exists yet: this logs the template name only.
        email=LoggingEmailSender(),
        once=RedisOnceStore(redis),
    )
    ctx["maintenance"] = MaintenanceService(session_factory=session_factory, clock=clock)


async def shutdown(ctx: Ctx) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    """ARQ reads plain class attributes, so the registries cannot be ``ClassVar``-annotated."""

    functions = _registered()
    cron_jobs = [  # noqa: RUF012
        _every_five_minutes(identity_jobs.expire_purchase_approvals),
        _every_five_minutes(identity_jobs.expire_impersonation_sessions),
        cron(
            _coroutine(identity_jobs.purge_expired_tokens),
            name="purge_expired_tokens",
            hour=3,
            minute=0,
            run_at_startup=False,
        ),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = MAX_TRIES
    retry_jobs = True
    # Read from the environment directly: importing this module must not require full Settings.
    redis_settings = RedisSettings.from_dsn(
        os.environ.get("APP_REDIS_URL", "redis://localhost:6379")
    )
