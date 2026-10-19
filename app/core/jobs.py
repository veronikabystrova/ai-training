"""Background-job seam. Services enqueue by name; the ARQ adapter arrives with Task 25.

Raw one-time tokens travel only in job kwargs (never logged) so the email worker can build the
link; every job re-reads user state by id.
"""

from typing import Protocol

import structlog


class JobQueue(Protocol):
    async def enqueue(self, name: str, **kwargs: str) -> None: ...


async def enqueue_best_effort(queue: JobQueue, name: str, **kwargs: str) -> bool:
    """Enqueue after a commit; a failure is logged (job name and error type only), not raised.

    The state is already committed, so a 500 would misreport it and break client retries; the
    user can re-trigger the email (resend, new reset request). Revisit with an outbox.
    """
    try:
        await queue.enqueue(name, **kwargs)
    except Exception as exc:
        structlog.get_logger(__name__).error(
            "job_enqueue_failed", job=name, error_type=type(exc).__name__
        )
        return False
    return True


class LoggingJobQueue:
    """Development adapter: logs the job name only, never its kwargs (they hold raw tokens)."""

    def __init__(self) -> None:
        self._log = structlog.get_logger(__name__)

    async def enqueue(self, name: str, **kwargs: str) -> None:
        self._log.info("job.enqueue", job=name)
