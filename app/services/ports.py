"""Cross-epic seams (A-06): consumed by Epic-01, implemented by Epics 02/05/08.

Epic-01 ships and is tested against the fakes in ``tests/fakes.py``.
"""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from app.models.identity import PurchaseApproval


class CommitmentChecker(Protocol):
    async def future_paid_rsvps(self, player_profile_id: UUID, trainer_id: UUID) -> list[UUID]:
        """Ids of future paid RSVPs that block unlinking a player from a trainer."""
        ...


class PurchaseExecutor(Protocol):
    async def execute(self, approval: PurchaseApproval) -> None:
        """Perform the approved purchase inside the caller's transaction."""
        ...


class CampSubmissionReader(Protocol):
    async def get_prefill(self, submission_id: UUID) -> Mapping[str, str] | None:
        """Registration pre-fill for a camp submission, or ``None`` when it does not exist."""
        ...


class NullCommitmentChecker:
    """Production default until the scheduling epic lands: no RSVP data exists, so no blocker."""

    async def future_paid_rsvps(self, player_profile_id: UUID, trainer_id: UUID) -> list[UUID]:
        return []


class UnconfiguredPurchaseExecutor:
    """Production default until the owning epic wires a real executor: fails loudly.

    Approving without an executor must never look like a successful purchase.
    """

    async def execute(self, approval: PurchaseApproval) -> None:
        raise RuntimeError("PurchaseExecutor is not configured")
