"""Best-effort removal of media objects that no row references any more (replaced or erased).

Runs in the worker via the ``delete_storage_object`` job. Deleting is idempotent, so an ARQ retry
is harmless. Only keys under the media prefixes are accepted: the job must never be a general
"delete anything in the bucket" primitive.
"""

from app.core.storage import ObjectStorage

MEDIA_PREFIXES = ("avatars/", "logos/", "incoming/")


class StorageCleanupService:
    def __init__(self, *, storage: ObjectStorage) -> None:
        self._storage = storage

    async def delete_object(self, *, key: str) -> None:
        if not key.startswith(MEDIA_PREFIXES):
            raise ValueError("not a media key")
        await self._storage.delete(key)
