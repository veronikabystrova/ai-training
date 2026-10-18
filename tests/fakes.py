from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.core.storage import ObjectMeta, PresignedPut


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
