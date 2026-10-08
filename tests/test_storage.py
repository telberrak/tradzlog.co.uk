import asyncio
from io import BytesIO
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

import tradzlog_web.main as web_main
from tradzlog_api.services import storage
from tradzlog_db.models import Attachment, Trade, User

pytestmark = pytest.mark.usefixtures("signed_in")

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> None:
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803 - boto3's argument names
        self.objects.pop((Bucket, Key), None)

    def generate_presigned_url(self, operation: str, Params: dict[str, str], ExpiresIn: int) -> str:  # noqa: N803
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?op={operation}&expires={ExpiresIn}"


def test_sniff_image_type_trusts_bytes_not_names() -> None:
    assert storage.sniff_image_type(PNG) == "image/png"
    assert storage.sniff_image_type(JPEG) == "image/jpeg"
    assert storage.sniff_image_type(WEBP) == "image/webp"
    assert storage.sniff_image_type(b"<html><script>alert(1)</script>") is None
    assert storage.sniff_image_type(b"RIFF\x00\x00\x00\x00WAVE") is None


def test_s3_storage_writes_private_encrypted_objects_per_user() -> None:
    client = FakeS3()
    s3 = storage.S3Storage("tradzlog-uploads", "prod/", client=client)
    ref = s3.save("user-1", PNG, "image/png")

    bucket, key = storage.split_s3_ref(ref)
    assert bucket == "tradzlog-uploads"
    assert key.startswith("prod/attachments/user-1/") and key.endswith(".png")
    stored = client.objects[(bucket, key)]
    assert stored["ContentType"] == "image/png"
    assert stored["ServerSideEncryption"] == "AES256"
    assert "ACL" not in stored  # never public

    url = storage.display_url(ref, client=client)
    assert url.startswith("https://tradzlog-uploads.s3.amazonaws.com/prod/attachments/user-1/")
    assert "op=get_object" in url

    s3.delete(ref)
    assert client.objects == {}


def test_local_storage_round_trip(tmp_path) -> None:
    local = storage.LocalStorage(tmp_path)
    ref = local.save("user-1", JPEG, "image/jpeg")
    assert ref.startswith("local:attachments/user-1/") and ref.endswith(".jpg")
    assert (tmp_path / ref.removeprefix("local:")).read_bytes() == JPEG
    assert storage.display_url(ref) == "/uploads-local/" + ref.removeprefix("local:")
    assert storage.display_url("/uploads-local/legacy.png") == "/uploads-local/legacy.png"
    local.delete(ref)
    assert not (tmp_path / ref.removeprefix("local:")).exists()


class UploadSession:
    def __init__(self, user: User, trade: Trade | None) -> None:
        self.results: list[object | None] = [user, trade]
        self.added: list[object] = []
        self.commits = 0

    def scalar(self, statement: Any) -> object | None:
        del statement
        return self.results.pop(0) if self.results else None

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        pass


def upload(data: bytes, content_type: str, name: str = "chart.png") -> UploadFile:
    return UploadFile(BytesIO(data), filename=name, headers=Headers({"content-type": content_type}))


def run_upload(monkeypatch: pytest.MonkeyPatch, file: UploadFile, trade_id: str = "trade-1") -> tuple[UploadSession, FakeS3]:
    session = UploadSession(User(id="user-1", email="t@example.com"), Trade(id="trade-1", user_id="user-1"))
    client = FakeS3()
    monkeypatch.setattr(web_main, "SessionLocal", lambda: session)
    monkeypatch.setattr(storage, "get_storage", lambda: storage.S3Storage("bucket", client=client))
    asyncio.run(web_main.upload_attachment(file=file, trade_id=trade_id, journal_entry_id=""))
    return session, client


def test_upload_stores_screenshot_in_s3_and_records_attachment(monkeypatch) -> None:
    session, client = run_upload(monkeypatch, upload(PNG, "image/png"))
    attachment = next(item for item in session.added if isinstance(item, Attachment))
    assert attachment.url.startswith("s3://bucket/attachments/user-1/")
    assert attachment.mime_type == "image/png" and attachment.file_size == len(PNG)
    assert len(client.objects) == 1 and session.commits == 1


def test_upload_rejects_disguised_files_and_unlinked_uploads(monkeypatch) -> None:
    with pytest.raises(HTTPException, match="PNG, JPEG or WebP"):
        run_upload(monkeypatch, upload(b"<html>evil</html>", "image/png"))
    with pytest.raises(HTTPException, match="trade or a journal"):
        run_upload(monkeypatch, upload(PNG, "image/png"), trade_id="")
    monkeypatch.setattr(web_main, "MAX_UPLOAD_BYTES", 16)
    with pytest.raises(HTTPException, match="too large"):
        run_upload(monkeypatch, upload(PNG, "image/png"))
