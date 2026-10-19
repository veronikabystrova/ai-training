import inspect

from app.repositories.approval import PurchaseApprovalRepository
from app.repositories.share_link import ShareLinkRepository
from app.repositories.trainer_player_link import TrainerPlayerLinkRepository

# Documented tenancy exceptions: the share-link code is the capability; the approval lock is
# scoped to the parent; the expiry sweep is a system job across tenants; the batch link check
# takes the trainer ids to test (tenant-scoped by the ids it is given, owner-scoped by the caller).
_EXCEPTIONS = {
    "lock_by_code",
    "lock_by_id_for_parent",
    "expire_due",
    "active_trainer_ids_for_owner",
}


def test_tenant_scoped_methods_require_keyword_only_trainer_id() -> None:
    checked = 0
    for cls in (TrainerPlayerLinkRepository, ShareLinkRepository, PurchaseApprovalRepository):
        for name, fn in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_") or name in _EXCEPTIONS:
                continue
            p = inspect.signature(fn).parameters
            assert "trainer_id" in p, f"{cls.__name__}.{name}"
            assert p["trainer_id"].kind is inspect.Parameter.KEYWORD_ONLY, f"{cls.__name__}.{name}"
            assert p["trainer_id"].default is inspect.Parameter.empty, f"{cls.__name__}.{name}"
            checked += 1
    assert checked >= 8


def test_parent_scoped_lock_requires_keyword_only_parent_user_id() -> None:
    p = inspect.signature(PurchaseApprovalRepository.lock_by_id_for_parent).parameters
    assert p["parent_user_id"].kind is inspect.Parameter.KEYWORD_ONLY
