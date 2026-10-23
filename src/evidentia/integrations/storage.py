"""Object storage. Clients upload straight to the bucket via short-lived signed
POST policies, so large files never pass through the API servers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from evidentia.core.config import StorageSettings


@dataclass(frozen=True, slots=True)
class SignedUpload:
    url: str
    fields: dict[str, str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredObject:
    size_bytes: int
    content_type: str | None


class ObjectStorage(Protocol):
    async def sign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_s: int
    ) -> SignedUpload: ...

    async def stat(self, key: str) -> StoredObject | None: ...

    async def read(self, key: str) -> bytes: ...


class S3Storage:
    """Any S3-compatible store (AWS S3, MinIO, Cloudflare R2, GCS interop)."""

    def __init__(self, config: StorageSettings) -> None:
        self._bucket = config.bucket
        options: dict[str, Any] = {
            "region_name": config.region,
            "aws_access_key_id": config.access_key_id,
            "aws_secret_access_key": (
                config.secret_access_key.get_secret_value() if config.secret_access_key else None
            ),
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        }
        self._client = boto3.client("s3", endpoint_url=config.endpoint_url, **options)
        # Signed URLs must use the host the browser can reach (differs inside Docker).
        self._signer = boto3.client(
            "s3", endpoint_url=config.public_endpoint_url or config.endpoint_url, **options
        )

    async def sign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_s: int
    ) -> SignedUpload:
        signed = await asyncio.to_thread(
            self._signer.generate_presigned_post,
            Bucket=self._bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=expires_s,
        )
        return SignedUpload(
            url=signed["url"],
            fields=dict(signed["fields"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_s),
        )

    async def stat(self, key: str) -> StoredObject | None:
        try:
            head = await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return StoredObject(
            size_bytes=int(head["ContentLength"]), content_type=head.get("ContentType")
        )

    async def read(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body


class InMemoryStorage:
    """Process-local store for tests and offline development."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/pdf") -> None:
        self.objects[key] = (data, content_type)

    async def sign_upload(
        self, key: str, *, content_type: str, max_bytes: int, expires_s: int
    ) -> SignedUpload:
        return SignedUpload(
            url=f"memory://uploads/{key}",
            fields={"key": key, "Content-Type": content_type},
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_s),
        )

    async def stat(self, key: str) -> StoredObject | None:
        if key not in self.objects:
            return None
        data, content_type = self.objects[key]
        return StoredObject(size_bytes=len(data), content_type=content_type)

    async def read(self, key: str) -> bytes:
        return self.objects[key][0]


def build_storage(config: StorageSettings) -> ObjectStorage:
    if config.backend == "memory":
        return InMemoryStorage()
    return S3Storage(config)
