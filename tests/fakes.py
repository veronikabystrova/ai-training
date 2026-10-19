from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from app.core.storage import ObjectMeta, PresignedPut
from app.models.identity import PurchaseApproval


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


@dataclass(frozen=True)
class SentEmail:
    to: str
    template: str
    context: dict[str, str]


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    async def send(self, *, to: str, template: str, context: dict[str, str]) -> None:
        self.sent.append(SentEmail(to, template, context))


@dataclass(frozen=True)
class EnqueuedJob:
    name: str
    kwargs: dict[str, str]


class FakeJobQueue:
    """Records ``(name, kwargs)`` of every enqueued job."""

    def __init__(self) -> None:
        self.enqueued: list[EnqueuedJob] = []

    async def enqueue(self, name: str, **kwargs: str) -> None:
        self.enqueued.append(EnqueuedJob(name, kwargs))


class FakeObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> PresignedPut:
        return PresignedPut(
            url=f"https://fake-storage.test/{key}",
            headers={"Content-Type": content_type},
            expires_in=expires_in,
        )

    async def head(self, key: str) -> ObjectMeta | None:
        item = self.objects.get(key)
        return ObjectMeta(content_type=item[1], size=len(item[0])) if item else None

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class FakeCommitmentChecker:
    """Recording ``CommitmentChecker``. Set ``rsvps`` to block unlinking, ``error`` to fail."""

    def __init__(self, *, rsvps: list[UUID] | None = None, error: Exception | None = None) -> None:
        self.rsvps = rsvps or []
        self.error = error
        self.calls: list[tuple[UUID, UUID]] = []

    async def future_paid_rsvps(self, player_profile_id: UUID, trainer_id: UUID) -> list[UUID]:
        self.calls.append((player_profile_id, trainer_id))
        if self.error:
            raise self.error
        return list(self.rsvps)


class FakePurchaseExecutor:
    """Recording ``PurchaseExecutor``. Set ``error`` to fail the purchase."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.executed: list[PurchaseApproval] = []

    async def execute(self, approval: PurchaseApproval) -> None:
        if self.error:
            raise self.error
        self.executed.append(approval)


class FakeCampSubmissionReader:
    """Recording ``CampSubmissionReader`` over a ``prefills`` mapping. Set ``error`` to fail."""

    def __init__(
        self,
        prefills: Mapping[UUID, Mapping[str, str]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.prefills = dict(prefills or {})
        self.error = error
        self.requested: list[UUID] = []

    async def get_prefill(self, submission_id: UUID) -> Mapping[str, str] | None:
        self.requested.append(submission_id)
        if self.error:
            raise self.error
        return self.prefills.get(submission_id)
