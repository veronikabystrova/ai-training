"""Merge gate: another tenant's ids are indistinguishable from missing ones (404, never 403)."""

import pytest

from tests.permissions.build import build, other_ids
from tests.permissions.conftest import MODULE_LOOP, World
from tests.permissions.matrix import TENANT_SCOPED, OtherTenantCell

pytestmark = [MODULE_LOOP, pytest.mark.gate]


@pytest.mark.parametrize("spec", TENANT_SCOPED, ids=[c.id for c in TENANT_SCOPED])
async def test_other_tenants_ids_return_404(spec: OtherTenantCell, cell_world: World) -> None:
    w = cell_world
    req = await build(w, spec.method, spec.path, spec.role, "", other_ids(w, spec.role))
    r = await w.client.request(req.method, req.url, headers=req.headers, json=req.json)
    assert r.status_code == 404, r.text
