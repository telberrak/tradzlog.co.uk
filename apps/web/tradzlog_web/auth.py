"""Web sign-in: server-side sessions in a secure cookie, and CSRF protection for every form.

- The cookie holds a random token; the database stores only its SHA-256 (``WebSession``).
- Sessions last 30 days from the last visit, refreshed at most hourly.
- Every POST must carry the session's CSRF token (added to forms automatically by ``inject_csrf``),
  except sign-in and sign-up, which have no session yet and instead must come from this site
  (Origin or Referer header).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, datetime, timedelta
from html import escape
from secrets import token_urlsafe
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from tradzlog_api.config import settings
from tradzlog_db.models import User, WebSession
from tradzlog_db.session import SessionLocal
from tradzlog_web.context import CURRENT_CSRF, CURRENT_USER_ID

SESSION_LIFETIME = timedelta(days=30)
REFRESH_AFTER = timedelta(hours=1)
CSRF_FIELD = "csrf_token"
# Forms posted before a session exists; protected by an origin check instead of a token.
SESSIONLESS_POSTS = {"/login", "/signup"}


class LoginRequired(Exception):
    """Raised by ``current_user`` for visitors; turned into a redirect to the sign-in page."""


def cookie_secure() -> bool:
    return settings.app_env not in {"local", "test"}


def cookie_name() -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure, host-only and Path=/.
    return "__Host-tz_session" if cookie_secure() else "tz_session"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def csrf_for(token_hash: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"csrf:{token_hash}".encode(), hashlib.sha256).hexdigest()


def client_ip(request: Request) -> str | None:
    return request.client.host[:64] if request.client else None


# --------------------------------------------------------------------- sessions


def start_session(db, user: User, request: Request, response: Response) -> None:
    """Create a session for ``user`` and set its cookie (caller commits)."""
    now = datetime.now(UTC)
    db.execute(delete(WebSession).where(WebSession.user_id == user.id, WebSession.expires_at <= now))
    raw = token_urlsafe(32)
    db.add(
        WebSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            expires_at=now + SESSION_LIFETIME,
            last_seen_at=now,
            user_agent=(request.headers.get("user-agent") or "")[:255] or None,
            ip_address=client_ip(request),
        )
    )
    response.set_cookie(
        cookie_name(),
        raw,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )


def end_session(db, request: Request, response: Response) -> None:
    raw = request.cookies.get(cookie_name())
    if raw:
        db.execute(delete(WebSession).where(WebSession.token_hash == hash_token(raw)))
    response.delete_cookie(cookie_name(), path="/", secure=cookie_secure(), httponly=True, samesite="lax")


def lookup_session(raw: str) -> tuple[str, str] | None:
    """(user id, token hash) for a live session, sliding its expiry; None if unknown or expired."""
    token_hash = hash_token(raw)
    now = datetime.now(UTC)
    db = SessionLocal()
    try:
        row = db.scalar(select(WebSession).where(WebSession.token_hash == token_hash))
        if row is None or row.expires_at <= now:
            return None
        if now - row.last_seen_at >= REFRESH_AFTER:
            row.last_seen_at = now
            row.expires_at = now + SESSION_LIFETIME
            db.commit()
        return row.user_id, token_hash
    finally:
        db.close()


# --------------------------------------------------------------------- request guard


def same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source or source == "null":
        return False
    return urlsplit(source).netloc.lower() == (request.headers.get("host") or "").lower()


async def web_auth(request: Request) -> None:
    """App-wide dependency: identify the visitor and enforce CSRF on every POST."""
    CURRENT_USER_ID.set(None)
    CURRENT_CSRF.set(None)
    request.state.session_token_hash = None
    raw = request.cookies.get(cookie_name())
    if raw:
        found = await run_in_threadpool(lookup_session, raw)
        if found is not None:
            user_id, token_hash = found
            CURRENT_USER_ID.set(user_id)
            CURRENT_CSRF.set(csrf_for(token_hash))
            request.state.session_token_hash = token_hash

    if request.method != "POST":
        return
    if request.url.path in SESSIONLESS_POSTS:
        if not same_origin(request):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Form must be submitted from this site")
        return
    if request.state.session_token_hash is None:
        raise LoginRequired
    form = await request.form()
    sent = form.get(CSRF_FIELD)
    if not isinstance(sent, str) or not hmac.compare_digest(sent, CURRENT_CSRF.get() or ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This form has expired. Reload the page and try again.")


def current_user(db) -> User:
    """The signed-in user, loaded with ``db``. Raises LoginRequired for visitors."""
    user_id = CURRENT_USER_ID.get()
    if user_id is None:
        raise LoginRequired
    user = db.scalar(select(User).where(User.id == user_id))
    if user is None:
        raise LoginRequired
    return user


# --------------------------------------------------------------------- helpers


_POST_FORM = re.compile(r"(<form\b[^>]*\bmethod=[\"']?post[\"']?[^>]*>)", re.IGNORECASE)


def inject_csrf(html: str) -> str:
    """Add the CSRF hidden field to every POST form in a rendered page."""
    token = CURRENT_CSRF.get()
    if not token:
        return html
    field = f'<input type="hidden" name="{CSRF_FIELD}" value="{escape(token)}" />'
    return _POST_FORM.sub(lambda match: match.group(1) + field, html)


def safe_next(target: str | None, default: str = "/dashboard") -> str:
    """Only same-site paths: refuse absolute URLs, //host and backslash tricks (open redirects)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return default
    return target
