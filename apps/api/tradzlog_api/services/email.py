"""Transactional email: welcome, password reset, password changed, account deleted.

``EMAIL_BACKEND`` picks how mail leaves:

- ``log`` (default; local, tests, and production until SES is set up): nothing is sent; the message
  is logged without its body and kept in ``OUTBOX`` so tests can read it.
- ``ses``: Amazon SES (v2 API) in ``AWS_REGION``, with the EC2 instance role's credentials. Sent by
  the RQ worker so a slow or failing SES never delays a page; if the queue is unreachable, sent inline.

Every message has a plain-text body and a simple HTML body, comes from ``EMAIL_FROM`` and replies go
to ``SUPPORT_EMAIL``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from functools import lru_cache
from html import escape
from typing import Any

from tradzlog_api.config import settings

logger = logging.getLogger("tradzlog.email")

OUTBOX: list[Message] = []  # "log" backend only: what would have been sent (tests read it)
OUTBOX_LIMIT = 100


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str


# --------------------------------------------------------------------- templates


def layout(heading: str, paragraphs: list[str], button: tuple[str, str] | None = None) -> str:
    """Minimal, client-safe HTML: inline styles, one column, no images or tracking."""
    body = "".join(f'<p style="margin:0 0 14px;line-height:1.6">{part}</p>' for part in paragraphs)
    cta = (
        f'<p style="margin:22px 0"><a href="{escape(button[1])}" style="background:#2563eb;color:#ffffff;'
        f'text-decoration:none;padding:11px 20px;border-radius:8px;font-weight:600;display:inline-block">{escape(button[0])}</a></p>'
        if button else ""
    )
    return f"""<!doctype html><html><body style="margin:0;background:#f4f6fa;font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#111827">
<div style="max-width:560px;margin:0 auto;padding:28px 20px">
<div style="font-weight:700;letter-spacing:.06em;color:#2563eb;margin-bottom:18px">TRADZLOG</div>
<div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:26px">
<h1 style="font-size:20px;margin:0 0 16px">{escape(heading)}</h1>{body}{cta}</div>
<p style="font-size:12px;color:#6b7280;line-height:1.5;margin:16px 4px">You are receiving this because of activity on your TradzLog
account. Questions? Reply to this email or write to {escape(settings.support_email)}.</p>
</div></body></html>"""


def greeting(name: str | None) -> str:
    return f"Hi {name.strip()}," if name and name.strip() else "Hi,"


def welcome(to: str, name: str | None) -> Message:
    url = f"{settings.public_url}/dashboard"
    text = (f"{greeting(name)}\n\nWelcome to TradzLog. Your journal is ready: add a trading account, import your broker's "
            f"history or log a trade, and your dashboard fills in.\n\nOpen your dashboard: {url}\n\n— TradzLog")
    html = layout("Welcome to TradzLog", [escape(greeting(name)),
                  "Your journal is ready: add a trading account, import your broker's history or log a trade, and your dashboard fills in."],
                  ("Open your dashboard", url))
    return Message(to, "Welcome to TradzLog", text, html)


def password_reset(to: str, name: str | None, link: str, minutes: int) -> Message:
    text = (f"{greeting(name)}\n\nSomeone (hopefully you) asked to reset your TradzLog password. Use this link within "
            f"{minutes} minutes:\n\n{link}\n\nIf you didn't ask, ignore this email: your password stays the same.\n\n— TradzLog")
    html = layout("Reset your password", [escape(greeting(name)),
                  f"Someone (hopefully you) asked to reset your TradzLog password. The link works once, for {minutes} minutes.",
                  "If you didn't ask, ignore this email: your password stays the same."], ("Choose a new password", link))
    return Message(to, "Reset your TradzLog password", text, html)


def password_changed(to: str, name: str | None) -> Message:
    url = f"{settings.public_url}/forgot-password"
    text = (f"{greeting(name)}\n\nYour TradzLog password was just changed, and other devices were signed out.\n\n"
            f"If this wasn't you, reset your password now: {url}\nand tell us at {settings.support_email}.\n\n— TradzLog")
    html = layout("Your password was changed", [escape(greeting(name)),
                  "Your TradzLog password was just changed, and other devices were signed out.",
                  f"If this wasn't you, reset your password now and tell us at {escape(settings.support_email)}."],
                  ("Reset password", url))
    return Message(to, "Your TradzLog password was changed", text, html)


def account_deleted(to: str, name: str | None) -> Message:
    text = (f"{greeting(name)}\n\nYour TradzLog account has been deleted, with your trades, journal and screenshots. "
            f"Copies in our rolling backups expire within 30 days.\n\nIf you didn't do this, write to {settings.support_email} "
            f"straight away.\n\n— TradzLog")
    html = layout("Your account has been deleted", [escape(greeting(name)),
                  "Your TradzLog account has been deleted, with your trades, journal and screenshots. "
                  "Copies in our rolling backups expire within 30 days.",
                  f"If you didn't do this, write to {escape(settings.support_email)} straight away."])
    return Message(to, "Your TradzLog account has been deleted", text, html)


# --------------------------------------------------------------------- sending


@lru_cache(maxsize=1)
def ses_client() -> Any:
    import boto3

    return boto3.client("sesv2", region_name=settings.ses_region)


def deliver(message: Message) -> None:
    """Send now, with the configured backend."""
    if settings.email_backend == "ses":
        ses_client().send_email(
            FromEmailAddress=settings.email_from,
            Destination={"ToAddresses": [message.to]},
            ReplyToAddresses=[settings.support_email],
            Content={"Simple": {
                "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": message.text, "Charset": "UTF-8"}, "Html": {"Data": message.html, "Charset": "UTF-8"}},
            }},
        )
        logger.info("email sent via SES: %s", message.subject)
        return
    OUTBOX.append(message)
    del OUTBOX[:-OUTBOX_LIMIT]
    logger.info("email not sent (EMAIL_BACKEND=%s): %s", settings.email_backend, message.subject)


def deliver_job(payload: dict[str, str]) -> None:
    """RQ entry point."""
    deliver(Message(**payload))


def send(message: Message) -> None:
    """Send in the background with SES; never raises, so an email problem can't break the page that sent it."""
    try:
        if settings.email_backend == "ses":
            from tradzlog_api.services.jobs import enqueue_job

            try:
                enqueue_job("default", deliver_job, asdict(message))
                return
            except Exception:
                logger.warning("email queue unavailable; sending inline", exc_info=True)
        deliver(message)
    except Exception:
        logger.exception("email failed: %s", message.subject)
