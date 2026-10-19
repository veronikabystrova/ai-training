from typing import Literal

from pydantic import BaseModel

from app.models.identity import ShareLinkKind
from app.services.sharelink_service import SharePreview


class ShareLinkPreview(BaseModel):
    trainer_name: str
    business_name: str
    logo_url: str | None
    kind: ShareLinkKind
    valid: Literal[True] = True  # invalid links are 404, never ``valid: false``
    target_email_hint: str | None  # coach_unique only: "s***@example.com"


def preview_response(view: SharePreview) -> ShareLinkPreview:
    return ShareLinkPreview(
        trainer_name=view.trainer_name,
        business_name=view.business_name,
        logo_url=view.logo_url,
        kind=view.kind,
        target_email_hint=view.target_email_hint,
    )
