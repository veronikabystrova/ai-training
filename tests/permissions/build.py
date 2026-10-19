"""Turns a matrix cell into a concrete request: ids, query, headers and the minimal body."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from tests.permissions.conftest import World
from tests.permissions.matrix import API, QUERY, fill


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str]
    json: Any


def own_ids(w: World, method: str, path: str) -> dict[str, UUID | str]:
    """Ids of tenant A, the caller's own data, so authorization alone decides the status."""
    ids: dict[str, UUID | str] = {
        "player_id": w.ids["child"],  # the child login's own profile: readable by parent and child
        "trainer_id": w.a.trainer,
        "approval_id": w.a.approval,
        "share_link_id": w.a.share_link,
        "player_profile_id": w.a.player,
        "coach_user_id": w.a.coach,
        "user_id": w.ids["target"],
        "code": w.b.share_code,  # a link the (linked) callers do not already belong to
        "slug": w.a.slug,
    }
    if (method, path) == ("POST", f"{API}/admin/users/{{user_id}}/reactivate"):
        ids["user_id"] = w.ids["inactive"]
    return ids


def other_ids(w: World, role: str) -> dict[str, UUID | str]:
    """Ids owned by tenant B (a different trainer and parent)."""
    return {
        "player_id": w.a.player if role == "child" else w.b.player,  # child: a sibling profile
        "trainer_id": w.b.trainer,
        "approval_id": w.b.approval,
        "share_link_id": w.b.share_link,
        "player_profile_id": w.b.player,
        "coach_user_id": w.b.coach,
    }


async def _issued_key(
    w: World, headers: dict[str, str], presign_path: str, prefix: str, method: str
) -> str:
    """A key issued to the caller and present in storage, so ``confirm`` reaches its success."""
    r = await w.client.request(
        method,
        presign_path,
        headers=headers,
        json={"content_type": "image/png", "content_length": 10},
    )
    if r.status_code != 200:
        # The role may not presign at all (its confirm is refused before the key matters).
        return f"{prefix}/{uuid4()}"
    key = str(r.json()["key"])
    w.storage.objects[key] = (b"x" * 10, "image/png")
    return key


async def minimal_body(
    w: World, method: str, path: str, role: str, variant: str, headers: dict[str, str]
) -> Any:
    """The smallest body that passes validation, so authorization decides the status."""
    key = (method, path.removeprefix(API))
    match key:
        case ("POST", "/auth/login"):
            return {"email": w.emails["trainer_a"], "password": "Correct-Horse-Battery-9"}
        case ("POST", "/auth/refresh"):
            return {"refresh_token": w.refresh["trainer"]}
        case ("POST", "/auth/logout"):
            return {"refresh_token": w.refresh.get(role, "x" * 24)}
        case ("POST", "/auth/password-reset/request"):
            return {"email": "nobody@example.com"}
        case ("POST", "/auth/password-reset/confirm" | "/auth/password/setup"):
            return {"token": "t" * 24, "new_password": "Correct-Horse-Battery-9"}
        case ("POST", "/auth/verify-email" | "/me/email-change/confirm"):
            return {"token": "t" * 24}
        case ("POST", "/auth/register"):
            return {
                "email": f"new-{role}@example.com",
                "password": "Correct-Horse-Battery-9",
                "first_name": "New",
                "last_name": "Person",
                "birth_date": "1990-01-01",
            }
        case ("PATCH", "/me"):
            return {"first_name": "Renamed"}
        case ("PUT", "/me/avatar"):
            return {"content_type": "image/png", "content_length": 10}
        case ("POST", "/me/avatar/confirm"):
            return {
                "avatar_key": await _issued_key(w, headers, f"{API}/me/avatar", "avatars", "PUT")
            }
        case ("POST", "/me/email-change"):
            return {
                "new_email": f"changed-{role}@example.com",
                "password": "Correct-Horse-Battery-9",
            }
        case ("PUT", "/me/availability" | "/me/players/{player_id}/availability"):
            return {"timezone": "UTC", "windows": []}
        case ("POST", "/me/players"):
            return {"display_name": "Kid", "birth_date": "2015-03-01", "gender": "undisclosed"}
        case ("PATCH", "/me/players/{player_id}"):
            return {"display_name": "Renamed"}
        case ("POST", "/me/players/{player_id}/trainers"):
            return {"trainer_id": str(w.ids["extra_trainer"])}
        case ("POST", "/me/approvals/{approval_id}/deny"):
            return {}
        case ("POST", "/trainer/share-links"):
            if variant == "coach_unique":
                return {"kind": "coach_unique", "target_email": "coach-new@example.com"}
            return {"kind": "player_static"}
        case ("PATCH", "/trainer/branding"):
            return {"business_name": "Renamed Academy"}
        case ("POST", "/trainer/branding/logo"):
            return {"content_type": "image/png", "content_length": 10}
        case ("POST", "/trainer/branding/logo/confirm"):
            key_ = await _issued_key(w, headers, f"{API}/trainer/branding/logo", "logos", "POST")
            return {"logo_key": key_}
        case ("POST", "/admin/users/trainers"):
            return {
                "email": "provisioned@example.com",
                "first_name": "Pro",
                "last_name": "Visioned",
                "business_name": "Provisioned Academy",
                "slug": "provisioned-academy",
            }
        case ("PATCH", "/admin/users/{user_id}"):
            return {"first_name": "Renamed"}
    return None


def _headers(w: World, method: str, path: str, role: str) -> dict[str, str]:
    headers = dict(w.headers[role])
    if path.endswith(("/approve", "/deny")):
        headers["Idempotency-Key"] = f"perm-{role}"
    return headers


async def build(
    w: World, method: str, path: str, role: str, variant: str, ids: dict[str, UUID | str]
) -> Request:
    template = path
    url = fill(template, ids)
    if (query := QUERY.get((method, template))) is not None:
        values = dict(ids)
        values["share_code"] = ids.get("code", w.b.share_code)
        url = f"{url}?{fill(query, values)}"
    headers = _headers(w, method, path, role)
    body = await minimal_body(w, method, path, role, variant, headers)
    return Request(method, url, headers, body)
