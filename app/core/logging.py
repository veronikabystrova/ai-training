import re
from collections.abc import MutableMapping
from typing import Any

import structlog
from structlog.typing import WrappedLogger

_SENSITIVE_KEY = re.compile(r"password|token|authorization|secret", re.IGNORECASE)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items() if not _SENSITIVE_KEY.search(str(k))}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def redact_processor(
    _logger: WrappedLogger, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Drop every key whose name looks like a credential, at any nesting depth (by key name)."""
    return {k: _redact(v) for k, v in event_dict.items() if not _SENSITIVE_KEY.search(k)}


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        # Not cached, so ``structlog.testing.capture_logs`` can intercept loggers created earlier.
        cache_logger_on_first_use=False,
    )
