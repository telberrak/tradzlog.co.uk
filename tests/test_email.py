"""Transactional email: templates, the log backend, SES delivery and the never-raise guarantee."""

from __future__ import annotations

from typing import Any

import pytest

from tradzlog_api.config import settings
from tradzlog_api.services import email


class FakeSES:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_email(self, **kwargs: Any) -> dict[str, str]:
        self.sent.append(kwargs)
        return {"MessageId": "1"}


@pytest.fixture
def outbox():
    email.OUTBOX.clear()
    yield email.OUTBOX
    email.OUTBOX.clear()


def test_templates_have_text_and_html_and_escape_names(monkeypatch) -> None:
    monkeypatch.setattr(settings, "public_url", "https://tradzlog.example")
    message = email.password_reset("a@example.com", "<b>Al</b>", "https://tradzlog.example/reset-password?token=abc", 60)
    assert "https://tradzlog.example/reset-password?token=abc" in message.text and "60 minutes" in message.text
    assert "&lt;b&gt;Al&lt;/b&gt;" in message.html and "<b>Al</b>" not in message.html
    assert "https://tradzlog.example/dashboard" in email.welcome("a@example.com", None).html
    assert "deleted" in email.account_deleted("a@example.com", "Al").subject


def test_log_backend_sends_nothing_and_keeps_the_message(outbox, monkeypatch) -> None:
    monkeypatch.setattr(settings, "email_backend", "log")
    email.send(email.welcome("a@example.com", "Al"))
    assert [message.subject for message in outbox] == ["Welcome to TradzLog"]


def test_ses_delivery(monkeypatch) -> None:
    fake = FakeSES()
    monkeypatch.setattr(settings, "email_backend", "ses")
    monkeypatch.setattr(settings, "email_from", "TradzLog <no-reply@tradzlog.example>")
    monkeypatch.setattr(settings, "support_email", "help@tradzlog.example")
    monkeypatch.setattr(email, "ses_client", lambda: fake)
    email.deliver(email.password_changed("a@example.com", "Al"))
    sent = fake.sent[0]
    assert sent["FromEmailAddress"] == "TradzLog <no-reply@tradzlog.example>"
    assert sent["Destination"] == {"ToAddresses": ["a@example.com"]} and sent["ReplyToAddresses"] == ["help@tradzlog.example"]
    body = sent["Content"]["Simple"]["Body"]
    assert "password was just changed" in body["Text"]["Data"] and body["Html"]["Data"].startswith("<!doctype html>")


def test_ses_goes_through_the_queue_and_falls_back_inline(monkeypatch) -> None:
    import tradzlog_api.services.jobs as jobs

    fake = FakeSES()
    queued: list[tuple] = []
    monkeypatch.setattr(settings, "email_backend", "ses")
    monkeypatch.setattr(email, "ses_client", lambda: fake)
    monkeypatch.setattr(jobs, "enqueue_job", lambda *args: queued.append(args))
    email.send(email.welcome("a@example.com", "Al"))
    assert queued[0][0] == "default" and queued[0][1] is email.deliver_job and not fake.sent
    email.deliver_job(queued[0][2])
    assert fake.sent[0]["Destination"] == {"ToAddresses": ["a@example.com"]}

    def broken(*args):
        raise ConnectionError("redis down")

    monkeypatch.setattr(jobs, "enqueue_job", broken)
    email.send(email.welcome("b@example.com", "Bo"))
    assert fake.sent[1]["Destination"] == {"ToAddresses": ["b@example.com"]}


def test_send_never_raises(monkeypatch) -> None:
    class Down:
        def send_email(self, **kwargs: Any) -> None:
            raise RuntimeError("SES unavailable")

    import tradzlog_api.services.jobs as jobs

    monkeypatch.setattr(settings, "email_backend", "ses")
    monkeypatch.setattr(email, "ses_client", lambda: Down())
    monkeypatch.setattr(jobs, "enqueue_job", lambda *args: (_ for _ in ()).throw(ConnectionError()))
    email.send(email.welcome("a@example.com", "Al"))  # logged, not raised
