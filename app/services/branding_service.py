"""Trainer branding: business name, colours and logo (also read by the public endpoint).

The service owns the transaction. Logo uploads mirror the avatar flow: presign a key bound to
the trainer, upload straight to storage, then confirm; an SVG is sanitised and re-serialised
before the key is persisted.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, UnprocessableError
from app.core.media import MediaUrlBuilder, issue_bound_key, key_is_bound_to
from app.core.storage import ObjectStorage, PresignedPut
from app.core.svg import sanitize_svg
from app.models.identity import TrainerProfile
from app.repositories.profile import TrainerProfileRepository

LOGO_CONTENT_TYPES = frozenset({"image/png", "image/svg+xml", "image/webp"})
LOGO_MAX_BYTES = 2 * 1024 * 1024
BRANDING_FIELDS = frozenset({"business_name", "brand_primary_color", "brand_secondary_color"})

_LOGO_PREFIX = "logos"
_LOGO_PURPOSE = "logo"


@dataclass(frozen=True, slots=True)
class BrandingView:
    business_name: str
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None


class BrandingService:
    def __init__(
        self, *, session: AsyncSession, settings: Settings, storage: ObjectStorage
    ) -> None:
        self._session = session
        self._settings = settings
        self._storage = storage
        self._secret = settings.jwt_secret.get_secret_value().encode()
        self._media = MediaUrlBuilder(settings.media_base_url)
        self._trainers = TrainerProfileRepository(session)

    def _view(self, profile: TrainerProfile) -> BrandingView:
        return BrandingView(
            business_name=profile.business_name,
            logo_url=self._media.url(profile.logo_key),
            primary_color=profile.brand_primary_color,
            secondary_color=profile.brand_secondary_color,
        )

    async def _profile(self, trainer_id: UUID) -> TrainerProfile:
        profile = await self._trainers.get(trainer_id)
        if profile is None:
            raise NotFoundError()
        return profile

    async def get(self, trainer_id: UUID) -> BrandingView:
        view = self._view(await self._profile(trainer_id))
        await self._session.commit()  # end the read transaction
        return view

    async def update(self, trainer_id: UUID, changes: Mapping[str, Any]) -> BrandingView:
        """``changes`` holds only the fields the client sent, keyed by column name."""
        profile = await self._profile(trainer_id)
        for name, value in changes.items():
            if name in BRANDING_FIELDS:
                setattr(profile, name, value)
        await self._session.commit()
        return self._view(profile)

    async def presign_logo(
        self, trainer_id: UUID, *, content_type: str, content_length: int
    ) -> tuple[str, PresignedPut]:
        key = issue_bound_key(
            self._secret, prefix=_LOGO_PREFIX, purpose=_LOGO_PURPOSE, owner_id=trainer_id
        )
        presigned = await self._storage.presign_put(
            key, content_type, content_length, self._settings.presign_ttl_seconds
        )
        return key, presigned

    async def confirm_logo(self, trainer_id: UUID, *, key: str) -> BrandingView:
        if not key_is_bound_to(
            self._secret, prefix=_LOGO_PREFIX, purpose=_LOGO_PURPOSE, owner_id=trainer_id, key=key
        ):
            raise NotFoundError()  # not issued to this trainer: same answer as a missing object
        meta = await self._storage.head(key)
        if meta is None:
            raise NotFoundError()
        if meta.content_type not in LOGO_CONTENT_TYPES or meta.size > LOGO_MAX_BYTES:
            raise UnprocessableError(
                "INVALID_LOGO", "Logo must be a png, svg or webp image of at most 2 MB"
            )
        if meta.content_type == "image/svg+xml":
            # Never serve what the client uploaded: only the re-serialised allowlist copy.
            clean = sanitize_svg(await self._storage.get(key))
            await self._storage.put(key, clean, "image/svg+xml")
        profile = await self._profile(trainer_id)
        profile.logo_key = key
        await self._session.commit()
        return self._view(profile)
