import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import anyio.to_thread
import boto3
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


@dataclass(frozen=True)
class PresignedPut:
    url: str
    headers: dict[str, str]
    expires_in: int


@dataclass(frozen=True)
class ObjectMeta:
    content_type: str
    size: int


class ObjectStorage(Protocol):
    async def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> PresignedPut: ...

    async def head(self, key: str) -> ObjectMeta | None: ...

    async def get(self, key: str) -> bytes: ...

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...


class S3ObjectStorage:
    """boto3 adapter. Blocking calls run in worker threads."""

    def __init__(self, bucket: str, client: "S3Client | None" = None) -> None:
        self._bucket = bucket
        self._client_or_none = client
        self._client_lock = threading.Lock()

    @property
    def _client(self) -> "S3Client":
        # Created on first use, inside the worker thread: boto3's credential chain can block for
        # seconds (EC2 metadata probe) and must not run at app construction.
        if self._client_or_none is None:
            with self._client_lock:
                if self._client_or_none is None:
                    self._client_or_none = boto3.client("s3")
        return self._client_or_none

    async def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> PresignedPut:
        def _sign() -> str:
            return self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "ContentLength": content_length,
                },
                ExpiresIn=expires_in,
            )

        url = await anyio.to_thread.run_sync(_sign)
        return PresignedPut(url=url, headers={"Content-Type": content_type}, expires_in=expires_in)

    async def head(self, key: str) -> ObjectMeta | None:
        def _head() -> ObjectMeta | None:
            try:
                res = self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            return ObjectMeta(
                content_type=res.get("ContentType", "application/octet-stream"),
                size=res.get("ContentLength", 0),
            )

        return await anyio.to_thread.run_sync(_head)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()

        return await anyio.to_thread.run_sync(_get)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        await anyio.to_thread.run_sync(_put)
