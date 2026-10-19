"""Public URLs for stored media keys (avatars, logos) and caller-bound upload keys."""

import hashlib
import hmac
import secrets
from uuid import UUID


class MediaUrlBuilder:
    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")

    def url(self, key: str | None) -> str | None:
        return None if key is None else f"{self._base}/{key}"


def _tag(secret: bytes, purpose: str, owner_id: UUID, nonce: bytes) -> bytes:
    return hmac.new(
        secret, purpose.encode() + b":" + owner_id.bytes + nonce, hashlib.sha256
    ).digest()[:8]


def issue_bound_key(secret: bytes, *, prefix: str, purpose: str, owner_id: UUID) -> str:
    """``<prefix>/<uuid-shaped>``: 8 random bytes plus an 8-byte MAC binding them to the owner.

    The key keeps the shape of a UUID so schema patterns stay simple, and ``purpose`` separates
    the MAC domains (a logo key never validates as another kind of key).
    """
    nonce = secrets.token_bytes(8)
    return f"{prefix}/{UUID(bytes=nonce + _tag(secret, purpose, owner_id, nonce))}"


def key_is_bound_to(secret: bytes, *, prefix: str, purpose: str, owner_id: UUID, key: str) -> bool:
    """True only for a key ``issue_bound_key`` produced for this owner (canonical spelling)."""
    name = key.removeprefix(f"{prefix}/")
    try:
        parsed = UUID(name)
    except ValueError:
        return False
    if str(parsed) != name or key != f"{prefix}/{name}":
        return False
    raw = parsed.bytes
    return hmac.compare_digest(raw[8:], _tag(secret, purpose, owner_id, raw[:8]))
