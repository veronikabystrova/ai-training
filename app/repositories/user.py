from datetime import datetime
from uuid import UUID

from sqlalchemy import Text, cast, literal_column, select, tuple_, union, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.core.errors import UnprocessableError
from app.core.pagination import decode_cursor, encode_cursor
from app.db.session import rowcount
from app.models.identity import Profile, User, UserRole, UserStatus

MIN_QUERY_LENGTH = 2


def _like_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, user: User) -> User:
        self._s.add(user)
        await self._s.flush()
        return user

    async def get(self, user_id: UUID) -> User | None:
        return await self._s.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """``users.email`` is CITEXT, so the comparison is case-insensitive."""
        result = await self._s.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def list_directory(
        self,
        *,
        query: str | None = None,
        role: UserRole | None = None,
        status: UserStatus | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[User], str | None]:
        """Admin directory, newest first, keyset-paginated on ``(created_at, id)``."""
        stmt = (
            select(User)
            .outerjoin(Profile, Profile.user_id == User.id)
            .options(contains_eager(User.profile))
            .order_by(User.created_at.desc(), User.id.desc())
            .limit(limit + 1)
        )
        if query is not None:
            term = query.strip()
            if len(term) < MIN_QUERY_LENGTH:
                raise UnprocessableError(
                    "VALIDATION_ERROR", f"Query must be at least {MIN_QUERY_LENGTH} characters"
                )
            pattern = _like_pattern(term)
            full_name = Profile.first_name + literal_column("' '") + Profile.last_name
            # Each branch is a single-table predicate so Postgres can use its trigram index
            # (a plain ``CITEXT ilike`` and an ``OR`` across the join both force a seq scan).
            matches = union(
                select(User.id).where(cast(User.email, Text).ilike(pattern, escape="\\")),
                select(Profile.user_id).where(full_name.ilike(pattern, escape="\\")),
            )
            stmt = stmt.where(User.id.in_(matches))
        if role is not None:
            stmt = stmt.where(User.role == role)
        if status is not None:
            stmt = stmt.where(User.status == status)
        if cursor is not None:
            ts, ident = decode_cursor(cursor)
            stmt = stmt.where(tuple_(User.created_at, User.id) < tuple_(ts, ident))

        rows = list((await self._s.execute(stmt)).scalars().unique())
        if len(rows) <= limit:
            return rows, None
        rows = rows[:limit]
        last = rows[-1]
        return rows, encode_cursor(last.created_at, last.id)

    async def set_status(self, user_id: UUID, status: UserStatus) -> bool:
        result = await self._s.execute(update(User).where(User.id == user_id).values(status=status))
        return rowcount(result) > 0

    async def anonymise(self, user_id: UUID, *, email: str, now: datetime) -> None:
        """Scrub PII in place; the row stays so foreign keys keep resolving."""
        await self._s.execute(
            update(User)
            .where(User.id == user_id)
            .values(
                email=email,
                password_hash="!",  # noqa: S106  (unusable hash, not a credential)
                status=UserStatus.deleted,
                deleted_at=now,
                is_email_verified=False,
                last_login_at=None,
            )
        )
        await self._s.execute(
            update(Profile)
            .where(Profile.user_id == user_id)
            .values(
                first_name="Deleted", last_name="User", phone=None, avatar_key=None, school=None
            )
        )
