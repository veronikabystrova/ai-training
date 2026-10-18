"""Background-job seam. Services enqueue by name; the ARQ adapter arrives with Task 25.

Raw one-time tokens travel only in job kwargs (never logged) so the email worker can build the
link; every job re-reads user state by id.
"""

from typing import Protocol

import structlog


class JobQueue(Protocol):
    async def enqueue(self, name: str, **kwargs: str) -> None: ...


class LoggingJobQueue:
    """Development adapter: logs the job name only, never its kwargs (they hold raw tokens)."""

    def __init__(self) -> None:
        self._log = structlog.get_logger(__name__)

    async def enqueue(self, name: str, **kwargs: str) -> None:
        self._log.info("job.enqueue", job=name)
