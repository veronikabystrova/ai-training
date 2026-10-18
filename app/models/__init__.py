"""ORM models. Import every module here so ``Base.metadata`` is complete for Alembic."""

from app.models import identity, platform

__all__ = ["identity", "platform"]
