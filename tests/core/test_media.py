from uuid import UUID, uuid4

from app.core.media import issue_bound_key, key_is_bound_to

SECRET = b"s" * 32


def test_bound_key_validates_only_for_its_owner_and_purpose() -> None:
    owner = uuid4()
    key = issue_bound_key(SECRET, prefix="logos", purpose="logo", owner_id=owner)
    assert key.startswith("logos/") and len(key) == len("logos/") + 36
    assert key_is_bound_to(SECRET, prefix="logos", purpose="logo", owner_id=owner, key=key)
    assert not key_is_bound_to(SECRET, prefix="logos", purpose="logo", owner_id=uuid4(), key=key)
    assert not key_is_bound_to(SECRET, prefix="logos", purpose="avatar", owner_id=owner, key=key)
    assert not key_is_bound_to(b"o" * 32, prefix="logos", purpose="logo", owner_id=owner, key=key)


def test_bound_key_rejects_forged_and_malformed_keys() -> None:
    owner = uuid4()
    key = issue_bound_key(SECRET, prefix="logos", purpose="logo", owner_id=owner)
    for bad in (
        f"logos/{uuid4()}",
        "logos/not-a-uuid",
        "logos/",
        key.replace("logos/", "avatars/"),
        key.upper(),
        key + "x",
    ):
        assert not key_is_bound_to(SECRET, prefix="logos", purpose="logo", owner_id=owner, key=bad)
    assert UUID(key.removeprefix("logos/"))
