from collections.abc import Iterator
from typing import TYPE_CHECKING

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.core.storage import ObjectMeta, S3ObjectStorage

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@pytest.fixture
def s3() -> Iterator[tuple["S3Client", Stubber]]:
    client = boto3.client(
        "s3", region_name="us-east-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    with Stubber(client) as stubber:
        yield client, stubber


async def test_head_returns_none_on_404(s3: tuple["S3Client", Stubber]) -> None:
    client, stubber = s3
    stubber.add_client_error("head_object", service_error_code="404", http_status_code=404)
    assert await S3ObjectStorage("b", client=client).head("k") is None


async def test_head_returns_meta(s3: tuple["S3Client", Stubber]) -> None:
    client, stubber = s3
    stubber.add_response("head_object", {"ContentType": "image/svg+xml", "ContentLength": 7})
    meta = await S3ObjectStorage("b", client=client).head("k")
    assert meta == ObjectMeta(content_type="image/svg+xml", size=7)


async def test_head_reraises_other_errors(s3: tuple["S3Client", Stubber]) -> None:
    client, stubber = s3
    stubber.add_client_error("head_object", service_error_code="500", http_status_code=500)
    with pytest.raises(ClientError):
        await S3ObjectStorage("b", client=client).head("k")


def test_client_is_not_created_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    # boto3.client probes credentials (EC2 metadata, ~2 s off AWS); create_app must not pay that.
    def _fail(*_a: object, **_k: object) -> None:
        raise AssertionError("boto3.client called at construction")

    monkeypatch.setattr(boto3, "client", _fail)
    S3ObjectStorage("b")


async def test_delete_removes_the_key_and_a_missing_key_is_still_success(
    s3: tuple["S3Client", Stubber],
) -> None:
    client, stubber = s3
    # S3 answers 204 for a key that does not exist, so a second delete is a no-op success.
    stubber.add_response("delete_object", {}, {"Bucket": "b", "Key": "k"})
    stubber.add_response("delete_object", {}, {"Bucket": "b", "Key": "k"})
    storage = S3ObjectStorage("b", client=client)
    await storage.delete("k")
    await storage.delete("k")
    stubber.assert_no_pending_responses()


async def test_delete_reraises_real_errors(s3: tuple["S3Client", Stubber]) -> None:
    client, stubber = s3
    stubber.add_client_error("delete_object", service_error_code="500", http_status_code=500)
    with pytest.raises(ClientError):
        await S3ObjectStorage("b", client=client).delete("k")
