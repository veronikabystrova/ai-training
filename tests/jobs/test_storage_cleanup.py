import pytest

from app.jobs import identity_jobs
from app.jobs.identity_jobs import Ctx
from app.jobs.worker import WorkerSettings
from tests.fakes import FakeObjectStorage


async def test_delete_storage_object_removes_the_object(
    ctx: Ctx, storage: FakeObjectStorage
) -> None:
    await storage.put("logos/old", b"x", "image/png")
    await storage.put("logos/keep", b"y", "image/png")
    await identity_jobs.delete_storage_object(ctx, key="logos/old")
    assert set(storage.objects) == {"logos/keep"}


async def test_delete_storage_object_accepts_the_incoming_prefix(
    ctx: Ctx, storage: FakeObjectStorage
) -> None:
    await storage.put("incoming/logos/raw", b"<svg/>", "image/svg+xml")
    await identity_jobs.delete_storage_object(ctx, key="incoming/logos/raw")
    assert storage.objects == {}


async def test_delete_storage_object_is_idempotent_for_a_missing_object(
    ctx: Ctx, storage: FakeObjectStorage
) -> None:
    await identity_jobs.delete_storage_object(ctx, key="avatars/gone")
    await identity_jobs.delete_storage_object(ctx, key="avatars/gone")
    assert storage.objects == {}


async def test_delete_storage_object_refuses_keys_outside_media_prefixes(
    ctx: Ctx, storage: FakeObjectStorage
) -> None:
    await storage.put("secrets/key", b"x", "text/plain")
    with pytest.raises(ValueError, match="media key"):
        await identity_jobs.delete_storage_object(ctx, key="secrets/key")
    assert "secrets/key" in storage.objects


async def test_delete_storage_object_is_registered_with_retries() -> None:
    registered = {f.name: f for f in WorkerSettings.functions}
    assert registered["delete_storage_object"].max_tries == 5
