"""File storage for uploaded screenshots: private S3 in production, local disk in development.

An upload is stored once and referenced by an opaque string kept in ``Attachment.url``:

- ``s3://<bucket>/<key>``: private S3 object, shown through a short-lived presigned URL
- ``local:<key>``: file under ``var/uploads``, served by the web app at ``/uploads-local/<key>``
- ``/uploads-local/<name>``: older local uploads, served as they are

Use :func:`display_url` whenever a reference is rendered, never the raw value.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from tradzlog_api.config import settings

LOCAL_ROOT = Path("var/uploads")
LOCAL_URL_PREFIX = "/uploads-local/"

IMAGE_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def sniff_image_type(data: bytes) -> str | None:
    """The image type from the file's own bytes; the browser-supplied content type is not trusted."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class Storage(Protocol):
    def save(self, owner_id: str, data: bytes, content_type: str) -> str: ...

    def delete(self, ref: str) -> None: ...


def object_key(prefix: str, owner_id: str, content_type: str) -> str:
    # Grouped per user so a whole account's files can be listed, exported or deleted together.
    return f"{prefix}attachments/{owner_id}/{uuid4().hex}{IMAGE_EXTENSIONS[content_type]}"


class LocalStorage:
    def __init__(self, root: Path = LOCAL_ROOT) -> None:
        self.root = root

    def save(self, owner_id: str, data: bytes, content_type: str) -> str:
        key = object_key("", owner_id, content_type)
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"local:{key}"

    def delete(self, ref: str) -> None:
        if ref.startswith("local:"):
            (self.root / ref.removeprefix("local:")).unlink(missing_ok=True)
        elif ref.startswith(LOCAL_URL_PREFIX):
            (self.root / ref.removeprefix(LOCAL_URL_PREFIX)).unlink(missing_ok=True)


class S3Storage:
    def __init__(self, bucket: str, prefix: str = "", client: Any | None = None) -> None:
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = s3_client()
        return self._client

    def save(self, owner_id: str, data: bytes, content_type: str) -> str:
        key = object_key(self.prefix, owner_id, content_type)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ContentDisposition="inline",
            CacheControl="private, max-age=3600",
            ServerSideEncryption="AES256",
        )
        return f"s3://{self.bucket}/{key}"

    def delete(self, ref: str) -> None:
        bucket, key = split_s3_ref(ref)
        self.client.delete_object(Bucket=bucket, Key=key)


def split_s3_ref(ref: str) -> tuple[str, str]:
    bucket, _, key = ref.removeprefix("s3://").partition("/")
    return bucket, key


@lru_cache(maxsize=1)
def s3_client() -> Any:
    import boto3
    from botocore.config import Config

    # Credentials come from the default chain: the EC2 instance role in production.
    return boto3.client(
        "s3",
        region_name=settings.s3_region,
        endpoint_url=settings.s3_endpoint_url,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        return S3Storage(settings.s3_bucket, settings.s3_prefix)
    return LocalStorage()


def display_url(ref: str, client: Any | None = None) -> str:
    """A URL the browser can load for a stored reference. S3 objects stay private."""
    if ref.startswith("s3://"):
        bucket, key = split_s3_ref(ref)
        return (client or s3_client()).generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=settings.s3_url_ttl_seconds,
        )
    if ref.startswith("local:"):
        return LOCAL_URL_PREFIX + ref.removeprefix("local:")
    return ref
