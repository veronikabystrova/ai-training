"""Pure rules for GDPR erasure (US-01.13): what an erased account looks like.

No I/O here, so the anonymiser is unit-tested without a database. ``AdminUserService.erase``
applies these values through the repositories.
"""

import hashlib
from datetime import datetime
from uuid import UUID

ERASED_PLAYER_NAME = "Deleted Player"
ERASED_EMAIL_DOMAIN = "erased.invalid"


def erased_email(user_id: UUID) -> str:
    """Unique placeholder (``users.email`` is unique); carries nothing of the original."""
    return f"deleted-{user_id}@{ERASED_EMAIL_DOMAIN}"


def email_fingerprint(email: str) -> str:
    """Hex SHA-256 of the lowercased address: the only trace kept in ``user_erasures``."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


def retention_until(erased_at: datetime, *, years: int) -> datetime:
    """``erased_at`` plus whole calendar years; Feb 29 falls back to Feb 28 in a common year."""
    try:
        return erased_at.replace(year=erased_at.year + years)
    except ValueError:  # Feb 29 -> a year without one
        return erased_at.replace(year=erased_at.year + years, day=28)
