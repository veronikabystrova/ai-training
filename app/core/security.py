import asyncio
import hashlib
import os
import secrets
from typing import Any, Protocol
from uuid import UUID

import anyio.to_thread
import jwt
from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError
from zxcvbn import zxcvbn

from app.core.clock import Clock
from app.core.errors import UnauthorizedError, UnprocessableError
from app.core.ids import new_id

_INVALID_TOKEN = "Invalid token"  # noqa: S105  (error message, not a credential)


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


class PasswordHasher:
    """Argon2id hashing in worker threads, bounded by a semaphore (architecture §6)."""

    def __init__(
        self, *, time_cost: int = 3, memory_kib: int = 65536, parallelism: int = 4
    ) -> None:
        self._h = _Argon2(time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism)
        self._dummy = self._h.hash("dummy-password-for-constant-time")
        self._sem = asyncio.Semaphore(os.cpu_count() or 2)

    async def hash(self, password: str) -> str:
        async with self._sem:
            return await anyio.to_thread.run_sync(self._h.hash, password)

    async def verify(self, digest: str | None, password: str) -> bool:
        target = digest or self._dummy
        async with self._sem:
            try:
                ok = await anyio.to_thread.run_sync(self._h.verify, target, password)
            except (VerificationError, InvalidHashError):
                return False
        return ok and digest is not None

    def needs_rehash(self, digest: str) -> bool:
        return self._h.check_needs_rehash(digest)


class PasswordPolicyPort(Protocol):
    def validate(self, password: str, user_inputs: list[str] | None = None) -> None: ...


class PasswordPolicy:
    def validate(self, password: str, user_inputs: list[str] | None = None) -> None:
        if len(password) < 12 or zxcvbn(password, user_inputs=user_inputs or [])["score"] < 3:
            raise UnprocessableError("WEAK_PASSWORD", "Password is too weak")


class TokenIssuer:
    def __init__(self, *, secret: str, access_ttl: int, clock: Clock) -> None:
        self._secret, self._ttl, self._clock = secret, access_ttl, clock

    def issue_access(
        self,
        *,
        user_id: UUID,
        role: str,
        trainer_id: UUID | None,
        act: UUID | None,
        child: bool,
        ttl: int | None = None,
        jti: UUID | None = None,
    ) -> tuple[str, UUID]:
        jti = jti or new_id()
        now = int(self._clock.now().timestamp())
        claims: dict[str, Any] = {
            "sub": str(user_id),
            "role": role,
            "tid": str(trainer_id) if trainer_id else None,
            "act": str(act) if act else None,
            "child": child,
            "jti": str(jti),
            "typ": "access",
            "iat": now,
            "exp": now + (self._ttl if ttl is None else ttl),
        }
        return jwt.encode(claims, self._secret, algorithm="HS256"), jti

    def decode(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                # pyjwt checks exp against wall clock; verify it ourselves with the injected clock
                options={
                    "require": ["sub", "role", "exp", "jti", "typ"],
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("INVALID_TOKEN", _INVALID_TOKEN) from exc
        if claims["typ"] != "access":
            raise UnauthorizedError("INVALID_TOKEN", _INVALID_TOKEN)
        if claims["exp"] <= int(self._clock.now().timestamp()):
            raise UnauthorizedError("INVALID_TOKEN", _INVALID_TOKEN)
        return claims
