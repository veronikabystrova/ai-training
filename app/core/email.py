from typing import Protocol

import structlog


class EmailSender(Protocol):
    async def send(self, *, to: str, template: str, context: dict[str, str]) -> None: ...


class LoggingEmailSender:
    """Development adapter. Logs the template name only, never recipients or tokens."""

    def __init__(self) -> None:
        self._log = structlog.get_logger(__name__)

    async def send(self, *, to: str, template: str, context: dict[str, str]) -> None:
        self._log.info("email.send", template=template)
