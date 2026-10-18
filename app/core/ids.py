from uuid import UUID

from uuid_utils.compat import uuid7


def new_id() -> UUID:
    return uuid7()
