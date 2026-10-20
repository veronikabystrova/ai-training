"""Merge gate: another tenant's ids are indistinguishable from missing ones (404, never 403)."""

from collections.abc import Callable
from uuid import UUID

import pytest

from tests.permissions.build import build, other_ids
from tests.permissions.conftest import MODULE_LOOP, World
from tests.permissions.matrix import API, TENANT_SCOPED, OtherTenantCell

pytestmark = [MODULE_LOOP, pytest.mark.gate]


@pytest.mark.parametrize("spec", TENANT_SCOPED, ids=[c.id for c in TENANT_SCOPED])
async def test_other_tenants_ids_return_404(spec: OtherTenantCell, cell_world: World) -> None:
    w = cell_world
    req = await build(w, spec.method, spec.path, spec.role, "", other_ids(w, spec.role))
    r = await w.client.request(req.method, req.url, headers=req.headers, json=req.json)
    assert r.status_code == 404, r.text


def _tenant_b_markers(w: World) -> set[str]:
    b = w.b
    return {
        str(value)
        for value in (
            b.trainer,
            b.coach,
            b.parent,
            b.player,
            b.share_link,
            b.approval,
            b.share_code,
            b.slug,
        )
    }


# (role headers, list path, a tenant A id that must be in the body: guards a vacuous pass)
LIST_ROUTES = [
    ("trainer", f"{API}/trainer/share-links", lambda w: w.a.share_link),
    ("coach", f"{API}/trainer/share-links", lambda w: w.a.share_link),
    ("parent", f"{API}/me/players", lambda w: w.a.player),
    ("parent", f"{API}/me/approvals?trainer_id=", lambda w: w.a.approval),
]


@pytest.mark.parametrize("role,path,own", LIST_ROUTES, ids=[f"{r}-{p}" for r, p, _ in LIST_ROUTES])
async def test_list_routes_never_return_another_tenants_rows(
    role: str, path: str, own: Callable[[World], UUID], cell_world: World
) -> None:
    w = cell_world
    url = f"{path}{w.a.trainer}" if path.endswith("trainer_id=") else path
    r = await w.client.get(url, headers=w.headers[role])
    assert r.status_code == 200, r.text
    assert str(own(w)) in r.text  # the caller's own data is listed, so the check is not vacuous
    leaked = {marker for marker in _tenant_b_markers(w) if marker in r.text}
    assert leaked == set(), f"{url} leaked tenant B data: {sorted(leaked)}"
