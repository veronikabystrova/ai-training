"""Session lifecycle: login, refresh rotation with reuse detection, logout.

The service owns the transaction (repositories never commit). Failure paths that wrote
nothing simply raise: the request-scoped session is closed, and thereby rolled back, by
``get_session``. The one failure that must persist (reuse detection) commits first.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import BadRequestError, NotFoundError, UnauthorizedError
from app.core.ids import new_id
from app.core.jobs import JobQueue
from app.core.ratelimit import RateLimiter, RouteClass
from app.core.security import (
    ClientFingerprint,
    PasswordHasher,
    PasswordPolicyPort,
    TokenIssuer,
    hash_token,
)
from app.models.identity import OttPurpose, User, UserRole, UserStatus
from app.repositories.coach_profile import CoachProfileRepository
from app.repositories.impersonation import ImpersonationSessionRepository
from app.repositories.one_time_token import OneTimeTokenRepository
from app.repositories.player import PlayerProfileRepository
from app.repositories.refresh_token import RefreshTokenRepository
from app.repositories.user import UserRepository
from app.services.audit_service import AuditService


@dataclass(frozen=True, slots=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class IssuedSession:
    tokens: TokenSet
    user: User
    trainer_id: UUID | None
    child: bool


def _invalid_credentials() -> UnauthorizedError:
    return UnauthorizedError("INVALID_CREDENTIALS", "Invalid credentials")


def _token_invalid() -> BadRequestError:
    # One code and message for unknown, expired, used and mismatched tokens: no oracle.
    return BadRequestError("TOKEN_INVALID", "Token is invalid or expired")


def _invalid_refresh() -> UnauthorizedError:
    return UnauthorizedError("INVALID_REFRESH_TOKEN", "Invalid refresh token")


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        clock: Clock,
        hasher: PasswordHasher,
        issuer: TokenIssuer,
        limiter: RateLimiter,
        audit: AuditService,
        policy: PasswordPolicyPort,
        jobs: JobQueue,
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock
        self._hasher = hasher
        self._issuer = issuer
        self._limiter = limiter
        self._audit = audit
        self._policy = policy
        self._jobs = jobs
        self._users = UserRepository(session)
        self._coaches = CoachProfileRepository(session)
        self._players = PlayerProfileRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)
        self._impersonations = ImpersonationSessionRepository(session)
        self._tokens = OneTimeTokenRepository(session)

    # --- login -----------------------------------------------------------------------------

    async def login(self, *, email: str, password: str, client: ClientFingerprint) -> IssuedSession:
        await self._limiter.hit(RouteClass.login_email, email)
        user = await self._users.get_by_email(email)
        # Always verify (a dummy digest when the user is missing): constant time, no oracle.
        verified = await self._hasher.verify(user.password_hash if user else None, password)
        if user is None or not verified or user.status is not UserStatus.active:
            raise _invalid_credentials()

        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = await self._hasher.hash(password)
        user.last_login_at = self._clock.now()

        issued = await self._issue_session(user, client)
        await self._session.commit()
        return issued

    async def _issue_session(self, user: User, client: ClientFingerprint) -> IssuedSession:
        trainer_id = await self._tenant_of(user)
        child = await self._is_child(user)
        access, _ = self._issuer.issue_access(
            user_id=user.id, role=user.role.value, trainer_id=trainer_id, act=None, child=child
        )
        refresh = await self._refresh_tokens.issue(
            user_id=user.id,
            family_id=new_id(),
            expires_at=self._refresh_expiry(),
            user_agent_hash=client.user_agent_hash,
            ip_hash=client.ip_hash,
        )
        tokens = TokenSet(access, refresh, self._settings.access_ttl_seconds)
        return IssuedSession(tokens=tokens, user=user, trainer_id=trainer_id, child=child)

    async def _tenant_of(self, user: User) -> UUID | None:
        if user.role is UserRole.trainer:
            return user.id
        if user.role is UserRole.coach:
            tenure = await self._coaches.get_active_for_user(user.id)
            return tenure.trainer_id if tenure else None
        return None

    async def _is_child(self, user: User) -> bool:
        if user.role is not UserRole.player:
            return False
        profile = await self._players.get_by_login_user(user.id)
        return profile is not None and profile.is_dependent

    def _refresh_expiry(self) -> datetime:
        return self._clock.now() + timedelta(days=self._settings.refresh_ttl_days)

    # --- refresh ---------------------------------------------------------------------------

    async def refresh(self, *, refresh_token: str, client: ClientFingerprint) -> TokenSet:
        now = self._clock.now()
        row = await self._refresh_tokens.lock_by_hash(hash_token(refresh_token))
        if row is None or row.revoked_at is not None:
            raise _invalid_refresh()
        if row.consumed_at is not None:
            # A rotated token came back: assume theft and kill the whole family. The revoke
            # and its audit row must survive, so commit before raising.
            await self._refresh_tokens.revoke_family(row.family_id, now=now)
            await self._audit.record(
                action="auth.refresh_reuse_detected",
                actor_id=row.user_id,
                subject_type="user",
                subject_id=row.user_id,
                metadata={"family_id": str(row.family_id)},
            )
            await self._session.commit()
            raise UnauthorizedError(
                "REFRESH_REUSE_DETECTED", "Refresh token reuse detected; sign in again"
            )
        if row.expires_at <= now:
            raise _invalid_refresh()

        user = await self._users.get(row.user_id)
        if user is None or user.status is not UserStatus.active:
            raise _invalid_refresh()

        expires_at = self._refresh_expiry()
        act: UUID | None = None
        jti: UUID | None = None
        # Impersonation families are keyed by their session's jti (see ImpersonationService):
        # they never outlive the session (G-14) and keep carrying ``act``.
        imp = await self._impersonations.get_by_jti(row.family_id)
        if imp is not None:
            admin = await self._users.get(imp.admin_user_id)
            if (
                imp.ended_at is not None
                or imp.expires_at <= now
                or admin is None
                or admin.status is not UserStatus.active
            ):
                await self._refresh_tokens.revoke_family(row.family_id, now=now)
                await self._session.commit()
                raise UnauthorizedError("IMPERSONATION_EXPIRED", "Impersonation session expired")
            expires_at = min(expires_at, imp.expires_at)
            act, jti = imp.admin_user_id, imp.jti

        # Claims are rebuilt from the DB: a moved coach gets the new tenant (A-03).
        trainer_id = await self._tenant_of(user)
        child = await self._is_child(user)
        access, _ = self._issuer.issue_access(
            user_id=user.id,
            role=user.role.value,
            trainer_id=trainer_id,
            act=act,
            child=child,
            jti=jti,
        )
        new_refresh = await self._refresh_tokens.rotate(
            row,
            now=now,
            expires_at=expires_at,
            user_agent_hash=client.user_agent_hash,
            ip_hash=client.ip_hash,
        )
        await self._session.commit()
        return TokenSet(access, new_refresh, self._settings.access_ttl_seconds)

    # --- logout ----------------------------------------------------------------------------

    async def logout(self, *, user_id: UUID, refresh_token: str) -> None:
        row = await self._refresh_tokens.lock_by_hash(hash_token(refresh_token))
        if row is None or row.user_id != user_id:
            raise NotFoundError()  # never confirm that someone else's token exists
        await self._refresh_tokens.revoke_family(row.family_id, now=self._clock.now())
        await self._session.commit()

    # --- password reset, email verification, trainer setup ----------------------------------

    def _ott_ttl(self, purpose: OttPurpose) -> timedelta:
        return timedelta(hours=self._settings.ott_ttl_hours[purpose.value])

    async def request_password_reset(self, *, email: str) -> None:
        """Always succeeds from the caller's view; only an active user gets a token and a job."""
        await self._limiter.hit(RouteClass.password_reset_email, email)
        user = await self._users.get_by_email(email)
        if user is None or user.status is not UserStatus.active:
            return
        raw = await self._tokens.issue(
            user_id=user.id,
            purpose=OttPurpose.password_reset,
            ttl=self._ott_ttl(OttPurpose.password_reset),
            now=self._clock.now(),
        )
        await self._session.commit()
        await self._jobs.enqueue("send_password_reset", user_id=str(user.id), token=raw)

    async def confirm_password_reset(self, *, token: str, new_password: str) -> None:
        # Policy and hashing run before the token is consumed: a weak password leaves the
        # link usable, and the row lock is not held across the argon2 work.
        self._policy.validate(new_password)
        digest = await self._hasher.hash(new_password)
        now = self._clock.now()
        consumed = await self._tokens.consume(raw=token, purpose=OttPurpose.password_reset, now=now)
        if consumed is None:
            raise _token_invalid()
        user = await self._users.get(consumed.user_id)
        if user is None or user.status is not UserStatus.active:
            raise _token_invalid()  # the request session rolls the consume back
        user.password_hash = digest
        user.must_change_password = False
        await self._refresh_tokens.revoke_all_for_user(user.id, now=now)
        await self._audit.record(
            action="auth.password_reset",
            actor_id=user.id,
            subject_type="user",
            subject_id=user.id,
        )
        await self._session.commit()

    async def verify_email(self, *, token: str) -> None:
        consumed = await self._tokens.consume(
            raw=token, purpose=OttPurpose.email_verify, now=self._clock.now()
        )
        if consumed is None:
            raise _token_invalid()
        user = await self._users.get(consumed.user_id)
        if user is None or user.status is not UserStatus.active:
            raise _token_invalid()
        user.is_email_verified = True
        await self._session.commit()

    async def resend_email_verification(self, *, user_id: UUID) -> bool:
        """True when a new token was issued, False when the address is already verified."""
        user = await self._users.get(user_id)
        if user is None:
            raise UnauthorizedError()
        if user.is_email_verified:
            return False
        raw = await self._tokens.issue(
            user_id=user.id,
            purpose=OttPurpose.email_verify,
            ttl=self._ott_ttl(OttPurpose.email_verify),
            now=self._clock.now(),
        )
        await self._session.commit()
        await self._jobs.enqueue("send_email_verification", user_id=str(user.id), token=raw)
        return True

    async def setup_password(
        self, *, token: str, new_password: str, client: ClientFingerprint
    ) -> IssuedSession:
        """Provisioned trainer: pending -> active in one transaction, then a session."""
        self._policy.validate(new_password)
        digest = await self._hasher.hash(new_password)
        now = self._clock.now()
        consumed = await self._tokens.consume(raw=token, purpose=OttPurpose.trainer_setup, now=now)
        if consumed is None:
            raise _token_invalid()
        user = await self._users.get(consumed.user_id)
        if user is None or user.status is not UserStatus.pending:
            raise _token_invalid()
        user.password_hash = digest
        user.status = UserStatus.active
        user.must_change_password = False
        user.is_email_verified = True  # the invite reached the mailbox
        user.last_login_at = now
        await self._audit.record(
            action="auth.password_setup",
            actor_id=user.id,
            subject_type="user",
            subject_id=user.id,
        )
        issued = await self._issue_session(user, client)
        await self._session.commit()
        return issued
