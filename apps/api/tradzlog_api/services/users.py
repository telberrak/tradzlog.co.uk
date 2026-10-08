"""Sign-up and sign-in rules shared by the API and the web app, so both enforce the same policy."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradzlog_api.config import settings
from tradzlog_api.security import (
    generate_one_time_token,
    hash_one_time_token,
    hash_password,
    verify_password,
)
from tradzlog_db.models import AuthToken, AuthTokenPurpose, User

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
# Checked when the email is unknown, so a wrong email takes as long as a wrong password.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


class RegistrationClosed(Exception):
    """Sign-up is invite-only and no valid invite code was given."""


class EmailTaken(Exception):
    """An account with this email already exists."""


def normalise_email(email: str) -> str:
    return email.strip().lower()


def registration_allowed(invite_code: str | None) -> bool:
    if settings.registration_open:
        return True
    expected = settings.registration_invite_code
    # Closed with no code configured means nobody can sign up; compare in constant time.
    return bool(expected and invite_code and hmac.compare_digest(invite_code.strip().encode(), expected.encode()))


def create_auth_token(session: Session, user: User, purpose: AuthTokenPurpose, expires_in: timedelta) -> str:
    raw_token = generate_one_time_token()
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_one_time_token(raw_token),
            expires_at=datetime.now(UTC) + expires_in,
        )
    )
    return raw_token


def register_user(session: Session, email: str, password: str, name: str | None, invite_code: str | None) -> User:
    """Create a user (not committed). Raises RegistrationClosed or EmailTaken."""
    if not registration_allowed(invite_code):
        raise RegistrationClosed
    email = normalise_email(email)
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise EmailTaken
    user = User(email=email, name=(name or "").strip() or None, hashed_password=hash_password(password))
    session.add(user)
    session.flush()
    create_auth_token(session, user, AuthTokenPurpose.EMAIL_VERIFICATION, timedelta(hours=24))
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == normalise_email(email)))
    if user is None or user.hashed_password is None:
        verify_password(password, _DUMMY_HASH)
        return None
    return user if verify_password(password, user.hashed_password) else None
