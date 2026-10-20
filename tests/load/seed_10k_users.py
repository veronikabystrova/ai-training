"""Seed a migrated database with ~10 000 users for the k6 scenarios (tests/load/scenarios.js).

    uv run python -m tests.load.seed_10k_users --dsn postgresql://app:app@localhost:5432/app

Rows are written with one bulk ``COPY`` per table. Every user shares ONE password hash (hashing
10 000 passwords at production cost would take minutes), computed with the production Argon2
parameters so logins never trigger a rehash.

Seeded accounts (all ``active``, e-mail verified, password ``LOAD_PASSWORD``):

* ``load-admin@example.com``       super_admin (``GET /admin/users``)
* ``load-trainer-<n>@example.com`` trainers, with a trainer profile
* ``load-player-<n>@example.com``  players (``GET /me``, ``POST /auth/login``)

Never point this at a shared or production database.
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from argon2 import PasswordHasher

from app.core.config import Settings
from app.core.ids import new_id

LOAD_PASSWORD = "Load-Test-Passw0rd-xK9!"
ADMIN_EMAIL = "load-admin@example.com"
TRAINER_EVERY = 100  # one trainer per 100 users
USER_COLUMNS = (
    "id",
    "email",
    "password_hash",
    "role",
    "status",
    "is_email_verified",
    "must_change_password",
    "created_at",
    "updated_at",
)
PROFILE_COLUMNS = ("user_id", "first_name", "last_name", "timezone")
TRAINER_COLUMNS = ("user_id", "business_name", "slug")


def build_rows(
    count: int, password_hash: str, now: datetime
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """``(users, profiles, trainer_profiles)`` for ``count`` users, the admin included."""
    users: list[tuple[Any, ...]] = []
    profiles: list[tuple[Any, ...]] = []
    trainers: list[tuple[Any, ...]] = []
    trainer_count = max(1, count // TRAINER_EVERY)
    for n in range(count):
        user_id = new_id()
        if n == 0:
            email, role, first, last = ADMIN_EMAIL, "super_admin", "Load", "Admin"
        elif n <= trainer_count:
            email, role = f"load-trainer-{n}@example.com", "trainer"
            first, last = "Trainer", f"Load{n}"
            trainers.append((user_id, f"Load Trainer {n} Academy", f"load-trainer-{n}"))
        else:
            email, role = f"load-player-{n}@example.com", "player"
            first, last = f"Player{n}", f"Load{n}"
        created = now - timedelta(seconds=count - n)  # distinct, increasing created_at
        users.append((user_id, email, password_hash, role, "active", True, False, created, created))
        profiles.append((user_id, first, last, "UTC"))
    return users, profiles, trainers


async def seed(dsn: str, count: int) -> None:
    settings = Settings.model_construct()  # defaults only: no env needed for the Argon2 params
    hasher = PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
    )
    password_hash = hasher.hash(LOAD_PASSWORD)
    users, profiles, trainers = build_rows(count, password_hash, datetime.now(UTC))

    conn = await asyncpg.connect(dsn)
    try:
        # COPY is binary and asyncpg has no codec for citext; its binary form is plain UTF-8.
        await conn.set_type_codec(
            "citext",
            schema="public",
            encoder=str.encode,
            decoder=bytes.decode,
            format="binary",
        )
        if await conn.fetchval("SELECT 1 FROM users WHERE email = $1", ADMIN_EMAIL):
            sys.exit("already seeded (load-admin@example.com exists): use a fresh database")
        async with conn.transaction():
            await conn.copy_records_to_table("users", records=users, columns=USER_COLUMNS)
            await conn.copy_records_to_table("profiles", records=profiles, columns=PROFILE_COLUMNS)
            await conn.copy_records_to_table(
                "trainer_profiles", records=trainers, columns=TRAINER_COLUMNS
            )
        await conn.execute("ANALYZE users, profiles, trainer_profiles")
    finally:
        await conn.close()
    print(f"seeded {len(users)} users ({len(trainers)} trainers); password: {LOAD_PASSWORD}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", default=os.environ.get("LOAD_DATABASE_URL"), required=False)
    parser.add_argument("--count", type=int, default=10_000)
    args = parser.parse_args()
    if not args.dsn:
        sys.exit("pass --dsn or set LOAD_DATABASE_URL (postgresql://user:pass@host:port/db)")
    asyncio.run(seed(args.dsn.replace("+asyncpg", ""), args.count))


if __name__ == "__main__":
    main()
