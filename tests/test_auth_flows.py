from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

import tradzlog_api.main as api_main
from tradzlog_api.main import (
    consume_auth_token,
    consume_magic_link,
    forgot_password,
    register,
    request_magic_link,
    reset_password,
    verify_email,
)
from tradzlog_api.security import hash_one_time_token
from tradzlog_db.models import AuthToken, AuthTokenPurpose, User
from tradzlog_types.schemas import (
    ForgotPasswordRequest,
    MagicLinkRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenExchangeRequest,
)


class FakeAuthSession:
    def __init__(self, scalar_results: list[object | None] | None = None) -> None:
        self.scalar_results = scalar_results or []
        self.users: dict[str, User] = {}
        self.added: list[object] = []
        self.commits = 0

    def scalar(self, statement: Any) -> object | None:
        del statement
        return self.scalar_results.pop(0) if self.scalar_results else None

    def get(self, model: type[object], object_id: str) -> object | None:
        if model is User:
            return self.users.get(object_id)
        return None

    def add(self, instance: object) -> None:
        self.added.append(instance)
        if isinstance(instance, User) and instance.id:
            self.users[instance.id] = instance

    def flush(self) -> None:
        for instance in self.added:
            if isinstance(instance, User) and not instance.id:
                instance.id = f"user-{len(self.users) + 1}"
                self.users[instance.id] = instance

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, instance: object, attribute_names: list[str] | None = None) -> None:
        del instance, attribute_names


@pytest.fixture(autouse=True)
def stable_password_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, "hash_password", lambda password: f"hashed:{password}")


def auth_tokens(session: FakeAuthSession) -> list[AuthToken]:
    return [instance for instance in session.added if isinstance(instance, AuthToken)]


def token_for(raw_token: str, purpose: AuthTokenPurpose, user_id: str = "user-1") -> AuthToken:
    return AuthToken(
        user_id=user_id,
        purpose=purpose,
        token_hash=hash_one_time_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )


def test_register_creates_email_verification_token() -> None:
    session = FakeAuthSession()

    user = register(
        RegisterRequest(email="Trader@Example.com", password="password123", name="Trader"),
        session,  # type: ignore[arg-type]
    )

    created_tokens = auth_tokens(session)
    assert user.id == "user-1"
    assert user.email == "trader@example.com"
    assert len(created_tokens) == 1
    assert created_tokens[0].user_id == "user-1"
    assert created_tokens[0].purpose == AuthTokenPurpose.EMAIL_VERIFICATION
    assert session.commits == 1


def test_verify_email_consumes_token_once() -> None:
    raw_token = "v" * 40
    user = User(id="user-1", email="trader@example.com", hashed_password="hashed")
    auth_token = token_for(raw_token, AuthTokenPurpose.EMAIL_VERIFICATION)
    session = FakeAuthSession([auth_token])
    session.users[user.id] = user

    response = verify_email(TokenExchangeRequest(token=raw_token), session)  # type: ignore[arg-type]

    assert response.token_type == "bearer"
    assert user.email_verified_at is not None
    assert auth_token.used_at is not None
    assert session.commits == 1

    session.scalar_results.append(auth_token)
    with pytest.raises(HTTPException) as exc:
        consume_auth_token(session, raw_token, AuthTokenPurpose.EMAIL_VERIFICATION)  # type: ignore[arg-type]
    assert exc.value.status_code == 400


def test_magic_link_token_can_only_be_consumed_once() -> None:
    user = User(id="user-1", email="trader@example.com", hashed_password="hashed")
    session = FakeAuthSession([user])
    session.users[user.id] = user

    response = request_magic_link(
        MagicLinkRequest(email="trader@example.com"),
        session,  # type: ignore[arg-type]
    )
    raw_token = str(response["devToken"])
    auth_token = auth_tokens(session)[0]

    session.scalar_results.append(auth_token)
    token_response = consume_magic_link(
        TokenExchangeRequest(token=raw_token),
        session,  # type: ignore[arg-type]
    )

    assert token_response.token_type == "bearer"
    assert auth_token.used_at is not None

    session.scalar_results.append(auth_token)
    with pytest.raises(HTTPException) as exc:
        consume_magic_link(TokenExchangeRequest(token=raw_token), session)  # type: ignore[arg-type]
    assert exc.value.status_code == 400


def test_password_reset_token_updates_password_and_is_single_use() -> None:
    user = User(id="user-1", email="trader@example.com", hashed_password="old-hash")
    session = FakeAuthSession([user])
    session.users[user.id] = user

    response = forgot_password(
        ForgotPasswordRequest(email="trader@example.com"),
        session,  # type: ignore[arg-type]
    )
    raw_token = str(response["devToken"])
    auth_token = auth_tokens(session)[0]

    session.scalar_results.append(auth_token)
    reset_password(
        ResetPasswordRequest(token=raw_token, password="new-password-123"),
        session,  # type: ignore[arg-type]
    )

    assert user.hashed_password == "hashed:new-password-123"
    assert auth_token.used_at is not None

    session.scalar_results.append(auth_token)
    with pytest.raises(HTTPException) as exc:
        reset_password(
            ResetPasswordRequest(token=raw_token, password="another-password-123"),
            session,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400


def test_one_time_tokens_are_not_echoed_outside_local(monkeypatch) -> None:
    monkeypatch.setattr(api_main.settings, "app_env", "production")
    user = User(id="user-1", email="trader@example.com", hashed_password="hashed")
    session = FakeAuthSession([user, user])
    session.users[user.id] = user

    reset = forgot_password(ForgotPasswordRequest(email="trader@example.com"), session)  # type: ignore[arg-type]
    magic = request_magic_link(MagicLinkRequest(email="trader@example.com"), session)  # type: ignore[arg-type]

    assert "devToken" not in reset
    assert "devToken" not in magic
    assert len(auth_tokens(session)) == 2


@pytest.mark.parametrize(
    ("open_", "code", "given", "allowed"),
    [
        (True, None, None, True),
        (False, "beta-2026", "beta-2026", True),
        (False, "beta-2026", "wrong", False),
        (False, "beta-2026", None, False),
        (False, None, "anything", False),  # closed with no code configured: nobody signs up
    ],
)
def test_registration_switch(monkeypatch, open_: bool, code: str | None, given: str | None, allowed: bool) -> None:
    monkeypatch.setattr(api_main.settings, "registration_open", open_)
    monkeypatch.setattr(api_main.settings, "registration_invite_code", code)
    session = FakeAuthSession()
    payload = RegisterRequest(email="new@example.com", password="password-123", invite_code=given)
    if allowed:
        assert register(payload, session).email == "new@example.com"  # type: ignore[arg-type]
    else:
        with pytest.raises(HTTPException) as exc:
            register(payload, session)  # type: ignore[arg-type]
        assert exc.value.status_code == 403
        assert session.added == []
