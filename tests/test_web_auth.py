from types import SimpleNamespace

import pytest
from fastapi import Response

from tradzlog_web import auth
from tradzlog_web.context import CURRENT_CSRF


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/trades?status_filter=OPEN", "/trades?status_filter=OPEN"),
        ("/analytics", "/analytics"),
        (None, "/dashboard"),
        ("", "/dashboard"),
        ("//evil.example", "/dashboard"),
        ("https://evil.example/x", "/dashboard"),
        ("/\evil.example", "/dashboard"),
        ("javascript:alert(1)", "/dashboard"),
    ],
)
def test_safe_next_only_allows_same_site_paths(target, expected) -> None:
    assert auth.safe_next(target) == expected


def request_with(headers: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(headers={key.lower(): value for key, value in headers.items()})


def test_same_origin_checks_origin_then_referer_against_host() -> None:
    assert auth.same_origin(request_with({"Host": "tradzlog.com", "Origin": "https://tradzlog.com"}))
    assert auth.same_origin(request_with({"Host": "tradzlog.com", "Referer": "https://tradzlog.com/login?next=/"}))
    assert not auth.same_origin(request_with({"Host": "tradzlog.com", "Origin": "https://evil.example"}))
    assert not auth.same_origin(request_with({"Host": "tradzlog.com", "Origin": "https://tradzlog.com.evil.example"}))
    assert not auth.same_origin(request_with({"Host": "tradzlog.com", "Origin": "null"}))
    assert not auth.same_origin(request_with({"Host": "tradzlog.com"}))


def test_csrf_field_is_added_to_post_forms_only() -> None:
    token = CURRENT_CSRF.set("abc123")
    try:
        html = auth.inject_csrf('<form method="post" action="/x"><b></b></form><form method="get"></form><FORM class="a" METHOD=POST>')
    finally:
        CURRENT_CSRF.reset(token)
    assert html.count('name="csrf_token" value="abc123"') == 2
    assert '<form method="get"></form>' in html


def test_csrf_token_depends_on_the_session() -> None:
    assert auth.csrf_for("a" * 64) != auth.csrf_for("b" * 64)
    assert len(auth.csrf_for("a" * 64)) == 64


def test_production_session_cookie_is_secure_and_host_only(monkeypatch) -> None:
    monkeypatch.setattr(auth.settings, "app_env", "production")
    assert auth.cookie_name() == "__Host-tz_session"

    class FakeDb:
        def __init__(self) -> None:
            self.added = []

        def execute(self, statement) -> None:
            del statement

        def add(self, row) -> None:
            self.added.append(row)

    db, response = FakeDb(), Response()
    request = SimpleNamespace(headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130"}, client=SimpleNamespace(host="203.0.113.9"))
    auth.start_session(db, SimpleNamespace(id="user-1"), request, response)
    cookie = response.headers["set-cookie"]
    raw = cookie.split(";")[0].split("=", 1)[1]
    assert cookie.startswith("__Host-tz_session=")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=2592000"):
        assert flag in cookie
    assert "Domain" not in cookie
    (row,) = db.added
    assert row.token_hash == auth.hash_token(raw) and raw not in row.token_hash  # only the hash is stored
    assert row.ip_address == "203.0.113.9"
