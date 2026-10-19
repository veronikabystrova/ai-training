"""The permission matrix as data (spec "Permission summary" + per-endpoint notes).

One ``Cell`` per ``(route, role)``. Rules, in this order:

* a role the route allows gets the route's success status (``ok``) or its listed override;
* ``anonymous`` on a protected route gets 401;
* every other role gets 403 (role gates raise 403, never 404, see ``deps.py``).

``test_matrix.py`` fails if a registered ``/api/v1`` route has no cell here.
"""

from dataclasses import dataclass
from uuid import UUID

ROLES = (
    "anonymous",
    "admin",
    "trainer",
    "coach",
    "parent",
    "child",
    "imp_trainer",
    "imp_parent",
)
API = "/api/v1"

AUTHENTICATED = frozenset(ROLES) - {"anonymous"}
EVERYONE = frozenset(ROLES)
STAFF = frozenset({"trainer", "coach", "imp_trainer"})
TRAINER = frozenset({"trainer", "imp_trainer"})
PARENT = frozenset({"parent", "imp_parent"})
PLAYER_ANY = frozenset({"parent", "child", "imp_parent"})
AVAILABILITY = frozenset({"coach", "parent", "child", "imp_parent"})
ADMIN = frozenset({"admin"})
IMPERSONATING = frozenset({"imp_trainer", "imp_parent"})


@dataclass(frozen=True)
class Cell:
    method: str
    path: str  # template as registered, e.g. /api/v1/me/players/{player_id}
    role: str
    expected: int
    variant: str = ""  # selects an alternative body (e.g. coach_unique share link)

    @property
    def id(self) -> str:
        who = f"{self.role}/{self.variant}" if self.variant else self.role
        return f"{self.method} {self.path.removeprefix(API)} [{who}]"


def _cells(
    method: str,
    path: str,
    ok: int,
    allowed: frozenset[str],
    *,
    overrides: dict[str, int] | None = None,
    variant: str = "",
) -> list[Cell]:
    out: list[Cell] = []
    for role in ROLES:
        if role in allowed:
            status = ok
        elif role == "anonymous":
            status = 401
        else:
            status = 403
        status = (overrides or {}).get(role, status)
        out.append(Cell(method, f"{API}{path}", role, status, variant))
    return out


# Public routes answer the same for everyone, so a bad body is allowed to decide: the token
# routes consume a one-time token we do not hold, hence 400 TOKEN_INVALID, never 401/403.
_AUTH: list[Cell] = [
    *_cells("POST", "/auth/login", 200, EVERYONE),
    *_cells("POST", "/auth/refresh", 200, EVERYONE),
    *_cells("POST", "/auth/logout", 204, AUTHENTICATED),
    *_cells("POST", "/auth/password-reset/request", 202, EVERYONE),
    *_cells("POST", "/auth/password-reset/confirm", 400, EVERYONE),
    *_cells("POST", "/auth/verify-email", 400, EVERYONE),
    # Seeded users are already verified.
    *_cells("POST", "/auth/verify-email/resend", 200, AUTHENTICATED),
    *_cells("POST", "/auth/password/setup", 400, EVERYONE),
    # Register is public; a bearer token turns it into "redeem as this user" (200 linked for a
    # parent). A player link cannot be redeemed by staff or an admin (409 WRONG_LINK_KIND, a
    # business rule, not a role gate) and children are barred (403 CHILD_CANNOT_REDEEM).
    *_cells(
        "POST",
        "/auth/register",
        201,
        EVERYONE,
        overrides={
            "admin": 409,
            "trainer": 409,
            "coach": 409,
            "imp_trainer": 409,
            "parent": 200,
            "imp_parent": 200,
            "child": 403,
        },
    ),
    *_cells("POST", "/auth/impersonation/exit", 204, IMPERSONATING),
]

_ME: list[Cell] = [
    *_cells("GET", "/me", 200, AUTHENTICATED),
    *_cells("PATCH", "/me", 200, AUTHENTICATED),
    *_cells("PUT", "/me/avatar", 200, AUTHENTICATED),
    *_cells("POST", "/me/avatar/confirm", 200, AUTHENTICATED),
    *_cells("POST", "/me/email-change", 202, AUTHENTICATED),
    *_cells("POST", "/me/email-change/confirm", 400, EVERYONE),
    *_cells("GET", "/me/availability", 200, AVAILABILITY),
    *_cells("PUT", "/me/availability", 200, AVAILABILITY),
    *_cells("GET", "/me/contexts", 200, PARENT),
    *_cells("GET", "/me/players", 200, PLAYER_ANY),
    *_cells("POST", "/me/players", 201, PARENT),
    *_cells("GET", "/me/players/{player_id}", 200, PLAYER_ANY),
    *_cells("PATCH", "/me/players/{player_id}", 200, PARENT),
    *_cells("DELETE", "/me/players/{player_id}", 204, PARENT),
    *_cells("PUT", "/me/players/{player_id}/availability", 200, PARENT),
    *_cells("POST", "/me/players/{player_id}/trainers", 201, PARENT),
    *_cells("DELETE", "/me/players/{player_id}/trainers/{trainer_id}", 204, PARENT),
    *_cells("GET", "/me/approvals", 200, PARENT),
    *_cells("POST", "/me/approvals/{approval_id}/approve", 200, PARENT),
    *_cells("POST", "/me/approvals/{approval_id}/deny", 200, PARENT),
]

