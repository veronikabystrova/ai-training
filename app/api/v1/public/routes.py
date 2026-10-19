"""Unauthenticated endpoints: no principal dependency, rate limited per client IP."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.api.deps import ClockDep, FingerprintDep, SessionDep, SettingsDep, rate_limit
from app.api.v1.public.schemas import ShareLinkPreview, preview_response
from app.api.v1.trainer.deps import BrandingServiceDep
from app.api.v1.trainer.schemas import BrandingResponse, branding_response
from app.core.ratelimit import RouteClass
from app.services.sharelink_service import SharePreviewService

router = APIRouter(prefix="/public", tags=["public"])

_preview_limit = Depends(rate_limit(RouteClass.public_preview_ip))
# No principal here, so the default limit is keyed by client IP (decision 2026-10-18).
_default_by_ip = Depends(rate_limit(RouteClass.default_principal, by_ip=True))

_ERRORS: dict[int | str, dict[str, Any]] = {429: {"description": "RATE_LIMITED"}}


def get_preview_service(
    session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> SharePreviewService:
    return SharePreviewService(session=session, settings=settings, clock=clock)


PreviewServiceDep = Annotated[SharePreviewService, Depends(get_preview_service)]


@router.get(
    "/share-links/{code}",
    response_model=ShareLinkPreview,
    summary="Preview a share link",
    dependencies=[_preview_limit],
    responses={**_ERRORS, 404: {"description": "SHARE_LINK_INVALID"}},
)
async def preview_share_link(
    code: str, service: PreviewServiceDep, client: FingerprintDep
) -> ShareLinkPreview:
    return preview_response(await service.preview(code=code, client=client))


@router.get(
    "/trainers/{slug}/branding",
    response_model=BrandingResponse,
    summary="A trainer's public branding",
    dependencies=[_default_by_ip],
    responses={**_ERRORS, 404: {"description": "Unknown slug or deactivated trainer"}},
)
async def get_public_branding(
    slug: str, service: BrandingServiceDep, response: Response
) -> BrandingResponse:
    view = await service.get_public(slug)
    response.headers["Cache-Control"] = "public, max-age=300"
    return branding_response(view)
