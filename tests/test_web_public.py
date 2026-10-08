"""Public pages, error pages and feature switches; no database needed (visitors have no session)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import tradzlog_web.main as web_main
from tradzlog_api.config import settings
from tradzlog_web.public_routes import feature_disabled
from tradzlog_web.ui import RISK_NOTICE

PUBLIC_PAGES = ("/", "/features", "/pricing", "/security", "/legal/terms", "/legal/privacy", "/legal/cookies", "/legal/risk")


@pytest.fixture
def visitor():
    with TestClient(web_main.app, base_url="http://testserver", raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages_render_for_visitors(visitor, path: str) -> None:
    response = visitor.get(path, follow_redirects=False)
    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert RISK_NOTICE in response.text.replace("&#x27;", "'")
    assert 'href="/login"' in response.text and 'href="/signup"' in response.text


def test_landing_page_and_free_beta_pricing(visitor) -> None:
    assert "See what actually makes you money." in visitor.get("/").text
    assert "Free during the private beta" in visitor.get("/pricing").text


def test_legal_pages_name_the_support_mailbox(visitor, monkeypatch) -> None:
    monkeypatch.setattr(settings, "support_email", "help@example.test")
    for path in ("/legal/terms", "/legal/privacy", "/security"):
        assert 'href="mailto:help@example.test"' in visitor.get(path).text


def test_unknown_page_is_a_friendly_404(visitor) -> None:
    response = visitor.get("/no-such-page")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Page not found" in response.text


def test_refused_forms_explain_themselves(visitor) -> None:
    response = visitor.post("/login", data={"email": "a@example.com", "password": "x"})  # no Origin header
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "Form must be submitted from this site" in response.text


def test_unexpected_errors_show_a_500_page(visitor, monkeypatch) -> None:
    def broken() -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(web_main, "landing_page", broken)
    response = visitor.get("/")
    assert response.status_code == 500
    assert "Something went wrong" in response.text and "boom" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/community", "/community/mentor", "/share/abc", "/settings/billing"])
def test_switched_off_features_are_not_found(visitor, path: str) -> None:
    assert visitor.get(path, follow_redirects=False).status_code == 404


def test_feature_switches(monkeypatch) -> None:
    for path in ("/community", "/community/mentor/access", "/share/x", "/trades/t1/share", "/settings/billing/checkout"):
        assert feature_disabled(path)
    for path in ("/trades/t1", "/trades/new", "/settings/accounts", "/dashboard"):
        assert not feature_disabled(path)
    monkeypatch.setattr(settings, "feature_community", True)
    assert not feature_disabled("/trades/t1/share") and feature_disabled("/settings/billing")
    monkeypatch.setattr(settings, "feature_billing", True)
    assert not feature_disabled("/settings/billing/portal")


def test_coaching_pages_say_it_is_not_advice() -> None:
    from tradzlog_web.ui import shell

    assert "not financial advice" in shell("AI Coaching", "coaching", "<p>review</p>")
    assert "not financial advice" not in shell("Trades", "trades", "<p>list</p>")
