from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient

import tradzlog_api.main as api_main
from tradzlog_api.deps import current_user, db_session
from tradzlog_api.main import app
from tradzlog_db.models import Attachment, User


class EmptyScalars:
    def all(self) -> list[object]:
        return []


class EmptySession:
    def __init__(self) -> None:
        self.scalar_statement: Any = None
        self.scalars_statement: Any = None

    def scalar(self, statement: Any) -> None:
        self.scalar_statement = statement
        return None

    def scalars(self, statement: Any) -> EmptyScalars:
        self.scalars_statement = statement
        return EmptyScalars()


class UploadDeleteSession(EmptySession):
    def __init__(self, attachment: Attachment | None, owner_result: object | None) -> None:
        super().__init__()
        self.attachment = attachment
        self.owner_result = owner_result
        self.deleted: object | None = None
        self.commits = 0

    def get(self, model: type[object], object_id: str) -> object | None:
        if model is Attachment and self.attachment and self.attachment.id == object_id:
            return self.attachment
        return None

    def scalar(self, statement: Any) -> object | None:
        self.scalar_statement = statement
        return self.owner_result

    def delete(self, instance: object) -> None:
        self.deleted = instance

    def commit(self) -> None:
        self.commits += 1


def override_user() -> User:
    return User(id="user-1", email="user@example.com", hashed_password="hashed")


def override_session() -> Iterator[EmptySession]:
    yield EmptySession()


def test_protected_route_requires_authentication() -> None:
    app.dependency_overrides[db_session] = override_session
    try:
        response = TestClient(app).get("/api/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token"


def test_account_route_hides_missing_or_cross_user_account() -> None:
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = override_session
    try:
        response = TestClient(app).get("/api/accounts/account-from-another-user")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"


def test_trade_route_hides_missing_or_cross_user_trade() -> None:
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = override_session
    try:
        response = TestClient(app).get("/api/trades/trade-from-another-user")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Trade not found"


def test_journal_route_hides_missing_or_cross_user_journal() -> None:
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = override_session
    try:
        response = TestClient(app).get("/api/journals/journal-from-another-user")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Journal not found"


def test_account_listing_uses_authenticated_user_scope() -> None:
    session = EmptySession()

    def scoped_session() -> Iterator[EmptySession]:
        yield session

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = scoped_session
    try:
        response = TestClient(app).get("/api/accounts")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []
    sql = str(session.scalars_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "accounts.user_id = 'user-1'" in sql


def test_import_csv_hides_missing_or_cross_user_account() -> None:
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = override_session
    try:
        response = TestClient(app).post(
            "/api/import/csv",
            params={"account_id": "account-from-another-user"},
            files={"file": ("trades.csv", b"symbol,price\nES,5000\n", "text/csv")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"


def test_import_history_uses_authenticated_user_account_scope() -> None:
    session = EmptySession()

    def scoped_session() -> Iterator[EmptySession]:
        yield session

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = scoped_session
    try:
        response = TestClient(app).get("/api/import/history")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == []
    sql = str(session.scalars_statement.compile(compile_kwargs={"literal_binds": True}))
    assert "accounts.user_id = 'user-1'" in sql


def test_upload_screenshot_rejects_unsupported_file_types() -> None:
    app.dependency_overrides[current_user] = override_user
    try:
        response = TestClient(app).post(
            "/api/uploads/screenshot",
            files={"file": ("payload.txt", b"not an image", "text/plain")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported screenshot type"


def test_upload_screenshot_rejects_path_like_file_names() -> None:
    app.dependency_overrides[current_user] = override_user
    try:
        response = TestClient(app).post(
            "/api/uploads/screenshot",
            files={"file": ("..\\payload.png", b"image", "image/png")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid file name"


def test_delete_upload_requires_existing_owned_attachment() -> None:
    session = UploadDeleteSession(attachment=None, owner_result=None)

    def scoped_session() -> Iterator[UploadDeleteSession]:
        yield session

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = scoped_session
    try:
        response = TestClient(app).delete("/api/uploads/missing-upload")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Upload not found"


def test_delete_upload_deletes_owned_trade_attachment() -> None:
    attachment = Attachment(id="upload-1", trade_id="trade-1", file_name="chart.png")
    session = UploadDeleteSession(attachment=attachment, owner_result=object())

    def scoped_session() -> Iterator[UploadDeleteSession]:
        yield session

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = scoped_session
    try:
        response = TestClient(app).delete("/api/uploads/upload-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert session.deleted is attachment
    assert session.commits == 1


def test_report_enqueue_uses_authenticated_user(monkeypatch) -> None:
    queued_user_ids: list[str] = []

    class FakeJob:
        queue = "reports"
        job_id = "job-1"

    def fake_enqueue(user_id: str) -> FakeJob:
        queued_user_ids.append(user_id)
        return FakeJob()

    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[db_session] = override_session
    monkeypatch.setattr(api_main, "enqueue_performance_report", fake_enqueue)
    try:
        response = TestClient(app).post("/api/reports/performance-pdf")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["jobId"] == "job-1"
    assert queued_user_ids == ["user-1"]
