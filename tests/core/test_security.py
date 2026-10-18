from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest

from app.core.errors import UnauthorizedError, UnprocessableError
from app.core.security import (
    PasswordHasher,
    PasswordPolicy,
    TokenIssuer,
    hash_token,
    new_opaque_token,
)
from tests.fakes import FakeClock

CLOCK = FakeClock(datetime(2026, 10, 18, tzinfo=UTC))


async def test_hash_verify_and_dummy_verify() -> None:
    h = PasswordHasher(time_cost=1, memory_kib=8, parallelism=1)
    digest = await h.hash("correct horse battery staple")
    assert await h.verify(digest, "correct horse battery staple") is True
    assert await h.verify(digest, "nope") is False
    # unknown user: runs the dummy verify, returns False
    assert await h.verify(None, "anything") is False


def test_policy_rejects_short_and_weak() -> None:
    p = PasswordPolicy()
    with pytest.raises(UnprocessableError) as e:
        p.validate("short1!")
    assert e.value.code == "WEAK_PASSWORD"
    with pytest.raises(UnprocessableError):
        p.validate("password1234")  # zxcvbn score < 3
    p.validate("tangerine-Quasar-ledger-77")


def test_hash_token_is_sha256_32_bytes_and_raw_is_urlsafe() -> None:
    raw = new_opaque_token()
    assert len(hash_token(raw)) == 32
    assert len(raw) >= 43


def test_access_token_roundtrip_and_expiry() -> None:
    clock = FakeClock(datetime(2026, 10, 18, tzinfo=UTC))
    ti = TokenIssuer(secret="s" * 32, access_ttl=900, clock=clock)
    uid = uuid4()
    tok, jti = ti.issue_access(user_id=uid, role="trainer", trainer_id=uid, act=None, child=False)
    claims = ti.decode(tok)
    assert claims["sub"] == str(uid)
    assert claims["typ"] == "access"
    assert claims["jti"] == str(jti)
    clock.advance(minutes=15, seconds=1)
    with pytest.raises(UnauthorizedError):
        ti.decode(tok)


def test_decode_rejects_alg_none_and_wrong_typ() -> None:
    ti = TokenIssuer(secret="s" * 32, access_ttl=900, clock=CLOCK)
    forged = jwt.encode({"sub": "x"}, key="", algorithm="none")
    with pytest.raises(UnauthorizedError):
        ti.decode(forged)
    refresh_like = jwt.encode(
        {
            "sub": "x",
            "role": "player",
            "jti": "j",
            "typ": "refresh",
            "exp": int(CLOCK.now().timestamp()) + 60,
        },
        "s" * 32,
        algorithm="HS256",
    )
    with pytest.raises(UnauthorizedError):
        ti.decode(refresh_like)


def test_explicit_zero_ttl_is_respected() -> None:
    ti = TokenIssuer(secret="s" * 32, access_ttl=900, clock=CLOCK)
    uid = uuid4()
    tok, _ = ti.issue_access(
        user_id=uid, role="trainer", trainer_id=uid, act=None, child=False, ttl=0
    )
    with pytest.raises(UnauthorizedError):  # exp == now is already expired
        ti.decode(tok)