_TRAINER: list[Cell] = [
    *_cells("POST", "/trainer/share-links", 201, STAFF),
    # A coach may only create player links: coach_unique is trainer-only (spec row 1).
    *_cells("POST", "/trainer/share-links", 201, TRAINER, variant="coach_unique"),
    *_cells("GET", "/trainer/share-links", 200, STAFF),
    *_cells("DELETE", "/trainer/share-links/{share_link_id}", 204, TRAINER),
    *_cells("GET", "/trainer/players/{player_profile_id}/availability", 200, STAFF),
    # Ambiguous in the spec ("read only" for coaches). Safer reading: a coach cannot read
    # another coach's availability, so 403 (matches the route's trainer-only gate).
    *_cells("GET", "/trainer/coaches/{coach_user_id}/availability", 200, TRAINER),
    *_cells("GET", "/trainer/branding", 200, STAFF),
    *_cells("PATCH", "/trainer/branding", 200, TRAINER),
    *_cells("POST", "/trainer/branding/logo", 200, TRAINER),
    *_cells("POST", "/trainer/branding/logo/confirm", 200, TRAINER),
]

_PUBLIC: list[Cell] = [
    *_cells("GET", "/public/share-links/{code}", 200, EVERYONE),
    *_cells("GET", "/public/trainers/{slug}/branding", 200, EVERYONE),
]

# Admin: super_admin only, and not while impersonating (an impersonating token carries the
# target's role, so it is 403 here either way).
_ADMIN: list[Cell] = [
    *_cells("GET", "/admin/users", 200, ADMIN),
    *_cells("POST", "/admin/users/trainers", 201, ADMIN),
    *_cells("GET", "/admin/users/{user_id}", 200, ADMIN),
    *_cells("PATCH", "/admin/users/{user_id}", 200, ADMIN),
    *_cells("DELETE", "/admin/users/{user_id}", 204, ADMIN),
    *_cells("POST", "/admin/users/{user_id}/deactivate", 200, ADMIN),
    *_cells("POST", "/admin/users/{user_id}/reactivate", 200, ADMIN),
    *_cells("POST", "/admin/users/{user_id}/impersonate", 200, ADMIN),
]

MATRIX: list[Cell] = [*_AUTH, *_ME, *_TRAINER, *_PUBLIC, *_ADMIN]


# --- cross-tenant (paired "other tenant's ids -> 404") -----------------------------------


@dataclass(frozen=True)
class OtherTenantCell:
    method: str
    path: str
    role: str  # a caller of tenant A

    @property
    def id(self) -> str:
        return f"{self.method} {self.path.removeprefix(API)} [{self.role}]"


def _other(method: str, path: str, *roles: str) -> list[OtherTenantCell]:
    return [OtherTenantCell(method, f"{API}{path}", role) for role in roles]


TENANT_SCOPED: list[OtherTenantCell] = [
    *_other("DELETE", "/trainer/share-links/{share_link_id}", "trainer", "imp_trainer"),
    *_other(
        "GET",
        "/trainer/players/{player_profile_id}/availability",
        "trainer",
        "coach",
        "imp_trainer",
    ),
    *_other("GET", "/trainer/coaches/{coach_user_id}/availability", "trainer", "imp_trainer"),
    *_other("GET", "/me/players/{player_id}", "parent", "child", "imp_parent"),
    *_other("PATCH", "/me/players/{player_id}", "parent"),
    *_other("DELETE", "/me/players/{player_id}", "parent"),
    *_other("PUT", "/me/players/{player_id}/availability", "parent"),
    *_other("POST", "/me/players/{player_id}/trainers", "parent"),
    *_other("DELETE", "/me/players/{player_id}/trainers/{trainer_id}", "parent"),
    *_other("GET", "/me/approvals", "parent", "imp_parent"),
    *_other("POST", "/me/approvals/{approval_id}/approve", "parent"),
    *_other("POST", "/me/approvals/{approval_id}/deny", "parent"),
]

# Routes whose ids are not tenant data: the admin directory spans every tenant by design.
CROSS_TENANT_EXEMPT_PREFIXES = (f"{API}/admin/", f"{API}/public/")


def is_tenant_scoped(method: str, path: str) -> bool:
    """A route that takes an id (or the ``trainer_id`` context) and so needs a 404 pair."""
    if path.startswith(CROSS_TENANT_EXEMPT_PREFIXES):
        return False
    return "{" in path or path == f"{API}/me/approvals"


# --- request building ---------------------------------------------------------------------

# Query string per route (kept out of the path so the template still matches the router).
QUERY: dict[tuple[str, str], str] = {
    ("GET", f"{API}/me/approvals"): "trainer_id={trainer_id}",
    ("POST", f"{API}/auth/register"): "share_code={share_code}",
}


def fill(template: str, values: dict[str, UUID | str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out
