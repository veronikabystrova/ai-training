from collections.abc import Mapping
from typing import Protocol
from uuid import UUID


class FeatureFlagService(Protocol):
    """Epic-07 swaps the implementation, not the call sites."""

    async def is_enabled(self, flag: str, trainer_id: UUID | None = None) -> bool: ...


class StaticFeatureFlagService:
    """Stub: answers from a fixed mapping; unknown flags are off. Ignores the tenant."""

    def __init__(self, defaults: Mapping[str, bool] | None = None) -> None:
        self._defaults = dict(defaults or {})

    async def is_enabled(self, flag: str, trainer_id: UUID | None = None) -> bool:
        return self._defaults.get(flag, False)
