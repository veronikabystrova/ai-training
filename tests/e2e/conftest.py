"""Fixtures for the cross-cutting sweeps: the shared app/client fixtures of ``tests/api``."""

from tests.api.admin.conftest import admin, admin_token  # noqa: F401
from tests.api.conftest import app, client  # noqa: F401
