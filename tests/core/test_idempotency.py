from app.core.idempotency import request_hash


def test_request_hash_stable_across_key_order() -> None:
    assert request_hash("POST", "/x", {"a": 1, "b": 2}) == request_hash(
        "POST", "/x", {"b": 2, "a": 1}
    )
    assert request_hash("POST", "/x", {"a": 1}) != request_hash("POST", "/x", {"a": 2})
