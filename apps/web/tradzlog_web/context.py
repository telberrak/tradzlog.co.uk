"""Per-request values set by the auth dependency and read by handlers and page rendering."""

from __future__ import annotations

from contextvars import ContextVar

# The signed-in user's id for this request, or None for a visitor.
CURRENT_USER_ID: ContextVar[str | None] = ContextVar("tradzlog_current_user_id", default=None)
# The CSRF token every POST form on the page must send back (None when signed out).
CURRENT_CSRF: ContextVar[str | None] = ContextVar("tradzlog_current_csrf", default=None)
