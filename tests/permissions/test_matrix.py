"""Every registered /api/v1 route is covered, and every cell answers as the matrix says."""

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from tests.permissions.build import build, own_ids
from tests.permissions.conftest import MODULE_LOOP, World
from tests.permissions.matrix import (
    API,
    MATRIX,
    ROLES,
    TENANT_SCOPED,
    Cell,
    OtherTenantCell,
    is_tenant_scoped,
)

pytestmark = MODULE_LOOP


def registered_routes(app: FastAPI) -> set[tuple[str, str]]:
    """``(method, path)`` of every versioned route, read from the OpenAPI document."""
    paths: dict[str, dict[str, object]] = app.openapi()["paths"]
    return {
        (method.upper(), path)
        for path, ops in paths.items()
        if path.startswith(API)
        for method in ops
        if method.upper() != "HEAD"
    }


def test_every_registered_route_is_in_the_matrix() -> None:
    app = create_app(
        Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            redis_url="redis://localhost:6379",
            jwt_secret="t" * 32,
            hash_salt="s" * 16,
        )
    )
    registered = registered_routes(app)
    assert len(registered) >= 48  # guards against the discovery itself going blind
    covered = {(c.method, c.path) for c in MATRIX}
    assert registered - covered == set(), (
        f"routes missing from the matrix: {sorted(registered - covered)}"
    )
    assert covered - registered == set(), (
        f"matrix rows for unknown routes: {sorted(covered - registered)}"
    )


def test_every_route_has_a_cell_for_every_role() -> None:
    cells = {(c.method, c.path, c.role) for c in MATRIX}
    missing = {(m, p, r) for m, p in {(c.method, c.path) for c in MATRIX} for r in ROLES} - cells
    assert missing == set()


def test_every_tenant_scoped_route_has_a_cross_tenant_pair() -> None:
    paired: set[tuple[str, str]] = {(c.method, c.path) for c in TENANT_SCOPED}
    needed = {(c.method, c.path) for c in MATRIX if is_tenant_scoped(c.method, c.path)}
    assert needed - paired == set(), f"no other-tenant 404 case for: {sorted(needed - paired)}"
    assert paired - needed == set()


def test_cross_tenant_roles_are_allowed_in_the_matrix() -> None:
    """A 404 pair only means something where the own-tenant cell is a success."""
    ok = {(c.method, c.path, c.role) for c in MATRIX if c.expected < 300}
    bad: list[OtherTenantCell] = [
        c for c in TENANT_SCOPED if (c.method, c.path, c.role) not in ok and not (c.role == "child")
    ]
    assert bad == []


@pytest.mark.parametrize("spec", MATRIX, ids=[c.id for c in MATRIX])
async def test_matrix_cell(spec: Cell, cell_world: World) -> None:
    w = cell_world
    req = await build(
        w, spec.method, spec.path, spec.role, spec.variant, own_ids(w, spec.method, spec.path)
    )
    r = await w.client.request(req.method, req.url, headers=req.headers, json=req.json)
    assert r.status_code == spec.expected, r.text
