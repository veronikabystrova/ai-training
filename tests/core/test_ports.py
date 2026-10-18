from tests.fakes import FakeEmailSender, FakeObjectStorage


async def test_fake_email_captures_without_logging() -> None:
    sender = FakeEmailSender()
    await sender.send(to="a@b.co", template="password_reset", context={"token": "t"})
    assert [(m.to, m.template, m.context) for m in sender.sent] == [
        ("a@b.co", "password_reset", {"token": "t"})
    ]


async def test_fake_storage_put_head_get_presign() -> None:
    store = FakeObjectStorage()
    assert await store.head("k") is None
    await store.put("k", b"abc", "image/png")
    meta = await store.head("k")
    assert meta is not None
    assert (meta.content_type, meta.size) == ("image/png", 3)
    assert await store.get("k") == b"abc"
    presigned = await store.presign_put("k2", "image/png", 10, 300)
    assert "k2" in presigned.url
