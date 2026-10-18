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
