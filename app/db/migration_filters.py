"""Autogenerate filters shared by ``alembic/env.py`` and the drift test."""

from typing import Any

# Expression index with an operator class: Alembic cannot reflect-and-compare it, so it would be
# reported as drift forever. It is created by raw DDL in 0002 and mirrored on the model for docs.
_UNCOMPARABLE_INDEXES = frozenset({"ix_profiles_full_name_trgm"})


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    return not (type_ == "index" and name in _UNCOMPARABLE_INDEXES)
